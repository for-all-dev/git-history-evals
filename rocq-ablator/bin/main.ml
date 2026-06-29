(* [ablate] CLI — the Rocq ablator's command line. Takes any mix of .v files and
   directories (walked for *.v) and emits one (challenge, solution) JSON record
   per file, or runs the corpus self-test with --check. Flags mirror the
   Isabelle/Lean ablators. *)

open Rocq_ablator

let usage =
  {|Usage: ablate [OPTIONS] PATH...

  Ablate proofs in Coq/Rocq source, replacing them with `Admitted.` (top level)
  or `admit.` (nested). PATH... is any mix of .v files and directories (walked
  for *.v). Default: emit one indented JSON (challenge, solution) record per file.

  Modes:
    --check          run the corpus self-test (round-trip + delimitation)
    --check-build    compile-test each ablation with coqc (challenge + solution);
                     only the file itself is built (its deps must already be
                     compiled), so --shrink can't break it

  Difficulty (raw knobs override any --difficulty preset):
    --difficulty L   preset ladder L0 (easy) .. L4 (code+spec only)
    --min-depth N    ablate goals at nesting depth >= N (default: 1)
    --max-depth N    ablate goals at nesting depth <= N; N may be `inf`
    --leaves-only    only ablate proofs whose body has no nested sub-proof
    --min-size N / --max-size N   body command-count window (N may be `inf`)
    --min-centrality N / --max-centrality N   corpus fan-in window
    -p PROB          probability of ablating each selected proof (default: 0.5)
    --all            ablate every selected proof (-p 1.0)
    --count N        ablate exactly min(N, matching) proofs (excl. -p/--all)
    --by-centrality  with --count, pick the most-cited proofs
    --allow-defined  also ablate Defined-terminated proofs (opacity risk)

  Context shaping (ignored by --check):
    --truncate          drop challenge text after the last inserted hole
    --shrink-challenge  drop challenge top-level goals after the N-th hole (--count);
                        keeps the prefix + structural closers (well-formed)
    --shrink-solution   same, for the solution
    --shrink-challenge-minimal
                        keep only the N holes + the dependency closure their
                        statements need; drop all unrelated decls (syntactic slice)
    --shrink-solution-minimal
                        same for the solution (restores the deleted lemma + its deps)

  Lemma deletion (instead of per-proof ablation):
    --delete-lemmas [N] delete eligible used lemmas + ablate their users. An optional
                        N deletes exactly N lemmas (weighted random draw); omit N for
                        the --count/-p behavior below. (N works on every --delete-* flag.)
                        With --count N, deletions are drawn at random weighted by
                        in-file user count until >= N ablations result (seed-driven;
                        popular lemmas favoured, the tail still reachable).
                        Correct-by-construction (no prover).
    --delete-lemmas-uniform
                        like --delete-lemmas but draw deletions uniformly
                        (unweighted by user count).
    --delete-lemmas-leaves
                        like --delete-lemmas but hole only the leaf steps citing the
                        deleted lemma (keeping the proof skeleton); falls back to
                        whole-proof ablation when a citation isn't leaf-isolated.
    --aggressively-delete-lemmas
                        as above but relaxes the syntactic guards and validates
                        each challenge with coqc (--check-build), dropping any
                        that don't compile. Needs coq.

  Other:
    -s SESSION       session/library label recorded in output (default: coq)
    -d DIR           strip DIR prefix from emitted file paths (repeatable)
    --repeat N       emit up to N deduplicated ablations per file (default: 1)
    --seed N         RNG seed (default: random)
    --text           output the ablated source instead of JSONL
    --compact        strict one-object-per-line JSONL (no indentation)
    -v               verbose: progress/summary on stderr
|}

let golden = 0x9E3779B97F4A7C15L

let die msg =
  prerr_endline ("error: " ^ msg);
  exit 2

let parse_depth s = if s = "inf" || s = "infinity" then Ablate.inf
  else match int_of_string_opt s with Some n -> n | None -> die ("bad number: " ^ s)

let fnv1a (s : string) : int64 =
  let h = ref 0xcbf29ce484222325L in
  String.iter
    (fun c ->
      h := Int64.logxor !h (Int64.of_int (Char.code c));
      h := Int64.mul !h 0x100000001b3L)
    s;
  !h

let rec collect acc p =
  if Sys.is_directory p then
    let kids = Sys.readdir p in
    Array.sort compare kids;
    Array.fold_left (fun acc k -> collect acc (Filename.concat p k)) acc kids
  else if Filename.check_suffix p ".v" then p :: acc
  else acc

