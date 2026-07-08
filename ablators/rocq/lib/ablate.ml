(* Proof ablation for Coq/Rocq: the depth-walk + difficulty knobs, modelled on
   the Isabelle ablator's [ablate.rs]. Operates on parsed spans; replaces
   selected proofs with admit-style holes, preserving everything else
   byte-for-byte.

   - Top-level (depth 1): a whole proof script is replaced by [Proof. Admitted.]
     (valid with or without an original [Proof.]).
   - Nested (depth >= 2): a focused sub-proof — a brace block [{ ... }], a bullet
     segment ([-]/[+]/[*]), or an [... by tac] clause — is replaced by [admit],
     and the enclosing terminator [Qed]/[Defined]/[Save] is rewritten to
     [Admitted] (a proof with an admitted goal can only be closed by [Admitted]). *)

let inf = max_int

type spec = {
  prob : float;
  count : int option;
  by_centrality : bool;
  min_depth : int;
  max_depth : int;
  leaves_only : bool;
  min_size : int;
  max_size : int;
  min_centrality : int;
  max_centrality : int;
  truncate : bool;
  shrink_challenge : bool; (* drop challenge top-level goals after the N-th hole *)
  shrink_solution : bool; (* drop solution top-level goals after the N-th hole *)
  shrink_challenge_minimal : bool; (* challenge: keep only the N holes + dep closure *)
  shrink_solution_minimal : bool; (* solution: keep only the N holes' decls + dep closure *)
  allow_defined : bool; (* ablate Defined-terminated proofs too (opacity risk) *)
  delete_lemmas : bool; (* delete eligible lemmas + ablate their users *)
  delete_count : int option; (* delete-lemmas: delete exactly this many lemmas (None = count/prob) *)
  delete_uniform : bool; (* delete-lemmas: pick deletions uniformly, not weighted by user count *)
  delete_leaves : bool; (* delete-lemmas: hole only the leaf steps citing L, not whole proofs *)
  aggressive : bool; (* delete-lemmas: relax syntactic guards (BE; needs check-build) *)
  corollary : bool; (* delete-lemmas: restrict candidates to one random theorem's dep closure *)
}

let default_spec =
  {
    prob = 0.5;
    count = None;
    by_centrality = false;
    min_depth = 1;
    max_depth = 1;
    leaves_only = false;
    min_size = 0;
    max_size = inf;
    min_centrality = 0;
    max_centrality = inf;
    truncate = false;
    shrink_challenge = false;
    shrink_solution = false;
    shrink_challenge_minimal = false;
    shrink_solution_minimal = false;
    allow_defined = false;
    delete_lemmas = false;
    delete_count = None;
    delete_uniform = false;
    delete_leaves = false;
    aggressive = false;
    corollary = false;
  }

let uses_centrality s =
  s.min_centrality > 0 || s.max_centrality <> inf || s.by_centrality

type hole = {
  theorem_name : string;
  depth : int;
  n_commands : int;
  n_lines : int;
  is_leaf : bool;
  centrality : int;
  method_ : string;
  proof_text : string;
  metrics : Metrics.t; (* proof-complexity metrics of [proof_text] (spec §2) *)
}

(* A lemma the ablator removed entirely (--delete-lemmas / corollary modes). [d_fan_in]
   is its in-file user count (how many proofs cited it); [d_metrics] covers the whole
   block for size and the proof body for the tactic heuristics. *)
type deleted_lemma = {
  d_name : string;
  d_text : string; (* original block: statement + proof *)
  d_fan_in : int;
  d_metrics : Metrics.t;
}

(* The theorem whose transitive in-file dependency closure seeded a corollary-mode
   deletion (empty outside corollary mode). It is typically also a holed theorem. *)
type corollary = {
  co_name : string;
  co_fan_in : int;
  co_metrics : Metrics.t;
}

type result = {
  text : string; (* the ablated challenge *)
  solution : string; (* the original, optionally shrunk to match *)
  total : int;
  ablated : int;
  holes : hole list;
  deleted : deleted_lemma list; (* lemmas removed for --delete-lemmas *)
  corollaries : corollary list; (* seeds of corollary-mode deletions (else []) *)
  closure_size : int; (* distinct eligible closure members drawn from (else 0) *)
}

(* ---------- SplitMix64 PRNG (seedable, reproducible) ---------- *)

module Rng = struct
  type t = { mutable s : int64 }

  let make seed = { s = seed }

  let next_u64 r =
    r.s <- Int64.add r.s 0x9E3779B97F4A7C15L;
    let z = r.s in
    let z =
      Int64.mul (Int64.logxor z (Int64.shift_right_logical z 30)) 0xBF58476D1CE4E5B9L
    in
    let z =
      Int64.mul (Int64.logxor z (Int64.shift_right_logical z 27)) 0x94D049BB133111EBL
    in
    Int64.logxor z (Int64.shift_right_logical z 31)

  let next_f64 r =
    let m = Int64.shift_right_logical (next_u64 r) 11 in
    Int64.to_float m /. 9007199254740992.0 (* 2^53 *)

  let shuffle r arr =
    let i = ref (Array.length arr) in
    while !i > 1 do
      decr i;
      let u = Int64.logand (next_u64 r) 0x7FFFFFFFFFFFFFFFL in
      let j = Int64.to_int (Int64.rem u (Int64.of_int (!i + 1))) in
      let tmp = arr.(!i) in
      arr.(!i) <- arr.(j);
      arr.(j) <- tmp
    done
end

(* ---------- small pure helpers ---------- *)

let n_lines s =
  if s = "" then 0
  else 1 + String.fold_left (fun a c -> if c = '\n' then a + 1 else a) 0 s

(* Metrics for a holed proof, whose only available text is the proof body. *)
let hole_metrics (proof_text : string) : Metrics.t =
  Metrics.compute ~block:proof_text ~body:proof_text

let lead_of (s : Span.t) =
  let b = Buffer.create 16 in
  (try
     List.iter
       (fun (t : Token.t) ->
         if Token.is_space t || Token.is_comment t then Buffer.add_string b t.source
         else raise Exit)
       s.content
   with Exit -> ());
  Buffer.contents b

(* method label for a tactic sentence: first tactic ident, apply-family folded
   to "apply", "by tac" -> "by:tac". *)
let method_of_span (s : Span.t) : string =
  match Span.proper s with
  | [] -> ""
  | first :: rest ->
      if not (Token.is_ident first) then ""
      else
        let h = first.source in
        if h = "by" then
          match List.find_opt Token.is_ident rest with
          | Some t -> "by:" ^ t.source
          | None -> "by"
        else if List.mem h [ "apply"; "eapply"; "exact"; "refine"; "simple" ] then
          "apply"
        else h

(* rewrite a terminator span's head token (Qed/Defined/Save) to Admitted *)
let rewrite_terminator (s : Span.t) : string =
  let b = Buffer.create 16 in
  let patched = ref false in
  List.iter
    (fun (t : Token.t) ->
      if (not !patched) && Token.is_proper t then begin
        Buffer.add_string b "Admitted";
        patched := true
      end
      else Buffer.add_string b t.source)
    s.content;
  Buffer.contents b

(* find the top-level "by" clause of a tactic sentence (bracket depth 0, with at
   least one proper token after it). Returns (prefix_src_through_by, body_text)
   or None. The final Dot is emitted separately by the caller. *)
let find_by_clause (s : Span.t) : (string * string) option =
  let toks = Array.of_list s.content in
  let n = Array.length toks in
  let depth = ref 0 in
  let by_idx = ref (-1) in
  let i = ref 0 in
  while !by_idx < 0 && !i < n do
    let t = toks.(!i) in
    (if Token.is_symbol t then
       match t.source with
       | "(" | "[" -> incr depth
       | ")" | "]" -> if !depth > 0 then decr depth
       | _ -> ()
     else if !depth = 0 && Token.is_ident_named t "by" then by_idx := !i);
    incr i
  done;
  if !by_idx < 0 then None
  else begin
    (* body = proper tokens after "by", before the terminating Dot *)
    let pre = Buffer.create 32 and body = Buffer.create 32 in
    let has_body = ref false in
    for k = 0 to !by_idx do
      Buffer.add_string pre toks.(k).source
    done;
    for k = !by_idx + 1 to n - 1 do
      let t = toks.(k) in
      if not (Token.is_dot t) then begin
        Buffer.add_string body t.source;
        if Token.is_proper t then has_body := true
      end
    done;
    if !has_body then Some (Buffer.contents pre, Buffer.contents body) else None
  end

(* ---------- difficulty preset ladder (easy -> hard) ---------- *)

type preset = { p_prob : float; p_min : int; p_max : int; p_leaves : bool }

let ladder =
  [|
    { p_prob = 0.3; p_min = 1; p_max = inf; p_leaves = true };  (* L0 *)
    { p_prob = 1.0; p_min = 1; p_max = inf; p_leaves = true };  (* L1 *)
    { p_prob = 1.0; p_min = 2; p_max = inf; p_leaves = false }; (* L2 *)
    { p_prob = 0.5; p_min = 1; p_max = 1; p_leaves = false };   (* L3 *)
    { p_prob = 1.0; p_min = 1; p_max = 1; p_leaves = false };   (* L4 *)
  |]

let preset_of s =
  let t = String.lowercase_ascii s in
  let t = if String.length t > 0 && t.[0] = 'l' then String.sub t 1 (String.length t - 1) else t in
  match int_of_string_opt t with
  | Some i when i >= 0 && i < Array.length ladder -> Some ladder.(i)
  | _ -> None

(* ---------- the walk ---------- *)

let walk_all (spans : Span.t array) (spec : spec) (centrality : string -> int)
    (decide : int -> bool) : result * (int * int) array =
  let n = Array.length spans in
  let out = Buffer.create 4096 in
  let emit s = Buffer.add_string out s in
  let buflen () = Buffer.length out in
  let holes = ref [] in
  let matches = ref [] in
  let top_segs = ref [] in
  let last_admit_end = ref (-1) in
  let total = ref 0 in
  let ablated = ref 0 in
  let cur_admit = ref false in

  let src_range lo hi =
    let b = Buffer.create 64 in
    for k = lo to hi - 1 do
      Buffer.add_string b (Span.source spans.(k))
    done;
    Buffer.contents b
  in
  let src_len lo hi =
    let c = ref 0 in
    for k = lo to hi - 1 do
      c := !c + String.length (Span.source spans.(k))
    done;
    !c
  in
  let count_cmds lo hi =
    let c = ref 0 in
    for k = lo to hi - 1 do
      match spans.(k).kind with Span.Sentence _ -> incr c | _ -> ()
    done;
    !c
  in
  let has_nested lo hi =
    let r = ref false in
    for k = lo to hi - 1 do
      match spans.(k).kind with Span.Open | Span.Bullet _ -> r := true | _ -> ()
    done;
    !r
  in
  let method_in lo hi =
    let res = ref "" in
    (try
       for k = lo to hi - 1 do
         match spans.(k).kind with
         | Span.Sentence _ when not (Span.is_proof spans.(k)) ->
             let m = method_of_span spans.(k) in
             if m <> "" then begin
               res := m;
               raise Exit
             end
         | _ -> ()
       done
     with Exit -> ());
    if !res = "" then "?" else !res
  in
  (* matching close index of a focus brace opened at [j], or n *)
  let find_brace_end j =
    let d = ref 1 and k = ref (j + 1) and res = ref n in
    while !res = n && !k < n do
      (match spans.(!k).kind with
       | Span.Open -> incr d
       | Span.Close ->
           decr d;
           if !d = 0 then res := !k
       | _ -> ());
      if !res = n then incr k
    done;
    !res
  in
  (* exclusive end of a bullet segment opened at [j] with signature [sg] *)
  let find_bullet_end j sg =
    let d = ref 0 and k = ref (j + 1) and res = ref n in
    while !res = n && !k < n do
      let s = spans.(!k) in
      (match s.kind with
       | Span.Open -> incr d
       | Span.Close -> if !d = 0 then res := !k else decr d
       | Span.Bullet b -> if !d = 0 && b = sg then res := !k
       | Span.Sentence _ -> if !d = 0 && Span.is_terminator s then res := !k
       | _ -> ());
      if !res = n then incr k
    done;
    !res
  in
  let record_hole ~name ~depth ~lo ~hi ~cent ~proof_text =
    holes :=
      {
        theorem_name = name;
        depth;
        n_commands = count_cmds lo hi;
        n_lines = n_lines proof_text;
        is_leaf = not (has_nested lo hi);
        centrality = cent;
        method_ = method_in lo hi;
        proof_text;
        metrics = hole_metrics proof_text;
      }
      :: !holes
  in
  (* candidate gate shared by nested units (name "" => centrality 0) *)
  let nested_candidate ~depth ~lo ~hi ~well_formed =
    well_formed
    && count_cmds lo hi >= 1
    && depth >= spec.min_depth && depth <= spec.max_depth
    && ((not spec.leaves_only) || not (has_nested lo hi))
    && count_cmds lo hi >= spec.min_size && count_cmds lo hi <= spec.max_size
    && 0 >= spec.min_centrality && 0 <= spec.max_centrality
  in

  (* walk a kept proof body for one top-level goal: emit spans, ablate nested
     units, rewrite the terminator if any admit was inserted. Returns the index
     to continue from. *)
  let walk_body start =
    let i = ref start in
    let depth = ref 1 in
    let stop = ref false in
    while (not !stop) && !i < n do
      let s = spans.(!i) in
      if Span.is_terminator s && !depth = 1 then begin
        if !cur_admit && List.mem (Span.head s) [ "Qed"; "Defined"; "Save" ] then
          emit (rewrite_terminator s)
        else emit (Span.source s);
        incr i;
        stop := true
      end
      else if Span.is_goal s && !depth = 1 then stop := true (* malformed: leave for outer loop *)
      else
        match s.kind with
        | Span.Open ->
            let unit_depth = !depth + 1 in
            let close_idx = find_brace_end !i in
            let body_lo = !i + 1 and body_hi = close_idx in
            let well_formed = close_idx < n in
            if nested_candidate ~depth:unit_depth ~lo:body_lo ~hi:body_hi ~well_formed then begin
              incr total;
              matches := (!i, 0) :: !matches;
              if decide !i then begin
                emit (Span.source s);
                emit " admit.";
                emit (Span.source spans.(close_idx));
                cur_admit := true;
                incr ablated;
                last_admit_end := buflen ();
                record_hole ~name:"" ~depth:unit_depth ~lo:body_lo ~hi:body_hi
                  ~cent:0 ~proof_text:(src_range body_lo body_hi);
                i := close_idx + 1
              end
              else begin
                (* keep: descend into the brace body *)
                emit (Span.source s);
                incr depth;
                incr i
              end
            end
            else begin
              emit (Span.source s);
              incr depth;
              incr i
            end
        | Span.Close ->
            emit (Span.source s);
            decr depth;
            incr i
        | Span.Bullet sg ->
            let unit_depth = !depth + 1 in
            let seg_end = find_bullet_end !i sg in
            let body_lo = !i + 1 and body_hi = seg_end in
            if nested_candidate ~depth:unit_depth ~lo:body_lo ~hi:body_hi ~well_formed:true
            then begin
              incr total;
              matches := (!i, 0) :: !matches;
              if decide !i then begin
                emit (Span.source s);
                emit " admit.";
                cur_admit := true;
                incr ablated;
                last_admit_end := buflen ();
                record_hole ~name:"" ~depth:unit_depth ~lo:body_lo ~hi:body_hi
                  ~cent:0 ~proof_text:(src_range body_lo body_hi);
                i := seg_end
              end
              else begin
                emit (Span.source s);
                incr i
              end
            end
            else begin
              emit (Span.source s);
              incr i
            end
        | Span.Sentence _ -> (
            (* maybe ablate an inline "by tac" clause (a depth+1 unit) *)
            let unit_depth = !depth + 1 in
            match find_by_clause s with
            | Some (pre, body)
              when unit_depth >= spec.min_depth && unit_depth <= spec.max_depth
                   && 1 >= spec.min_size && 1 <= spec.max_size
                   && 0 >= spec.min_centrality && 0 <= spec.max_centrality ->
                incr total;
                matches := (!i, 0) :: !matches;
                if decide !i then begin
                  emit pre;
                  emit " admit.";
                  cur_admit := true;
                  incr ablated;
                  last_admit_end := buflen ();
                  holes :=
                    {
                      theorem_name = "";
                      depth = unit_depth;
                      n_commands = 1;
                      n_lines = n_lines body;
                      is_leaf = true;
                      centrality = 0;
                      method_ = "by";
                      proof_text = body;
                      metrics = hole_metrics body;
                    }
                    :: !holes;
                  incr i
                end
                else begin
                  emit (Span.source s);
                  incr i
                end
            | _ ->
                emit (Span.source s);
                incr i)
        | Span.Ignored ->
            emit (Span.source s);
            incr i
    done;
    !i
  in
  (* measure a proof region starting at [start] (just after the goal) for the
     top-level candidate decision. Stops at the terminator (depth 1), the next
     goal, or EOF. *)
  let measure start =
    let i = ref start and depth = ref 1 in
    let term_idx = ref n and term_head = ref "" and has_term = ref false in
    let stop = ref false in
    while (not !stop) && !i < n do
      let s = spans.(!i) in
      if Span.is_terminator s && !depth = 1 then begin
        term_idx := !i + 1;
        term_head := Span.head s;
        has_term := true;
        stop := true
      end
      else if Span.is_goal s && !depth = 1 then begin
        term_idx := !i;
        stop := true
      end
      else begin
        (match s.kind with
         | Span.Open -> incr depth
         | Span.Close -> decr depth
         | _ -> ());
        incr i
      end
    done;
    (!term_idx, !term_head, !has_term)
  in

  let handle_goal g =
    let name = Span.name spans.(g) in
    emit (Span.source spans.(g));
    let term_idx, term_head, has_term = measure (g + 1) in
    let body_lo = g + 1 and body_hi = (if has_term then term_idx - 1 else term_idx) in
    let cent = centrality name in
    let term_ok =
      has_term
      && term_head <> "Admitted" && term_head <> "Abort"
      && (spec.allow_defined || term_head <> "Defined")
    in
    let top_candidate =
      term_ok
      && 1 >= spec.min_depth && 1 <= spec.max_depth
      && ((not spec.leaves_only) || not (has_nested body_lo body_hi))
      && count_cmds body_lo body_hi >= spec.min_size
      && count_cmds body_lo body_hi <= spec.max_size
      && cent >= spec.min_centrality && cent <= spec.max_centrality
    in
    if top_candidate then begin
      incr total;
      matches := (g, cent) :: !matches
    end;
    if top_candidate && decide g then begin
      (* whole-proof ablation: replace Proof...terminator with Proof. Admitted. *)
      let proof_text = src_range body_lo term_idx in
      emit (lead_of spans.(g + 1));
      emit "Proof. Admitted.";
      last_admit_end := buflen ();
      incr ablated;
      record_hole ~name ~depth:1 ~lo:body_lo ~hi:body_hi ~cent ~proof_text;
      term_idx
    end
    else begin
      cur_admit := false;
      walk_body (g + 1)
    end
  in

  (* top-level pass. Each segment records its end offset in BOTH the challenge
     output ([buflen]) and the original text ([orig]), so the challenge and the
     solution can be shrunk independently. *)
  let i = ref 0 in
  let orig = ref 0 in
  while !i < n do
    let s = spans.(!i) in
    if Span.is_goal s then begin
      let abl0 = !ablated in
      let g = !i in
      i := handle_goal g;
      orig := !orig + src_len g !i;
      (* a goal is never a structural closer *)
      top_segs := (buflen (), !orig, false, !ablated > abl0) :: !top_segs
    end
    else begin
      (* keep block openers too, so shrink keeps a balanced Section/Module skeleton *)
      let closer = Span.is_closer s || Span.is_opener s in
      emit (Span.source s);
      orig := !orig + String.length (Span.source s);
      incr i;
      top_segs := (buflen (), !orig, closer, false) :: !top_segs
    end
  done;

  let full = Buffer.contents out in
  let original = src_range 0 n in
  let segs = Array.of_list (List.rev !top_segs) in
  let chal_segs = Array.map (fun (c, _, g, a) -> (c, g, a)) segs in
  let sol_segs = Array.map (fun (_, o, g, a) -> (o, g, a)) segs in
  let text =
    if spec.truncate && !last_admit_end >= 0 then String.sub full 0 !last_admit_end
    else if spec.shrink_challenge then Shape.shrink ?count:spec.count full chal_segs
    else full
  in
  let solution =
    if spec.shrink_solution then Shape.shrink ?count:spec.count original sol_segs else original
  in
  ( {
      text;
      solution;
      total = !total;
      ablated = !ablated;
      holes = List.rev !holes;
      deleted = [];
      corollaries = [];
      closure_size = 0;
    },
    Array.of_list (List.rev !matches) )

(* ---------- --delete-lemmas: delete eligible lemmas + ablate their users ----- *)

(* Minimal dependency-closed slice for [--shrink-*-minimal]. Keep only the (first
   [count]) holes plus the transitive closure of the goal-decls their statements
   reference; ALL non-goal items (imports/sections/notations/defs) are kept as glue so
   the slice stays well-formed. Challenge excludes the deleted lemma(s) and renders any
   kept goal that still cites a deleted name as a hole (so it can't dangle); solution
   keeps everything real, pulling the deleted lemma(s) + their deps back in. Purely
   syntactic (no prover). *)
let slice_delete (spans : Span.t array) (spec : spec)
    ~(by_opener : (int, Uses.lemma) Hashtbl.t) ~(del : (int, Uses.lemma) Hashtbl.t)
    ~(to_ablate : (int, unit) Hashtbl.t) ~(solution : bool) : string =
  let n = Array.length spans in
  let src_range lo hi =
    let b = Buffer.create 64 in
    for k = lo to hi - 1 do
      Buffer.add_string b (Span.source spans.(k))
    done;
    Buffer.contents b
  in
  (* name -> ALL openers that define it. A name can be declared more than once (e.g. a
     `gtb_spec0` lemma inside each of the Nat/N/Z/Pos modules, or two `map_cps_correct`s),
     so we must keep *every* same-named decl in the closure — keeping only one (and, worse,
     an arbitrary one, since Hashtbl.iter order is unspecified) can drop the definition a
     kept hint/instance/statement actually resolves to, leaving a dangling reference. *)
  let openers_of_name = Hashtbl.create 64 in
  Hashtbl.iter
    (fun o (l : Uses.lemma) ->
      let prev = try Hashtbl.find openers_of_name l.name with Not_found -> [] in
      Hashtbl.replace openers_of_name l.name (o :: prev))
    by_opener;
  let deleted_names = Hashtbl.create 16 in
  Hashtbl.iter (fun _ (l : Uses.lemma) -> Hashtbl.replace deleted_names l.Uses.name ()) del;
  (* a kept goal must be holed in the challenge iff its body still cites a deleted name *)
  let must_hole o =
    match Hashtbl.find_opt by_opener o with
    | Some l -> List.exists (fun nm -> Hashtbl.mem deleted_names nm) l.Uses.body_names
    | None -> false
  in
  (* seed: the first [count] holes (file order); all of them if no --count *)
  let seed =
    let all = Hashtbl.fold (fun o () acc -> o :: acc) to_ablate [] |> List.sort compare in
    match spec.count with Some k -> List.filteri (fun i _ -> i < k) all | None -> all
  in
  (* Keep-set computed BEFORE ablating, shared by challenge and solution: the full
     statement+body dependency closure of the target holes over the *original* file.
     This keeps every lemma the real proofs need (so a challenge never throws away
     more than the deleted lemma itself) and gives challenge & solution the same
     context window — they differ only by the deletion + holing. The deleted lemma
     is kept in the set (restored in the solution, omitted from the challenge). *)
  let keep = Hashtbl.create 64 in
  let q = Queue.create () in
  let add o =
    if (not (Hashtbl.mem keep o)) && Hashtbl.mem by_opener o then begin
      Hashtbl.replace keep o ();
      Queue.push o q
    end
  in
  (* keep every in-file decl of a referenced name (all same-named openers, see above) *)
  let add_name nm = match Hashtbl.find_opt openers_of_name nm with Some os -> List.iter add os | None -> () in
  List.iter add seed;
  (* Structural items (Hint/Ltac/Notation/Arguments/…) are always kept, so any in-file
     decl they cite must be kept too — both decls a kept item *needs* (e.g.
     `Hint Resolve foo` needs `Lemma foo`) and decls kept proofs need *via* a kept item
     (e.g. an `Ltac` helper). Seed the closure with those references so nothing
     dangles. (Items citing the deleted lemma are handled at emit time below.) *)
  for k = 0 to n - 1 do
    if not (Span.is_goal spans.(k)) then
      List.iter add_name (Uses.names_in spans.(k).Span.content)
  done;
  while not (Queue.is_empty q) do
    let o = Queue.pop q in
    let l = Hashtbl.find by_opener o in
    List.iter add_name (l.Uses.stmt_names @ l.Uses.body_names)
  done;
  (* In the challenge the deleted lemma is omitted, so a kept structural item that cites
     it (e.g. `Hint Resolve <deleted>`) would dangle — drop such items from the
     challenge only (the solution restores the lemma, so they stay there). *)
  let struct_ok (s : Span.t) =
    solution
    || not (List.exists (fun nm -> Hashtbl.mem deleted_names nm) (Uses.names_in s.Span.content))
  in
  let buf = Buffer.create 4096 in
  let i = ref 0 in
  while !i < n do
    let s = spans.(!i) in
    if Span.is_goal s then begin
      let e = match Hashtbl.find_opt by_opener !i with Some l -> l.Uses.block_end | None -> !i + 1 in
      if Hashtbl.mem keep !i then begin
        if solution then Buffer.add_string buf (src_range !i e)
        else if Hashtbl.mem del !i then () (* deleted lemma: omitted from the challenge *)
        else if must_hole !i || Hashtbl.mem to_ablate !i then begin
          Buffer.add_string buf (Span.source spans.(!i));
          if !i + 1 < e then Buffer.add_string buf (lead_of spans.(!i + 1));
          Buffer.add_string buf "Proof. Admitted."
        end
        else Buffer.add_string buf (src_range !i e)
      end;
      i := e
    end
    else begin
      if struct_ok s then Buffer.add_string buf (Span.source s);
      incr i
    end
  done;
  Shape.collapse_blank_lines (Buffer.contents buf)

let ablate_delete (spans : Span.t array) (spec : spec) (rng : Rng.t) : result =
  let n = Array.length spans in
  let src_range lo hi =
    let b = Buffer.create 64 in
    for k = lo to hi - 1 do
      Buffer.add_string b (Span.source spans.(k))
    done;
    Buffer.contents b
  in
  let lemmas = Uses.analyze ~aggressive:spec.aggressive spans in
  let by_opener = Hashtbl.create 64 in
  List.iter (fun (l : Uses.lemma) -> Hashtbl.replace by_opener l.opener l) lemmas;
  let total_eligible = List.length (List.filter (fun (l : Uses.lemma) -> l.eligible) lemmas) in
  (* candidates: eligible lemmas within the centrality (= user-count) window *)
  let nusers (l : Uses.lemma) = List.length l.users in
  let cands =
    List.filter
      (fun (l : Uses.lemma) ->
        l.eligible && nusers l >= spec.min_centrality && nusers l <= spec.max_centrality)
      lemmas
  in
  (* [--count k] is a target number of *ablations* (holed users), not a number of
     deletions: deleting a lemma forces every one of its in-file users to be
     ablated, so ablations arrive in chunks. We draw deletions at random *without
     replacement*, each with probability proportional to its weight (its in-file
     user count — or 1 under [--delete-lemmas-uniform]), accumulating their forced
     ablations until the distinct total reaches >= k, then stop. This is seed-driven
     (so different seeds yield different evals), favours popular lemmas, yet keeps a
     non-zero chance on the long tail. Without --count the per-lemma [prob] coin
     decides, as before. (With --truncate we later keep only the first k.) *)
  (* corollary-mode bookkeeping: the seed theorem(s) deletions were drawn from, and the
     distinct eligible closure members they were drawn from (for the emitted record). *)
  let corollary_seeds = ref [] in
  let closure_openers : (int, unit) Hashtbl.t = Hashtbl.create 64 in
  let weight (l : Uses.lemma) = if spec.delete_uniform then 1.0 else float_of_int (nusers l) in
  (* one weighted pick (proportional to [weight]) from a non-empty candidate list *)
  let pick_weighted remaining =
    let total = List.fold_left (fun a l -> a +. weight l) 0.0 remaining in
    let r = Rng.next_f64 rng *. total in
    let rec go acc = function
      | [ l ] -> l
      | l :: tl -> let acc' = acc +. weight l in if acc' > r then l else go acc' tl
      | [] -> assert false
    in
    go 0.0 remaining
  in
  let without l rem = List.filter (fun (c : Uses.lemma) -> c.opener <> l.Uses.opener) rem in
  (* Corollary mode: pick a random theorem, take its transitive in-file dependency
     closure, and draw deletions from the *eligible* members of that closure (fan-in
     weighted via [pick_weighted], or uniform). One corollary is exhausted before a
     fresh one is drawn, so deletions stay concentrated in a single proof's subtree
     unless the target forces spilling. Mirrors the rust ablator's corollary_select. *)
  let corollary_select () =
    if cands = [] then []
    else begin
      let by_name : (string, Uses.lemma) Hashtbl.t = Hashtbl.create 64 in
      List.iter
        (fun (l : Uses.lemma) -> if not (Hashtbl.mem by_name l.name) then Hashtbl.replace by_name l.name l)
        lemmas;
      let deps_of (l : Uses.lemma) =
        l.stmt_names @ l.body_names
        |> List.filter_map (fun nm -> Hashtbl.find_opt by_name nm)
        |> List.filter (fun (d : Uses.lemma) -> d.opener <> l.opener)
      in
      let cand_openers = Hashtbl.create 64 in
      List.iter (fun (l : Uses.lemma) -> Hashtbl.replace cand_openers l.opener ()) cands;
      let closure_cands (start : Uses.lemma) =
        let seen = Hashtbl.create 64 in
        let acc = ref [] and stack = ref (deps_of start) in
        while !stack <> [] do
          match !stack with
          | [] -> ()
          | (l : Uses.lemma) :: tl ->
              stack := tl;
              if l.opener <> start.opener && not (Hashtbl.mem seen l.opener) then begin
                Hashtbl.replace seen l.opener ();
                acc := l :: !acc;
                stack := deps_of l @ !stack
              end
        done;
        !acc
        |> List.filter (fun (l : Uses.lemma) -> Hashtbl.mem cand_openers l.opener)
        |> List.sort (fun (a : Uses.lemma) (b : Uses.lemma) -> compare a.opener b.opener)
      in
      let order = Array.of_list lemmas in
      Rng.shuffle rng order;
      let del = Hashtbl.create 16 and chosen = ref [] in
      let target_deletions = spec.delete_count in
      let target_ablations = if spec.delete_count = None then spec.count else None in
      let use_prob = spec.delete_count = None && spec.count = None in
      let covered_count () =
        let s = Hashtbl.create 64 in
        List.iter
          (fun (l : Uses.lemma) ->
            List.iter (fun u -> if not (Hashtbl.mem del u) then Hashtbl.replace s u ()) l.users)
          !chosen;
        Hashtbl.length s
      in
      let reached () =
        match (target_deletions, target_ablations) with
        | Some nd, _ -> List.length !chosen >= nd
        | _, Some k -> covered_count () >= k
        | _ -> false
      in
      let not_chosen (l : Uses.lemma) = not (Hashtbl.mem del l.opener) in
      (* record [cor] as a corollary seed and its closure as the draw neighborhood *)
      let note_corollary (cor : Uses.lemma) =
        corollary_seeds := cor :: !corollary_seeds;
        List.iter
          (fun (l : Uses.lemma) -> Hashtbl.replace closure_openers l.opener ())
          (closure_cands cor)
      in
      let i = ref 0 and stop = ref false in
      while !i < Array.length order && not !stop do
        let cor = order.(!i) in
        incr i;
        if use_prob then begin
          let pool = List.filter not_chosen (closure_cands cor) in
          if pool <> [] then begin
            let before = List.length !chosen in
            List.iter
              (fun (l : Uses.lemma) ->
                if Rng.next_f64 rng < spec.prob then begin
                  Hashtbl.replace del l.opener ();
                  chosen := l :: !chosen
                end)
              pool;
            if List.length !chosen > before then note_corollary cor;
            stop := true
          end
        end
        else if reached () then stop := true
        else begin
          let pool = ref (List.filter not_chosen (closure_cands cor)) in
          let before = List.length !chosen in
          while !pool <> [] && not (reached ()) do
            let l = pick_weighted !pool in
            Hashtbl.replace del l.Uses.opener ();
            chosen := l :: !chosen;
            pool := List.filter (fun (c : Uses.lemma) -> c.Uses.opener <> l.Uses.opener) !pool
          done;
          if List.length !chosen > before then note_corollary cor
        end
      done;
      List.rev !chosen
    end
  in
  let selected =
    if spec.corollary then corollary_select ()
    else
    match spec.delete_count with
    | Some kd ->
        (* [--delete-lemmas K]: delete exactly K lemmas (weighted random draw), no
           matter how many ablations that forces. *)
        let chosen = ref [] and remaining = ref cands in
        let target = min kd (List.length cands) in
        while List.length !chosen < target && !remaining <> [] do
          let l = pick_weighted !remaining in
          chosen := l :: !chosen;
          remaining := without l !remaining
        done;
        List.rev !chosen
    | None -> (
        match spec.count with
        | None -> List.filter (fun _ -> Rng.next_f64 rng < spec.prob) cands
        | Some k when k <= 0 -> []
        | Some k ->
            let covered : (int, unit) Hashtbl.t = Hashtbl.create 64 in
            let chosen = ref [] and remaining = ref cands in
            while Hashtbl.length covered < k && !remaining <> [] do
              let l = pick_weighted !remaining in
              List.iter (fun u -> Hashtbl.replace covered u ()) l.users;
              chosen := l :: !chosen;
              remaining := without l !remaining
            done;
            List.rev !chosen)
  in
  let del = Hashtbl.create 16 in
  List.iter (fun (l : Uses.lemma) -> Hashtbl.replace del l.opener l) selected;
  (* distinct forced ablations: users of the selected lemmas that aren't themselves
     deleted, in file order. With --truncate + --count we ablate exactly the first
     [k] and cut the file after them; otherwise every user must be ablated (a
     dangling reference to the deleted name would not compile). *)
  let users_sorted =
    List.concat_map (fun (l : Uses.lemma) -> l.users) selected
    |> List.sort_uniq compare
    |> List.filter (fun u -> not (Hashtbl.mem del u))
  in
  let ablate_users =
    match (spec.truncate, spec.count) with
    | true, Some k -> List.filteri (fun idx _ -> idx < k) users_sorted
    | _ -> users_sorted
  in
  let to_ablate = Hashtbl.create 64 in
  List.iter (fun u -> Hashtbl.replace to_ablate u ()) ablate_users;
  (* whole-proof hole: statement + "Proof. Admitted." *)
  let whole_proof opener e =
    let b = Buffer.create 128 in
    Buffer.add_string b (Span.source spans.(opener));
    if opener + 1 < e then Buffer.add_string b (lead_of spans.(opener + 1));
    Buffer.add_string b "Proof. Admitted.";
    Buffer.contents b
  in
  (* does a span cite one of the deleted lemma names? (used by the leaf holer below) *)
  let deleted_names = Hashtbl.create 16 in
  List.iter (fun (l : Uses.lemma) -> Hashtbl.replace deleted_names l.Uses.name ()) selected;
  let cites (sp : Span.t) =
    List.exists (fun nm -> Hashtbl.mem deleted_names nm) (Uses.names_in sp.Span.content)
  in
  (* [--delete-lemmas-leaves]: hole the *smallest enclosing ablatable unit* that cites a
     deleted name. A Coq tactic only safely becomes [admit.] if doing so discharges the
     goal it focuses — true for a bullet segment ([- …]) or brace block ([{ … }]), each
     of which focuses one goal, but NOT for a tactic in a flat sequence (replacing it
     mid-sequence leaves the following tactics with "No such goal"). So we recurse into
     the proof's focus structure: a unit that cites is replaced wholesale with [admit.]
     (preferring the innermost citing unit), siblings are kept verbatim; a citation in a
     flat top-level sequence yields [None] => caller whole-proof-ablates. *)
  let leaf_proof opener e =
    let term = e - 1 in
    if term <= opener || not (Span.is_terminator spans.(term)) then None
    else begin
      (* matching [}] for the [{] at [j] (index of the Close), bounded by [term] *)
      let brace_end j =
        let d = ref 0 and k = ref (j + 1) and res = ref term in
        while !res = term && !k < term do
          (match spans.(!k).kind with
           | Span.Open -> incr d
           | Span.Close -> if !d = 0 then res := !k else decr d
           | _ -> ());
          if !res = term then incr k
        done;
        !res
      in
      (* exclusive end of the bullet segment opened at [j] with signature [sg] *)
      let bullet_end j sg =
        let d = ref 0 and k = ref (j + 1) and res = ref term in
        while !res = term && !k < term do
          let s = spans.(!k) in
          (match s.kind with
           | Span.Open -> incr d
           | Span.Close -> if !d = 0 then res := !k else decr d
           | Span.Bullet b -> if !d = 0 && b = sg then res := !k
           | Span.Sentence _ -> if !d = 0 && Span.is_terminator s then res := !k
           | _ -> ());
          if !res = term then incr k
        done;
        !res
      in
      let cites_range lo hi =
        let r = ref false in
        for k = lo to hi - 1 do if cites spans.(k) then r := true done;
        !r
      in
      (* render the tactics of one focused goal in [lo, hi): [`Admit] if a citation sits
         in this level's flat sequence (caller must admit the whole focus), else [`Keep t]
         with only the citing sub-units holed. *)
      let rec render_focused lo hi =
        (* a citation in a depth-0 sentence (outside any sub-unit) can't be isolated *)
        let direct = ref false and k = ref lo in
        while !k < hi do
          let s = spans.(!k) in
          (match s.kind with
           | Span.Open -> k := brace_end !k + 1
           | Span.Bullet sg -> k := bullet_end !k sg
           | Span.Sentence _ -> if cites s then direct := true; incr k
           | _ -> incr k)
        done;
        if !direct then `Admit
        else begin
          let b = Buffer.create 128 in
          let i = ref lo in
          while !i < hi do
            let s = spans.(!i) in
            match s.kind with
            | Span.Open ->
                let be = brace_end !i in
                if cites_range (!i + 1) be then begin
                  Buffer.add_string b (Span.source s);
                  (match render_focused (!i + 1) be with
                   | `Admit -> Buffer.add_string b " admit. "
                   | `Keep t -> Buffer.add_string b t);
                  Buffer.add_string b (Span.source spans.(be));
                  i := be + 1
                end
                else begin Buffer.add_string b (src_range !i (be + 1)); i := be + 1 end
            | Span.Bullet sg ->
                let bend = bullet_end !i sg in
                if cites_range (!i + 1) bend then begin
                  Buffer.add_string b (Span.source s);
                  (match render_focused (!i + 1) bend with
                   | `Admit -> Buffer.add_string b " admit."
                   | `Keep t -> Buffer.add_string b t);
                  i := bend
                end
                else begin Buffer.add_string b (src_range !i bend); i := bend end
            | _ -> Buffer.add_string b (Span.source s); incr i
          done;
          `Keep (Buffer.contents b)
        end
      in
      if not (cites_range (opener + 1) term) then None
      else
        match render_focused (opener + 1) term with
        | `Admit -> None (* flat top-level citation: fall back to whole-proof *)
        | `Keep body ->
            let b = Buffer.create 128 in
            Buffer.add_string b (Span.source spans.(opener));
            Buffer.add_string b body;
            Buffer.add_string b (lead_of spans.(term));
            Buffer.add_string b "Admitted.";
            Some (Buffer.contents b)
    end
  in
  let render_user opener e =
    if spec.delete_leaves then
      match leaf_proof opener e with Some t -> (t, "deleted-dep-leaves") | None -> (whole_proof opener e, "deleted-dep")
    else (whole_proof opener e, "deleted-dep")
  in
  (* Emit the challenge into [out] while recording, per top-level item, a [chal_segs]
     entry (offset into [out], is_closer, had_hole) for items PRESENT in the challenge
     and a [sol_segs] entry (offset into the original, is_closer, had_hole) for ALL
     items — so [--shrink-challenge]/[--shrink-solution] can independently trim each
     side to the first N holes (Shape.shrink ~count), and [--shrink-*-minimal] can keep
     only the holes' dependency closure (Slice.compute). A deleted lemma is absent from
     the challenge but present (original) in the solution. *)
  let out = Buffer.create 4096 in
  let holes = ref [] and deleted = ref [] and ablated = ref 0 in
  let last_admit_end = ref (-1) in
  let chal_segs = ref [] and sol_segs = ref [] and orig = ref 0 in
  let i = ref 0 in
  while !i < n do
    let s = spans.(!i) in
    if Span.is_goal s then begin
      let e = match Hashtbl.find_opt by_opener !i with Some l -> l.Uses.block_end | None -> !i + 1 in
      let item_src = src_range !i e in
      if Hashtbl.mem del !i then begin
        let l = Hashtbl.find by_opener !i in
        let body = src_range (!i + 1) e in
        deleted :=
          {
            d_name = l.Uses.name;
            d_text = item_src;
            d_fan_in = List.length l.Uses.users;
            d_metrics = Metrics.compute ~block:item_src ~body;
          }
          :: !deleted;
        orig := !orig + String.length item_src;
        sol_segs := (!orig, false, false) :: !sol_segs (* present in solution only *)
      end
      else if Hashtbl.mem to_ablate !i then begin
        let l = Hashtbl.find by_opener !i in
        let rendered, method_ = render_user !i e in
        Buffer.add_string out rendered;
        last_admit_end := Buffer.length out;
        let proof_text = src_range (!i + 1) e in
        holes :=
          {
            theorem_name = l.Uses.name;
            depth = 1;
            n_commands = 0;
            n_lines = n_lines proof_text;
            is_leaf = true;
            centrality = List.length l.Uses.users;
            method_;
            proof_text;
            metrics = hole_metrics proof_text;
          }
          :: !holes;
        incr ablated;
        chal_segs := (Buffer.length out, false, true) :: !chal_segs;
        orig := !orig + String.length item_src;
        sol_segs := (!orig, false, true) :: !sol_segs
      end
      else begin
        Buffer.add_string out item_src;
        chal_segs := (Buffer.length out, false, false) :: !chal_segs;
        orig := !orig + String.length item_src;
        sol_segs := (!orig, false, false) :: !sol_segs
      end;
      i := e
    end
    else begin
      let src = Span.source s in
      (* keep block openers too (not just [End] closers) so shrink can't drop a
         [Section]/[Module] header while keeping its [End] -> unbalanced file. *)
      let closer = Span.is_closer s || Span.is_opener s in
      Buffer.add_string out src;
      chal_segs := (Buffer.length out, closer, false) :: !chal_segs;
      orig := !orig + String.length src;
      sol_segs := (!orig, closer, false) :: !sol_segs;
      incr i
    end
  done;
  let raw = Buffer.contents out in
  let original = src_range 0 n in
  let chal_segs = Array.of_list (List.rev !chal_segs) in
  let sol_segs = Array.of_list (List.rev !sol_segs) in
  let text =
    if spec.truncate && !last_admit_end >= 0 then
      Shape.collapse_blank_lines (String.sub raw 0 !last_admit_end)
    else if spec.shrink_challenge_minimal then
      slice_delete spans spec ~by_opener ~del ~to_ablate ~solution:false
    else if spec.shrink_challenge then Shape.shrink ?count:spec.count raw chal_segs
    else Shape.collapse_blank_lines raw
  in
  let solution =
    if spec.shrink_solution_minimal then
      slice_delete spans spec ~by_opener ~del ~to_ablate ~solution:true
    else if spec.shrink_solution then Shape.shrink ?count:spec.count original sol_segs
    else original
  in
  let corollaries =
    List.rev_map
      (fun (c : Uses.lemma) ->
        let block = src_range c.opener c.block_end in
        let body = src_range (c.opener + 1) c.block_end in
        {
          co_name = c.Uses.name;
          co_fan_in = List.length c.Uses.users;
          co_metrics = Metrics.compute ~block ~body;
        })
      !corollary_seeds
  in
  {
    text;
    solution;
    total = total_eligible;
    ablated = !ablated;
    holes = List.rev !holes;
    deleted = List.rev !deleted;
    corollaries;
    closure_size = Hashtbl.length closure_openers;
  }

(* public entry. [centrality] maps a name to its corpus fan-in (0 if unused). *)
let ablate (spans : Span.t array) (spec : spec) (rng : Rng.t)
    (centrality : string -> int) : result =
  if spec.delete_lemmas then ablate_delete spans spec rng
  else
  match spec.count with
  | Some target ->
      let _, cands = walk_all spans spec centrality (fun _ -> false) in
      let m = Array.length cands in
      let selected = Hashtbl.create 16 in
      if m <= target then Array.iter (fun (i, _) -> Hashtbl.replace selected i ()) cands
      else if spec.by_centrality then begin
        let c = Array.copy cands in
        Array.sort (fun (i1, c1) (i2, c2) -> if c1 <> c2 then compare c2 c1 else compare i1 i2) c;
        for k = 0 to target - 1 do
          Hashtbl.replace selected (fst c.(k)) ()
        done
      end
      else begin
        let idx = Array.map fst cands in
        Rng.shuffle rng idx;
        for k = 0 to target - 1 do
          Hashtbl.replace selected idx.(k) ()
        done
      end;
      let r, _ = walk_all spans spec centrality (fun i -> Hashtbl.mem selected i) in
      { r with total = m }
  | None ->
      let p = spec.prob in
      fst (walk_all spans spec centrality (fun _ -> Rng.next_f64 rng < p))
