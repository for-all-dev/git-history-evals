(* Single-file usage analysis for [--delete-lemmas]. For each top-level goal we
   record the span range of its whole block, whether it is `Qed`-opaque, the
   names cited in its proof body, and — crucially — whether the lemma's name is
   ever used *outside a proof body* (a statement / definition / notation / hint),
   which would make deleting it unsafe.

   Correct-by-construction: a lemma is deletable only when every occurrence of
   its name outside its own block sits inside an ablatable proof body, so
   deleting it + whole-proof-ablating its users leaves a well-formed file. (In
   Coq, an attributed decl like `#[…] Lemma` is not even classified as a goal, so
   automation-registered lemmas are excluded for free; standalone `Hint …`/
   `Existing Instance …` commands name the lemma and count as non-proof uses.) *)

type lemma = {
  name : string;
  opener : int; (* span index of the goal opener (statement) *)
  block_end : int; (* span index just past the terminator *)
  qed : bool; (* terminator is `Qed` (opaque) *)
  users : int list; (* opener indices of in-file proofs citing this lemma *)
  stmt_names : string list; (* names referenced in the statement (for slice closure) *)
  body_names : string list; (* names cited in the proof body (for slice closure) *)
  eligible : bool;
}

let min_name_len = 3

(* every ident in [content], plus the last component of a qualified name *)
let names_in (content : Token.t list) : string list =
  List.concat_map
    (fun (t : Token.t) ->
      if Token.is_ident t then
        match String.rindex_opt t.source '.' with
        | Some i when i + 1 < String.length t.source ->
            [ t.source; String.sub t.source (i + 1) (String.length t.source - i - 1) ]
        | _ -> [ t.source ]
      else [])
    content

type goal = {
  g_opener : int;
  g_end : int;
  g_name : string;
  g_qed : bool;
  g_cited : (string, unit) Hashtbl.t; (* names cited in the proof body *)
}

(* [aggressive] relaxes the `Qed`-opaque requirement (BE only; the caller must
   validate with --check-build). The non-proof-use guard (a) is never relaxed —
   a use in a statement/definition can't be repaired by ablation. *)
let analyze ?(aggressive = false) (spans : Span.t array) : lemma list =
  let n = Array.length spans in
  let goals = ref [] in
  (* non-proof occurrences: (name, span-index) for statements / non-goal spans *)
  let nonproof = ref [] in
  let add_nonproof j =
    List.iter (fun nm -> nonproof := (nm, j) :: !nonproof) (names_in spans.(j).content)
  in
  let i = ref 0 in
  while !i < n do
    if Span.is_goal spans.(!i) then begin
      let g = !i in
      let nm = Span.name spans.(g) in
      add_nonproof g; (* the statement is non-proof context *)
      incr i;
      let depth = ref 1 in
      let cited = Hashtbl.create 16 in
      let qed = ref false in
      let stop = ref false in
      while (not !stop) && !i < n do
        let s = spans.(!i) in
        if Span.is_goal s && !depth = 1 then stop := true (* malformed: leave for outer *)
        else if Span.is_terminator s && !depth = 1 then begin
          qed := Span.head s = "Qed";
          incr i;
          stop := true
        end
        else begin
          List.iter (fun nm -> Hashtbl.replace cited nm ()) (names_in s.content);
          (match s.kind with Span.Open -> incr depth | Span.Close -> decr depth | _ -> ());
          incr i
        end
      done;
      goals := { g_opener = g; g_end = !i; g_name = nm; g_qed = !qed; g_cited = cited } :: !goals
    end
    else begin
      add_nonproof !i;
      incr i
    end
  done;
  let goals = List.rev !goals in
  let nonproof = !nonproof in
  List.map
    (fun gl ->
      let name = gl.g_name in
      let users =
        List.filter_map
          (fun g' -> if g'.g_opener <> gl.g_opener && Hashtbl.mem g'.g_cited name then Some g'.g_opener else None)
          goals
      in
      (* (a): every non-proof occurrence of [name] is within this lemma's block *)
      let only_in_proofs =
        List.for_all
          (fun (nm, j) -> nm <> name || (j >= gl.g_opener && j < gl.g_end))
          nonproof
      in
      let eligible =
        (gl.g_qed || aggressive) && String.length name >= min_name_len && users <> []
        && only_in_proofs
      in
      let stmt_names = names_in spans.(gl.g_opener).content in
      let body_names = Hashtbl.fold (fun k () acc -> k :: acc) gl.g_cited [] in
      { name; opener = gl.g_opener; block_end = gl.g_end; qed = gl.g_qed; users;
        stmt_names; body_names; eligible })
    goals
