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
  shrink_challenge : bool; (* drop challenge top-level goals after the last hole *)
  shrink_solution : bool; (* drop solution top-level goals after the last hole *)
  allow_defined : bool; (* ablate Defined-terminated proofs too (opacity risk) *)
  delete_lemmas : bool; (* delete eligible lemmas + ablate their users *)
  aggressive : bool; (* delete-lemmas: relax syntactic guards (BE; needs check-build) *)
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
    allow_defined = false;
    delete_lemmas = false;
    aggressive = false;
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
}

type result = {
  text : string; (* the ablated challenge *)
  solution : string; (* the original, optionally shrunk to match *)
  total : int;
  ablated : int;
  holes : hole list;
  deleted : (string * string) list; (* (name, original block) for --delete-lemmas *)
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
      let closer = Span.is_closer s in
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
    else if spec.shrink_challenge then Shape.shrink full chal_segs
    else full
  in
  let solution =
    if spec.shrink_solution then Shape.shrink original sol_segs else original
  in
  ( {
      text;
      solution;
      total = !total;
      ablated = !ablated;
      holes = List.rev !holes;
      deleted = [];
    },
    Array.of_list (List.rev !matches) )

(* ---------- --delete-lemmas: delete eligible lemmas + ablate their users ----- *)

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
  let selected =
    match spec.count with
    | Some k ->
        let arr = Array.of_list cands in
        if Array.length arr <= k then Array.to_list arr
        else if spec.by_centrality then begin
          Array.sort
            (fun (a : Uses.lemma) (b : Uses.lemma) ->
              let ca = nusers a and cb = nusers b in
              if ca <> cb then compare cb ca else compare a.opener b.opener)
            arr;
          Array.to_list (Array.sub arr 0 k)
        end
        else begin
          let idx = Array.init (Array.length arr) (fun i -> i) in
          Rng.shuffle rng idx;
          List.init k (fun i -> arr.(idx.(i)))
        end
    | None -> List.filter (fun _ -> Rng.next_f64 rng < spec.prob) cands
  in
  let del = Hashtbl.create 16 in
  List.iter (fun (l : Uses.lemma) -> Hashtbl.replace del l.opener l) selected;
  let users = Hashtbl.create 64 in
  List.iter
    (fun (l : Uses.lemma) ->
      List.iter (fun u -> if not (Hashtbl.mem del u) then Hashtbl.replace users u ()) l.users)
    selected;
  let out = Buffer.create 4096 in
  let holes = ref [] and deleted = ref [] and ablated = ref 0 in
  let i = ref 0 in
  while !i < n do
    let s = spans.(!i) in
    if Span.is_goal s then begin
      let e = match Hashtbl.find_opt by_opener !i with Some l -> l.Uses.block_end | None -> !i + 1 in
      if Hashtbl.mem del !i then begin
        let l = Hashtbl.find by_opener !i in
        deleted := (l.Uses.name, src_range !i e) :: !deleted;
        i := e
      end
      else if Hashtbl.mem users !i then begin
        let l = Hashtbl.find by_opener !i in
        Buffer.add_string out (Span.source s);
        if !i + 1 < e then Buffer.add_string out (lead_of spans.(!i + 1));
        Buffer.add_string out "Proof. Admitted.";
        let proof_text = src_range (!i + 1) e in
        holes :=
          {
            theorem_name = l.Uses.name;
            depth = 1;
            n_commands = 0;
            n_lines = n_lines proof_text;
            is_leaf = true;
            centrality = List.length l.Uses.users;
            method_ = "deleted-dep";
            proof_text;
          }
          :: !holes;
        incr ablated;
        i := e
      end
      else begin
        Buffer.add_string out (src_range !i e);
        i := e
      end
    end
    else begin
      Buffer.add_string out (Span.source s);
      incr i
    end
  done;
  {
    text = Shape.collapse_blank_lines (Buffer.contents out);
    solution = src_range 0 n;
    total = total_eligible;
    ablated = !ablated;
    holes = List.rev !holes;
    deleted = List.rev !deleted;
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