let read_file p =
  let ic = open_in_bin p in
  let len = in_channel_length ic in
  let s = really_input_string ic len in
  close_in ic;
  s

let display_path strip p =
  (* strip the longest matching -d prefix *)
  let strip = List.sort (fun a b -> compare (String.length b) (String.length a)) strip in
  let rec go = function
    | d :: rest ->
        if p = d then Filename.basename p
        else if String.length p > String.length d + 1
                && String.sub p 0 (String.length d + 1) = d ^ "/"
        then String.sub p (String.length d + 1) (String.length p - String.length d - 1)
        else go rest
    | [] -> p
  in
  go strip

type opts = {
  mutable session : string;
  mutable seed : int option;
  mutable verbose : bool;
  mutable check : bool;
  mutable check_build : bool;
  mutable compact : bool;
  mutable text_mode : bool;
  mutable difficulty : string option;
  mutable prob : float option;
  mutable all : bool;
  mutable count : int option;
  mutable by_centrality : bool;
  mutable min_depth : int option;
  mutable max_depth : int option;
  mutable leaves : bool;
  mutable min_size : int option;
  mutable max_size : int option;
  mutable min_cent : int option;
  mutable max_cent : int option;
  mutable truncate : bool;
  mutable shrink_challenge : bool;
  mutable shrink_solution : bool;
  mutable shrink_challenge_minimal : bool;
  mutable shrink_solution_minimal : bool;
  mutable allow_defined : bool;
  mutable delete_lemmas : bool;
  mutable delete_count : int option;
  mutable delete_uniform : bool;
  mutable delete_leaves : bool;
  mutable aggressive : bool;
  mutable repeat : int;
  mutable strip_dirs : string list;
  mutable paths : string list;
}

let parse_args argv =
  let o =
    { session = "coq"; seed = None; verbose = false; check = false; check_build = false; compact = false;
      text_mode = false; difficulty = None; prob = None; all = false; count = None;
      by_centrality = false; min_depth = None; max_depth = None; leaves = false;
      min_size = None; max_size = None; min_cent = None; max_cent = None;
      truncate = false; shrink_challenge = false; shrink_solution = false;
      shrink_challenge_minimal = false; shrink_solution_minimal = false;
      allow_defined = false; delete_lemmas = false; delete_count = None; delete_uniform = false;
      delete_leaves = false; aggressive = false;
      repeat = 1; strip_dirs = []; paths = [] }
  in
  let n = Array.length argv in
  let i = ref 1 in
  let next flag =
    incr i;
    if !i >= n then die ("missing arg for " ^ flag) else argv.(!i)
  in
  (* consume the next token as a non-negative int if it looks like one (for the
     optional count on --delete-lemmas[ N]); otherwise leave it for the next arg. *)
  let peek_int () =
    if !i + 1 < n then
      match int_of_string_opt argv.(!i + 1) with
      | Some k when k >= 0 -> incr i; Some k
      | _ -> None
    else None
  in
  while !i < n do
    let a = argv.(!i) in
    (match a with
     | "-h" | "--help" -> print_string usage; exit 0
     | "--check" -> o.check <- true
     | "--check-build" -> o.check_build <- true
     | "--compact" -> o.compact <- true
     | "--text" -> o.text_mode <- true
     | "-v" -> o.verbose <- true
     | "--all" -> o.all <- true
     | "--by-centrality" -> o.by_centrality <- true
     | "--truncate" -> o.truncate <- true
     | "--shrink-challenge" -> o.shrink_challenge <- true
     | "--shrink-solution" -> o.shrink_solution <- true
     | "--shrink-challenge-minimal" -> o.shrink_challenge_minimal <- true
     | "--shrink-solution-minimal" -> o.shrink_solution_minimal <- true
     | "--leaves-only" -> o.leaves <- true
     | "--allow-defined" -> o.allow_defined <- true
     | "--delete-lemmas" -> o.delete_lemmas <- true; o.delete_count <- peek_int ()
     | "--delete-lemmas-uniform" -> o.delete_lemmas <- true; o.delete_uniform <- true; o.delete_count <- peek_int ()
     | "--delete-lemmas-leaves" -> o.delete_lemmas <- true; o.delete_leaves <- true; o.delete_count <- peek_int ()
     | "--aggressively-delete-lemmas" -> o.delete_lemmas <- true; o.aggressive <- true; o.delete_count <- peek_int ()
     | "--difficulty" -> o.difficulty <- Some (next a)
     | "--min-depth" -> o.min_depth <- Some (parse_depth (next a))
     | "--max-depth" -> o.max_depth <- Some (parse_depth (next a))
     | "--min-size" -> o.min_size <- Some (parse_depth (next a))
     | "--max-size" -> o.max_size <- Some (parse_depth (next a))
     | "--min-centrality" -> o.min_cent <- Some (parse_depth (next a))
     | "--max-centrality" -> o.max_cent <- Some (parse_depth (next a))
     | "-p" -> o.prob <- Some (float_of_string (next a))
     | "--count" -> o.count <- Some (int_of_string (next a))
     | "--repeat" -> o.repeat <- int_of_string (next a)
     | "--seed" -> o.seed <- Some (int_of_string (next a))
     | "-s" -> o.session <- next a
     | "-d" -> o.strip_dirs <- next a :: o.strip_dirs
     | _ ->
         if String.length a > 0 && a.[0] = '-' && a <> "-" then die ("Unknown option: " ^ a)
         else o.paths <- a :: o.paths);
    incr i
  done;
  o.paths <- List.rev o.paths;
  o

