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
    --check-build    compile-test each ablation with `lake env lean` (challenge +
                     solution); only the file itself is built (deps must already
                     be compiled), so --shrink can't break it

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

  Lemma deletion (instead of per-proof ablation):
    --delete-lemmas     delete eligible used lemmas + ablate their users. With
                        --count N, deletions are drawn at random weighted by in-file
                        user count until >= N ablations result (seed-driven; popular
                        lemmas favoured, the tail still reachable). No prover needed.
    --delete-lemmas-uniform
                        like --delete-lemmas but draw deletions uniformly.
    --delete-lemmas-leaves
                        like --delete-lemmas; leaf-level holing is deferred to the
                        heavyweight semantic ablator, so this falls back to whole-proof.
    --aggressively-delete-lemmas
                        as above but relaxes guards and validates each challenge
                        with `lake env lean` (--check-build), dropping failures.
    --corollary-delete-lemmas[ N]
                        like --delete-lemmas but restrict deletions to one random
                        theorem's (a 'corollary') transitive in-file dependency closure
                        (fan-in weighted; re-picks a corollary only when the closure
                        runs dry). Variants: -uniform, -leaves.
    --corollary-delete-lemmas-all [N]
    --corollary-delete-lemmas-leaves-all [N]
                        walk the file and emit ONE ablation per eligible corollary
                        (each deletes N ancestor lemmas — default 1 — from that
                        corollary's closure + holes their users; -leaves-all holes
                        only leaf steps). Maximises coverage — ignores --repeat.

  Context shaping (ignored by --check):
    --truncate          drop challenge text after the last inserted `sorry`
    --shrink-challenge  drop challenge top-level decls after the N-th hole (--count)
    --shrink-solution   same, for the solution
    --shrink-challenge-minimal / --shrink-solution-minimal
                        keep only the N holes + their dependency closure (drop all
                        unrelated decls); solution restores the deleted lemma + deps

  Other:
    -s SESSION       session/library label recorded in output (default: lean)
    -d DIR           strip DIR prefix from emitted file paths (repeatable)
    --repo URL       record this git remote as provenance (default: auto-detect)
    --revision SHA   record this commit as provenance (default: auto-detect HEAD)
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
  checkBuild   : Bool := false
  deleteLemmas : Bool := false
  deleteCount  : Option Nat := none
  deleteUniform : Bool := false
  deleteLeaves : Bool := false
  aggressive   : Bool := false
  corollary    : Bool := false
  corollaryAll : Bool := false
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
  shrinkChallenge : Bool := false
  shrinkSolution  : Bool := false
  shrinkChallengeMinimal : Bool := false
  shrinkSolutionMinimal  : Bool := false
  repeatN      : Nat := 1
  paths        : Array String := #[]
  stripDirs    : Array String := #[]
  repoOpt      : Option String := none
  revisionOpt  : Option String := none

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
    -- a --delete-lemmas* flag, optionally followed by N (the number of lemmas)
    let delLemmas (k : Opts → Opts) : Except String Opts :=
      match rest with
      | v :: tl => match v.toNat? with
        | some c => parseArgs tl (k { o with deleteCount := some c })
        | none => parseArgs rest (k o)
      | [] => parseArgs rest (k o)
    match a with
    | "--check"          => parseArgs rest { o with check := true }
    | "--check-build"    => parseArgs rest { o with checkBuild := true }
    | "--delete-lemmas"  => delLemmas (fun o => { o with deleteLemmas := true })
    | "--delete-lemmas-uniform" => delLemmas (fun o => { o with deleteLemmas := true, deleteUniform := true })
    | "--delete-lemmas-leaves" => delLemmas (fun o => { o with deleteLemmas := true, deleteLeaves := true })
    | "--aggressively-delete-lemmas" => delLemmas (fun o => { o with deleteLemmas := true, aggressive := true })
    | "--corollary-delete-lemmas" => delLemmas (fun o => { o with deleteLemmas := true, corollary := true })
    | "--corollary-delete-lemmas-uniform" => delLemmas (fun o => { o with deleteLemmas := true, corollary := true, deleteUniform := true })
    | "--corollary-delete-lemmas-leaves" => delLemmas (fun o => { o with deleteLemmas := true, corollary := true, deleteLeaves := true })
    | "--corollary-delete-lemmas-all" => delLemmas (fun o => { o with deleteLemmas := true, corollary := true, corollaryAll := true })
    | "--corollary-delete-lemmas-leaves-all" => delLemmas (fun o => { o with deleteLemmas := true, corollary := true, deleteLeaves := true, corollaryAll := true })
    | "--compact"        => parseArgs rest { o with compact := true }
    | "--text"           => parseArgs rest { o with textMode := true }
    | "-v"               => parseArgs rest { o with verbose := true }
    | "--all"            => parseArgs rest { o with probOpt := some 1.0 }
    | "--by-centrality"  => parseArgs rest { o with byCentrality := true }
    | "--truncate"         => parseArgs rest { o with truncate := true }
    | "--shrink-challenge" => parseArgs rest { o with shrinkChallenge := true }
    | "--shrink-solution"  => parseArgs rest { o with shrinkSolution := true }
    | "--shrink-challenge-minimal" => parseArgs rest { o with shrinkChallengeMinimal := true }
    | "--shrink-solution-minimal"  => parseArgs rest { o with shrinkSolutionMinimal := true }
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
    | "--repo"           => needArg fun v => { o with repoOpt := some v }
    | "--revision"       => needArg fun v => { o with revisionOpt := some v }
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
    -- shrinking the solution implies shrinking the challenge (a shrunk solution against
    -- a full challenge is meaningless), so the solution flag forces the challenge flag
    shrinkChallenge := o.shrinkChallenge || o.shrinkSolution
    shrinkSolution := o.shrinkSolution
    shrinkChallengeMinimal := o.shrinkChallengeMinimal || o.shrinkSolutionMinimal
    shrinkSolutionMinimal := o.shrinkSolutionMinimal
    deleteLemmas := o.deleteLemmas
    deleteCount := o.deleteCount
    deleteUniform := o.deleteUniform
    deleteLeaves := o.deleteLeaves
    aggressive := o.aggressive
    corollary := o.corollary
    corollaryAll := o.corollaryAll
    forcedCorollary := none }

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
  let baseSpec := { spec with count := none, truncate := false, shrinkChallenge := false, shrinkSolution := false,
                              shrinkChallengeMinimal := false, shrinkSolutionMinimal := false }
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

