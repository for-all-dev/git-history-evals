/-
`ablate` CLI — the Lean ablator's command line. Takes any mix of `.lean` files
and directories (walked for `*.lean`) and emits one (challenge, solution) JSON
record per file, or runs the corpus self-test with `--check`.
-/

import Ablator

open Ablator

def usage : String :=
"Usage: ablate [OPTIONS] PATH...

  Ablate proofs/bodies in Lean source, replacing them with `sorry`.
  PATH... is any mix of .lean files and directories (walked for *.lean).
  Default: emit one indented JSON (challenge, solution) record per file.

  Modes:
    --check          run the corpus self-test (round-trip + delimitation)

  Difficulty (raw knobs override any --difficulty preset):
    --difficulty L   preset ladder L0 (easy) .. L4 (code+spec only)
    --min-depth N    ablate goals at nesting depth >= N (default: 1)
    --max-depth N    ablate goals at nesting depth <= N; N may be `inf`
    --leaves-only    only ablate goals whose body has no nested binding
    --min-size N / --max-size N   body line-count window (N may be `inf`)
    --min-centrality N / --max-centrality N   corpus fan-in window
    -p PROB          probability of ablating each selected body (default: 0.5)
    --all            ablate every selected body (-p 1.0)
    --count N        ablate exactly min(N, matching) bodies (excl. -p/--all)
    --by-centrality  with --count, pick the most-cited bodies

  Context shaping (challenge text only; ignored by --check):
    --truncate       drop everything after the last inserted `sorry`
    --shrink-context drop top-level decls after the last ablated one

  Other:
    -s SESSION       session/library label recorded in output (default: lean)
    -d DIR           strip DIR prefix from emitted file paths (repeatable)
    --repeat N       emit up to N deduplicated ablations per file (default: 1)
    --seed N         RNG seed (default: time-based)
    --text           output the ablated source instead of JSONL
    --compact        strict one-object-per-line JSONL (no indentation)
    -v               verbose: progress/summary on stderr
"

structure Opts where
  session      : String := "lean"
  seed         : Option UInt64 := none
  verbose      : Bool := false
  check        : Bool := false
  compact      : Bool := false
  textMode     : Bool := false
  difficulty   : Option String := none
  probOpt      : Option Float := none
  countOpt     : Option Nat := none
  minDepthOpt  : Option Int := none
  maxDepthOpt  : Option Int := none
  leavesOpt    : Option Bool := none
  minSizeOpt   : Option Int := none
  maxSizeOpt   : Option Int := none
  minCentOpt   : Option Int := none
  maxCentOpt   : Option Int := none
  byCentrality : Bool := false
  truncate     : Bool := false
  shrinkCtx    : Bool := false
  repeatN      : Nat := 1
  paths        : Array String := #[]
  stripDirs    : Array String := #[]

def parseDepth (s : String) : Except String Int :=
  if s == "inf" || s == "infinity" then .ok INF
  else match s.toInt? with
    | some n => .ok n
    | none => .error s!"bad number: {s}"

partial def parseArgs (args : List String) (o : Opts) : Except String Opts := do
  match args with
  | [] => .ok o
  | a :: rest =>
    let needArg (k : String → Opts) : Except String Opts :=
      match rest with
      | v :: tl => parseArgs tl (k v)
      | [] => .error s!"missing arg for {a}"
    match a with
    | "--check"          => parseArgs rest { o with check := true }
    | "--compact"        => parseArgs rest { o with compact := true }
    | "--text"           => parseArgs rest { o with textMode := true }
    | "-v"               => parseArgs rest { o with verbose := true }
    | "--all"            => parseArgs rest { o with probOpt := some 1.0 }
    | "--by-centrality"  => parseArgs rest { o with byCentrality := true }
    | "--truncate"       => parseArgs rest { o with truncate := true }
    | "--shrink-context" => parseArgs rest { o with shrinkCtx := true }
    | "--leaves-only"    => parseArgs rest { o with leavesOpt := some true }
    | "--difficulty"     => needArg fun v => { o with difficulty := some v }
    | "--min-depth"      => match rest with
                            | v :: tl => do let d ← parseDepth v; parseArgs tl { o with minDepthOpt := some d }
                            | [] => .error "missing arg for --min-depth"
    | "--max-depth"      => match rest with
                            | v :: tl => do let d ← parseDepth v; parseArgs tl { o with maxDepthOpt := some d }
                            | [] => .error "missing arg for --max-depth"
    | "--min-size"       => match rest with
                            | v :: tl => do let d ← parseDepth v; parseArgs tl { o with minSizeOpt := some d }
                            | [] => .error "missing arg for --min-size"
    | "--max-size"       => match rest with
                            | v :: tl => do let d ← parseDepth v; parseArgs tl { o with maxSizeOpt := some d }
                            | [] => .error "missing arg for --max-size"
    | "--min-centrality" => match rest with
                            | v :: tl => do let d ← parseDepth v; parseArgs tl { o with minCentOpt := some d }
                            | [] => .error "missing arg for --min-centrality"
    | "--max-centrality" => match rest with
                            | v :: tl => do let d ← parseDepth v; parseArgs tl { o with maxCentOpt := some d }
                            | [] => .error "missing arg for --max-centrality"
    | "-p"               => match rest with
                            | v :: tl => parseArgs tl { o with probOpt := some (stringToFloat v) }
                            | [] => .error "missing arg for -p"
    | "--count"          => match rest with
                            | v :: tl => match v.toNat? with
                              | some n => parseArgs tl { o with countOpt := some n }
                              | none => .error "bad --count"
                            | [] => .error "missing arg for --count"
    | "--repeat"         => match rest with
                            | v :: tl => match v.toNat? with
                              | some n => parseArgs tl { o with repeatN := n }
                              | none => .error "bad --repeat"
                            | [] => .error "missing arg for --repeat"
    | "--seed"           => match rest with
                            | v :: tl => match v.toNat? with
                              | some n => parseArgs tl { o with seed := some (UInt64.ofNat n) }
                              | none => .error "bad --seed"
                            | [] => .error "missing arg for --seed"
    | "-s"               => needArg fun v => { o with session := v }
    | "-d"               => needArg fun v => { o with stripDirs := o.stripDirs.push v }
    | _ =>
      if a.startsWith "-" && a != "-" then .error s!"Unknown option: {a}"
      else parseArgs rest { o with paths := o.paths.push a }