let build_spec o =
  let preset = match o.difficulty with
    | Some d -> (match Ablate.preset_of d with
        | Some p -> Some p
        | None -> die (Printf.sprintf "unknown --difficulty %s (expected L0..L%d)" d (Array.length Ablate.ladder - 1)))
    | None -> None
  in
  let opt v d = match v with Some x -> x | None -> d in
  let preset_get f d = match preset with Some p -> f p | None -> d in
  Ablate.
    {
      prob =
        (match o.prob with
         | Some p -> p
         | None -> if o.all then 1.0 else preset_get (fun p -> p.p_prob) 0.5);
      count = o.count;
      by_centrality = o.by_centrality;
      min_depth = opt o.min_depth (preset_get (fun p -> p.p_min) 1);
      max_depth = opt o.max_depth (preset_get (fun p -> p.p_max) 1);
      leaves_only = o.leaves || preset_get (fun p -> p.p_leaves) false;
      min_size = opt o.min_size 0;
      max_size = opt o.max_size Ablate.inf;
      min_centrality = opt o.min_cent 0;
      max_centrality = opt o.max_cent Ablate.inf;
      truncate = o.truncate;
      shrink_challenge = o.shrink_challenge;
      shrink_solution = o.shrink_solution;
      shrink_challenge_minimal = o.shrink_challenge_minimal;
      shrink_solution_minimal = o.shrink_solution_minimal;
      allow_defined = o.allow_defined;
      delete_lemmas = o.delete_lemmas;
      delete_count = o.delete_count;
      delete_uniform = o.delete_uniform;
      delete_leaves = o.delete_leaves;
      aggressive = o.aggressive;
    }

let count_goals text =
  let spans = Span.parse_spans text in
  Array.fold_left (fun a s -> if Span.is_goal s then a + 1 else a) 0 spans

let run_check docs spec centrality =
  let base =
    Ablate.
      { spec with count = None; truncate = false; shrink_challenge = false; shrink_solution = false;
                  shrink_challenge_minimal = false; shrink_solution_minimal = false }
  in
  let n_files = ref 0 and n_goals = ref 0 and n_ablated = ref 0 in
  let rt = ref [] and dl = ref [] and rp = ref [] in
  List.iter
    (fun (path, text) ->
      let spans = Span.parse_spans text in
      let id = Ablate.ablate spans { base with prob = 0.0 } (Ablate.Rng.make 0L) centrality in
      if id.text <> text then rt := path :: !rt;
      let all = Ablate.ablate spans { base with prob = 1.0 } (Ablate.Rng.make 0L) centrality in
      incr n_files;
      n_goals := !n_goals + all.total;
      n_ablated := !n_ablated + all.ablated;
      if all.ablated <> all.total then dl := path :: !dl;
      if count_goals all.text <> count_goals text then rp := path :: !rp)
    docs;
  Printf.printf "\n================ ablation self-test ================\n";
  Printf.printf "files checked        : %d\n" !n_files;
  Printf.printf "in-range proofs      : %d\n" !n_goals;
  let pct = if !n_goals > 0 then 100.0 *. float_of_int !n_ablated /. float_of_int !n_goals else 0.0 in
  Printf.printf "cleanly ablated      : %d (%.2f%%)\n" !n_ablated pct;
  Printf.printf "round-trip failures  : %d\n" (List.length !rt);
  Printf.printf "delimitation misses  : %d\n" (List.length !dl);
  Printf.printf "re-parse mismatches  : %d\n" (List.length !rp);
  List.iter
    (fun (label, xs) ->
      if xs <> [] then begin
        Printf.printf "\n-- %s (%d), first 10:\n" label (List.length xs);
        List.iteri (fun i x -> if i < 10 then Printf.printf "   %s\n" x) (List.rev xs)
      end)
    [ ("round-trip failures", !rt); ("delimitation misses", !dl); ("re-parse mismatches", !rp) ];
  let ok = !rt = [] && !rp = [] in
  Printf.printf "\nRESULT: %s\n" (if ok then "OK" else "FAILURES PRESENT");
  ok

