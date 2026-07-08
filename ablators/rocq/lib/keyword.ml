(* Coq/Rocq vernacular keyword classification. Coq's command vocabulary is fixed
   by the language, so we enumerate it here rather than loading a per-session
   table from a prover (unlike Isabelle, whose syntax is user-extensible). *)

(* span-kind constants (analogous to Isabelle's keyword "kinds") *)
let k_goal = "thy_goal" (* opens an interactive proof: Theorem, Lemma, ... *)
let k_proof = "proof" (* Proof. / Proof using ... / Proof with ... *)
let k_qed = "qed" (* Qed / Defined / Save — successful close *)
let k_admitted = "admitted" (* Admitted *)
let k_abort = "abort" (* Abort / Abort All *)

let mem (xs : string list) (s : string) = List.mem s xs

(* statements that reliably enter interactive proof mode on their own. *)
let goal_openers =
  [ "Theorem"; "Lemma"; "Corollary"; "Proposition"; "Remark"; "Fact";
    "Property"; "Example"; "Goal" ]

(* declaration heads that open a proof ONLY when no [:=] body is present and the
   next sentence is [Proof] (resolved by look-ahead in Span). *)
let conditional_openers =
  [ "Definition"; "Fixpoint"; "CoFixpoint"; "Let"; "Instance"; "Theorem" ]

let terminators_qed = [ "Qed"; "Defined"; "Save" ]
let terminator_admitted = "Admitted"
let terminators_abort = [ "Abort" ]

let is_goal_opener s = mem goal_openers s
let is_conditional_opener s = mem conditional_openers s
let is_proof s = s = "Proof"

(* classify a terminator head, or None *)
let terminator_kind s =
  if mem terminators_qed s then Some k_qed
  else if s = terminator_admitted then Some k_admitted
  else if mem terminators_abort s then Some k_abort
  else None

let is_terminator s = terminator_kind s <> None