where
  /-- Parse a decimal like `0.5` to Float without `Float.ofScientific` gymnastics. -/
  stringToFloat (s : String) : Float :=
    match s.splitOn "." with
    | [whole] => (whole.toNat?.getD 0).toFloat
    | [whole, frac] =>
      let w := (whole.toNat?.getD 0).toFloat
      let f := (frac.toNat?.getD 0).toFloat / (Nat.pow 10 frac.length).toFloat
      w + f
    | _ => 0.5

/-- Recursively collect `*.lean` files from a path (file or directory). -/
partial def collectTheories (p : System.FilePath) : IO (Array System.FilePath) := do
  if ← p.isDir then
    let entries ← p.readDir
    let sorted := entries.qsort (fun a b => a.fileName < b.fileName)
    let mut out := #[]
    for e in sorted do
      out := out ++ (← collectTheories e.path)
    return out
  else if p.toString.endsWith ".lean" then
    return #[p]
  else
    return #[]

def fnv1a (s : String) : UInt64 := Id.run do
  let mut h : UInt64 := 0xcbf29ce484222325
  for b in s.toUTF8 do
    h := (h ^^^ b.toUInt64) * 0x100000001b3
  return h

/-- Strip the longest matching `-d` prefix from a path. -/
def displayPath (stripDirs : Array String) (p : String) : String := Id.run do
  let sorted := stripDirs.qsort (fun a b => a.length > b.length)
  for d in sorted do
    if p == d then return (p.splitOn "/").getLastD p
    let pref := d ++ "/"
    if p.startsWith pref then return p.drop pref.length
  return p

/-- Build the `Spec` from parsed options + optional preset. -/
def buildSpec (o : Opts) (preset : Option Preset) : Spec :=
  { prob := o.probOpt.getD (preset.map (·.prob) |>.getD 0.5)
    count := o.countOpt
    byCentrality := o.byCentrality
    minDepth := o.minDepthOpt.getD (preset.map (·.minDepth) |>.getD 1)
    maxDepth := o.maxDepthOpt.getD (preset.map (·.maxDepth) |>.getD 1)
    leavesOnly := o.leavesOpt.getD (preset.map (·.leavesOnly) |>.getD false)
    minSize := o.minSizeOpt.getD 0
    maxSize := o.maxSizeOpt.getD INF
    minCentrality := o.minCentOpt.getD 0
    maxCentrality := o.maxCentOpt.getD INF
    truncate := o.truncate
    shrinkContext := o.shrinkCtx }

structure Doc where
  path : System.FilePath
  display : String
  text : String
  toks : Array Token
  spans : Array Span

/-- The corpus self-test: lossless round-trip, prob-1 delimitation, and decl
    statement preservation. Returns `true` on all-clean. -/