let () =
  let o = parse_args Sys.argv in
  let spec = build_spec o in
  if o.count <> None && o.prob <> None then die "--count cannot be combined with -p / --all";
  if o.by_centrality && o.count = None then die "--by-centrality only applies with --count";
  if spec.min_depth < 1 then die "--min-depth must be >= 1";
  if o.paths = [] then (prerr_string usage; exit 2);
  let base_seed =
    match o.seed with
    | Some s -> Int64.of_int s
    | None -> Random.self_init (); Int64.of_int (Random.bits () lxor (Random.bits () lsl 30))
  in
  let files = List.fold_left collect [] o.paths |> List.sort_uniq compare in
  let docs = List.filter_map (fun p -> try Some (p, read_file p) with _ -> None) files in
  if o.verbose then Printf.eprintf "[session %s: %d files]\n%!" o.session (List.length docs);

  let need_cent =
    Ablate.uses_centrality spec || ((not o.check) && not o.text_mode)
  in
  let fan = if need_cent then Centrality.fan_in (List.map snd docs) else Hashtbl.create 1 in
  let centrality name = match Hashtbl.find_opt fan name with Some c -> c | None -> 0 in

  if o.check then exit (if run_check docs spec centrality then 0 else 1);

  if o.check_build then begin
    let ok = ref 0 and fail = ref 0 in
    List.iter
      (fun (path, original) ->
        let spans = Span.parse_spans original in
        let seed = Int64.logxor base_seed (fnv1a (display_path o.strip_dirs path)) in
        let result = Ablate.ablate spans spec (Ablate.Rng.make seed) centrality in
        let chal = Build_check.check_compiles path result.text in
        let sol = Build_check.check_compiles path result.solution in
        if chal && sol then incr ok else incr fail;
        Printf.printf "%-50s challenge:%-4s solution:%-4s\n%!" path
          (if chal then "ok" else "FAIL") (if sol then "ok" else "FAIL"))
      docs;
    Printf.printf "\nbuild-check: %d ok, %d failed (of %d files)\n" !ok !fail (List.length docs);
    exit (if !fail = 0 then 0 else 1)
  end;

  let n_repeat = max 1 o.repeat in
  let emitted = ref 0 in
  List.iter
    (fun (path, original) ->
      let display = display_path o.strip_dirs path in
      let spans = Span.parse_spans original in
      let seen = Hashtbl.create 8 in
      let produced = ref 0 in
      for k = 0 to n_repeat - 1 do
        let pf = fnv1a display in
        let seed =
          Int64.logxor base_seed (Int64.logxor pf (Int64.mul (Int64.of_int k) golden))
        in
        let result = Ablate.ablate spans spec (Ablate.Rng.make seed) centrality in
        (* aggressive delete-lemmas: only keep challenges that actually compile *)
        let valid = (not o.aggressive) || Build_check.check_compiles path result.text in
        if valid && not (Hashtbl.mem seen result.text) then begin
          Hashtbl.replace seen result.text ();
          if o.text_mode then print_string result.text
          else begin
            let variant = if n_repeat > 1 then Some !produced else None in
            let obj =
              Record.record ~file_path:display ~session:o.session ~spec
                ~seed:(Int64.to_int base_seed) ~variant ~difficulty:o.difficulty
                ~result
            in
            print_endline
              (if o.compact then Yojson.Safe.to_string obj else Yojson.Safe.pretty_to_string obj)
          end;
          incr produced;
          incr emitted
        end
      done)
    docs;
  if o.verbose then
    Printf.eprintf "[emitted %d %s]\n%!" !emitted (if o.text_mode then "files" else "records")
