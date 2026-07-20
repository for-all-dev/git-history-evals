(* Proof-complexity metrics, computed inside the ablator so they are visible in the
   JSONL record (and any downstream website / difficulty classifier). These are
   deliberately simple, tokenizer-driven heuristics — informative features, not
   canonical semantics. See [docs/difficulty-features.md] §2 for the definitions,
   which the Lean/Rust/Scala ablators mirror for their own provers.

   Matches are whole identifier tokens only; occurrences inside comments or strings
   are ignored by construction, because [Tokenize.tokenize] classifies those bytes as
   [Comment]/[String] tokens rather than [Ident]. *)

type t = {
  n_lines : int;
  n_chars : int;
  n_subproofs : int; (* intermediate-assertion keywords: assert/have/enough/cut/pose *)
  n_tactics : int; (* atomic proof steps: sentence-terminating dots + ';' sequencing *)
  cyclomatic : int; (* 1 + #case-splitters + #alternation combinators *)
  (* What the proof DOES. Size/shape alone cannot tell a `by auto` one-liner from a 40-line
     induction with the same step count, and that distinction is most of what decides whether
     a model can re-derive the lemma. Mirrors the Lean/Isabelle banks for Coq's vocabulary. *)
  n_automation : int; (* closing/automation tactics: auto, lia, omega, congruence, … *)
  n_rewrites : int; (* rewriting/unfolding steps: rewrite, unfold, simpl, … *)
  n_structural : int; (* structural steps: induction, destruct, apply, exact, … *)
  automation_only : bool; (* EVERY step is automation: closable by a tactic call *)
  max_nesting : int; (* deepest bullet/brace nesting of the body *)
}

(* Coq keyword banks (see spec §2). *)
let subproof_kw = [ "assert"; "have"; "enough"; "cut"; "pose" ]

let case_splitters =
  [ "induction"; "destruct"; "case"; "inversion"; "elim"; "split"; "constructor"; "match" ]

let alternation_kw = [ "try"; "first"; "solve"; "repeat" ]

(* Closing/automation tactics: discharge a goal by search or decision procedure. *)
let automation_kw =
  [ "auto"; "eauto"; "tauto"; "lia"; "nia"; "lra"; "nra"; "omega"; "ring"; "field";
    "congruence"; "discriminate"; "trivial"; "reflexivity"; "assumption"; "easy";
    "firstorder"; "intuition"; "btauto"; "decide"; "now"; "done"; "crush"; "sauto" ]

(* Rewriting / unfolding steps: manipulate the goal without deciding it. *)
let rewrite_kw =
  [ "rewrite"; "unfold"; "simpl"; "cbn"; "cbv"; "change"; "subst"; "replace"; "fold";
    "setoid_rewrite"; "autorewrite"; "red"; "hnf" ]

(* Structural steps: introduce a proof skeleton the model must get right. *)
let structural_kw =
  [ "induction"; "destruct"; "inversion"; "apply"; "exact"; "refine"; "intro"; "intros";
    "exists"; "split"; "constructor"; "specialize"; "generalize"; "revert"; "elim"; "case" ]

let n_lines (s : string) : int =
  if s = "" then 0
  else 1 + String.fold_left (fun a c -> if c = '\n' then a + 1 else a) 0 s

(* [block] is the whole source slice (statement + proof) used for line/char size;
   [body] is the proof body used for the tactic/branch heuristics. For holes the two
   are the same (only the proof body is available). *)
let max_indent (s : string) : int =
  String.split_on_char '\n' s
  |> List.fold_left
       (fun acc line ->
         let rec ws i = if i < String.length line && line.[i] = ' ' then ws (i + 1) else i in
         let ind = ws 0 in
         if String.length line > ind then max acc ind else acc)
       0

let compute ~(block : string) ~(body : string) : t =
  let subproofs = ref 0 and tactics = ref 0 and branches = ref 0 in
  let automation = ref 0 and rewrites = ref 0 and structural = ref 0 in
  List.iter
    (fun (tk : Token.t) ->
      if Token.is_ident tk then begin
        let s = tk.Token.source in
        if List.mem s subproof_kw then incr subproofs;
        if List.mem s case_splitters then incr branches;
        if List.mem s alternation_kw then incr branches;
        if List.mem s automation_kw then incr automation;
        if List.mem s rewrite_kw then incr rewrites;
        if List.mem s structural_kw then incr structural
      end
      else if Token.is_dot tk then incr tactics
      else if Token.is_symbol tk then
        if tk.Token.source = ";" then incr tactics
        else if tk.Token.source = "||" then incr branches)
    (Tokenize.tokenize body);
  {
    n_lines = n_lines block;
    n_chars = String.length block;
    n_subproofs = !subproofs;
    n_tactics = !tactics;
    cyclomatic = 1 + !branches;
    n_automation = !automation;
    n_rewrites = !rewrites;
    n_structural = !structural;
    automation_only =
      !automation > 0 && !rewrites = 0 && !structural = 0 && !subproofs = 0 && !branches = 0;
    max_nesting = max_indent body;
  }

(* JSON fields for a metrics record, inlined flat into the enclosing object so the
   website / classifier sees plain keys. Callers that also carry a separate [n_lines]
   (holes already do) should not double-emit it. *)
let to_fields (m : t) : (string * Yojson.Safe.t) list =
  [
    ("n_lines", `Int m.n_lines);
    ("n_chars", `Int m.n_chars);
    ("n_subproofs", `Int m.n_subproofs);
    ("n_tactics", `Int m.n_tactics);
    ("cyclomatic", `Int m.cyclomatic);
    ("n_automation", `Int m.n_automation);
    ("n_rewrites", `Int m.n_rewrites);
    ("n_structural", `Int m.n_structural);
    ("automation_only", `Bool m.automation_only);
    ("max_nesting", `Int m.max_nesting);
  ]
