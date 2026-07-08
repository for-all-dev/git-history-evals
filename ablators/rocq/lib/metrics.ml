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
}

(* Coq keyword banks (see spec §2). *)
let subproof_kw = [ "assert"; "have"; "enough"; "cut"; "pose" ]

let case_splitters =
  [ "induction"; "destruct"; "case"; "inversion"; "elim"; "split"; "constructor"; "match" ]

let alternation_kw = [ "try"; "first"; "solve"; "repeat" ]

let n_lines (s : string) : int =
  if s = "" then 0
  else 1 + String.fold_left (fun a c -> if c = '\n' then a + 1 else a) 0 s

(* [block] is the whole source slice (statement + proof) used for line/char size;
   [body] is the proof body used for the tactic/branch heuristics. For holes the two
   are the same (only the proof body is available). *)
let compute ~(block : string) ~(body : string) : t =
  let subproofs = ref 0 and tactics = ref 0 and branches = ref 0 in
  List.iter
    (fun (tk : Token.t) ->
      if Token.is_ident tk then begin
        let s = tk.Token.source in
        if List.mem s subproof_kw then incr subproofs;
        if List.mem s case_splitters then incr branches;
        if List.mem s alternation_kw then incr branches
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
  ]