/-- Normalise a git remote URL to a stable `host/owner/repo` form (strip a
    trailing `.git`, rewrite `git@host:owner/repo` → `host/owner/repo`, drop a
    leading scheme). Leaves anything unrecognised untouched. -/
def normalizeRemote (url : String) : String :=
  let u := if url.endsWith ".git" then url.dropRight 4 else url
  -- scp-style: git@github.com:owner/repo
  let u := if u.startsWith "git@" then
             let rest := u.drop 4
             (rest.replace ":" "/")
           else u
  -- strip scheme://[user@]
  let u := match (u.splitOn "://") with | [_, tl] => tl | _ => u
  u

/-- Best-effort git provenance for the repo enclosing `dir`: (remote, HEAD sha).
    Returns `none` components when `git` is unavailable or `dir` isn't a repo. -/
def gitInfo (dir : System.FilePath) : IO (Option String × Option String) := do
  let run (args : Array String) : IO (Option String) := do
    try
      let out ← IO.Process.output { cmd := "git", args := args, cwd := some dir.toString }
      if out.exitCode == 0 then
        let s := out.stdout.trim
        return (if s.isEmpty then none else some s)
      else return none
    catch _ => return none
  let url ← run #["config", "--get", "remote.origin.url"]
  let sha ← run #["rev-parse", "HEAD"]
  return (url.map normalizeRemote, sha)

/-- Walk up from `p` to the enclosing Lake package root (the dir with a
    lakefile). Returns `none` if there isn't one. -/
partial def findLakeRoot (p : System.FilePath) : IO (Option System.FilePath) := do
  if (← (p / "lakefile.toml").pathExists) || (← (p / "lakefile.lean").pathExists) then
    return some p
  else match p.parent with
    | some par => if par.toString == p.toString then return none else findLakeRoot par
    | none => return none

/-- Native compile-test (used by `--check-build`): put `content` at `path`, run
    `lake env lean <path>` — which compiles only that file against the package's
    prebuilt deps, never its dependents — then restore the original. So dropping
    trailing decls via --shrink can't break the check. Deps must already be
    built (`lake build`). -/