def runCheck (docs : Array Doc) (spec : Spec) (centrality : String → Int) : IO Bool := do
  -- disable count / context shaping (validate the ablation itself)
  let baseSpec := { spec with count := none, truncate := false, shrinkContext := false }
  let mut nFiles := 0
  let mut nGoals : Int := 0
  let mut nAblated : Int := 0
  let mut roundtripFail : Array String := #[]
  let mut delimitFail : Array String := #[]
  let mut reparseFail : Array String := #[]
  for d in docs do
    let name := d.path.toString
    let id := ablate d.toks { baseSpec with prob := 0.0 } (Rng.mk 0) centrality
    if id.text != d.text then roundtripFail := roundtripFail.push name
    let all := ablate d.toks { baseSpec with prob := 1.0 } (Rng.mk 0) centrality
    nFiles := nFiles + 1
    nGoals := nGoals + all.total
    nAblated := nAblated + all.ablated
    if all.ablated != all.total then delimitFail := delimitFail.push name
    let declsBefore := (parseSpans (tokenize d.text)).filter (·.isDecl) |>.size
    let declsAfter := (parseSpans (tokenize all.text)).filter (·.isDecl) |>.size
    if declsBefore != declsAfter then reparseFail := reparseFail.push name
  IO.println "\n================ ablation self-test ================"
  IO.println s!"theories checked     : {nFiles}"
  IO.println s!"in-range goals       : {nGoals}"
  let pct := if nGoals > 0 then 100.0 * nAblated.toNat.toFloat / nGoals.toNat.toFloat else 0.0
  IO.println s!"cleanly ablated      : {nAblated} ({pct}%)"
  IO.println s!"round-trip failures  : {roundtripFail.size}"
  IO.println s!"delimitation misses  : {delimitFail.size}"
  IO.println s!"re-parse mismatches  : {reparseFail.size}"
  for (label, xs) in [("round-trip failures", roundtripFail),
                      ("delimitation misses", delimitFail),
                      ("re-parse mismatches", reparseFail)] do
    if !xs.isEmpty then
      IO.println s!"\n-- {label} ({xs.size}), first 10:"
      for x in xs.toList.take 10 do IO.println s!"   {x}"
  let ok := roundtripFail.isEmpty && reparseFail.isEmpty
  IO.println s!"\nRESULT: {if ok then "OK" else "FAILURES PRESENT"}"
  return ok

def golden : UInt64 := 0x9E3779B97F4A7C15

def main (args : List String) : IO UInt32 := do
  if args == ["-h"] || args == ["--help"] then
    IO.print usage
    return 0
  let o ← match parseArgs args {} with
    | .ok o => pure o
    | .error e => do IO.eprintln e; IO.eprintln usage; return 2
  let preset ← match o.difficulty with
    | some d => match presetOf d with
      | some p => pure (some p)
      | none => do IO.eprintln s!"Unknown --difficulty {d} (expected L0..L{ladder.size - 1})"; return 2
    | none => pure none
  if o.countOpt.isSome && o.probOpt.isSome then
    IO.eprintln "--count cannot be combined with -p / --all"; return 2
  if o.byCentrality && o.countOpt.isNone then
    IO.eprintln "--by-centrality only applies with --count"; return 2
  let spec := buildSpec o preset
  if spec.minDepth < 1 then IO.eprintln "--min-depth must be >= 1"; return 2
  if o.paths.isEmpty then IO.eprintln usage; return 2

  let seedBase ← match o.seed with
    | some s => pure s
    | none => do pure (UInt64.ofNat (← IO.monoNanosNow))

  -- gather theories, tokenize once
  let mut files : Array System.FilePath := #[]
  for p in o.paths do
    files := files ++ (← collectTheories (System.FilePath.mk p))
  let mut docs : Array Doc := #[]
  for f in files do
    let text ← IO.FS.readFile f
    let toks := tokenize text
    docs := docs.push { path := f, display := displayPath o.stripDirs f.toString,
                        text := text, toks := toks, spans := parseSpans toks }
  if o.verbose then
    IO.eprintln s!"[session {o.session}: {docs.size} theories]"

  -- corpus fan-in (always for JSONL emit; otherwise only when filtered)
  let needCent := spec.usesCentrality || (!o.check && !o.textMode)
  let centrality : String → Int ←
    if needCent then do
      let fan := fanIn (docs.map (fun d => (d.toks, d.spans)))
      pure (fun name => fan.getD name 0)
    else pure (fun _ => 0)

  if o.check then
    let ok ← runCheck docs spec centrality
    return (if ok then 0 else 1)

  let nRepeat := Nat.max 1 o.repeatN
  let mut emitted := 0
  for d in docs do
    let mut seen : Std.HashSet String := {}
    let mut produced := 0
    for k in [0:nRepeat] do
      let pf := fnv1a d.display
      let rng := Rng.mk (seedBase ^^^ pf ^^^ (UInt64.ofNat k * golden))
      let result := ablate d.toks spec rng centrality
      if !seen.contains result.text then
        seen := seen.insert result.text
        if o.textMode then
          IO.print result.text
        else
          let variant := if nRepeat > 1 then some produced else none
          let obj := Ablator.record d.display o.session spec (Int.ofNat seedBase.toNat)
                       variant o.difficulty d.text result
          IO.println (if o.compact then obj.compact else obj.pretty)
        produced := produced + 1
        emitted := emitted + 1
  if o.verbose then
    IO.eprintln s!"[emitted {emitted} {if o.textMode then "theories" else "records"}]"
  return 0