def checkCompiles (path : String) (content : String) : IO Bool := do
  let fp := System.FilePath.mk path
  let orig ← IO.FS.readFile fp
  IO.FS.writeFile fp content
  let result ← try
      let rootOpt ← findLakeRoot fp
      let root := rootOpt.getD (fp.parent.getD (System.FilePath.mk "."))
      let out ← IO.Process.output
        { cmd := "lake", args := #["env", "lean", fp.toString], cwd := some root.toString }
      pure (out.exitCode == 0)
    finally
      IO.FS.writeFile fp orig
  return result

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

  if o.checkBuild then do
    let mut nok := 0
    let mut nfail := 0
    for d in docs do
      let rng := Rng.mk (seedBase ^^^ fnv1a d.display)
      let result := ablate d.toks spec rng centrality
      let chal ← checkCompiles d.path.toString result.text
      let sol ← checkCompiles d.path.toString result.solution
      if chal && sol then nok := nok + 1 else nfail := nfail + 1
      let cs := if chal then "ok" else "FAIL"
      let ss := if sol then "ok" else "FAIL"
      IO.println s!"{d.display}  challenge:{cs} solution:{ss}"
    IO.println s!"\nbuild-check: {nok} ok, {nfail} failed (of {docs.size} files)"
    return (if nfail == 0 then 0 else 1)

  let nRepeat := Nat.max 1 o.repeatN
  -- git provenance, cached per enclosing directory. CLI --repo/--revision override
  -- detection wholesale (needed for AFP-style sources with no local git).
  let mut emitted := 0
  let mut provCache : Std.HashMap String (Option String × Option String) := {}
  for d in docs do
    let parent := (d.path.parent.getD (System.FilePath.mk ".")).toString
    let (detRepo, detRev) ← match provCache.get? parent with
      | some v => pure v
      | none => do
          let v ← gitInfo (System.FilePath.mk parent)
          provCache := provCache.insert parent v
          pure v
    let repo := o.repoOpt.orElse (fun _ => detRepo)
    let revision := o.revisionOpt.orElse (fun _ => detRev)
    let mut seen : Std.HashSet String := {}
    let mut produced := 0
    -- A file yields several records via --repeat OR --corollary-delete-lemmas*-all. In
    -- the latter mode we emit one ablation per eligible corollary (file order), ignoring
    -- --repeat; otherwise one per repeat slot with a per-k seed.
    let results : Array AblationResult :=
      if spec.corollaryAll then
        ablateAll d.toks spec (Rng.mk (seedBase ^^^ fnv1a d.display)) centrality
      else Id.run do
        let mut rs : Array AblationResult := #[]
        for k in [0:nRepeat] do
          let rng := Rng.mk (seedBase ^^^ fnv1a d.display ^^^ (UInt64.ofNat k * golden))
          rs := rs.push (ablate d.toks spec rng centrality)
        return rs
    for result in results do
      -- aggressive delete-lemmas: only keep challenges that actually compile
      let valid ← if o.aggressive then checkCompiles d.path.toString result.text else pure true
      -- Only emit *real* challenges: at least one hole was inserted AND the challenge
      -- differs from the solution. A file with no eligible lemmas (or a no-op ablation)
      -- otherwise yields a trivial challenge — already-complete, no holes to fill —
      -- which any model "passes" by doing nothing, inflating baselines. (#trivial-skip)
      let nontrivial := result.ablated > 0 && result.text != result.solution
      -- dedup key: challenge text normally, but the (deleted lemma(s), corollary) PAIR
      -- under --corollary-delete-lemmas*-all, so the same lemma deleted for different
      -- corollaries is kept — only an identical lemma/corollary pair is dropped.
      let namesKey (arr : Array String) : String := String.intercalate "," (arr.qsort (· < ·)).toList
      let key :=
        if spec.corollaryAll then
          namesKey (result.deleted.map (·.name)) ++ " @@ " ++ namesKey (result.corollaries.map (·.name))
        else result.text
      if valid && nontrivial && !seen.contains key then
        seen := seen.insert key
        if o.textMode then
          IO.print result.text
        else
          let variant := if nRepeat > 1 || spec.corollaryAll then some produced else none
          let obj := Ablator.record d.display o.session spec (Int.ofNat seedBase.toNat)
                       variant o.difficulty repo revision result
          IO.println (if o.compact then obj.compact else obj.pretty)
        produced := produced + 1
        emitted := emitted + 1
  if o.verbose then
    IO.eprintln s!"[emitted {emitted} {if o.textMode then "theories" else "records"}]"
  return 0
