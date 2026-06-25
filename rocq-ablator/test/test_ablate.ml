(* Unit tests for the Rocq ablator. Run with `dune test`. *)

open Rocq_ablator

let failures = ref 0

let check name cond =
  if cond then Printf.printf "ok   %s\n" name
  else begin
    Printf.printf "FAIL %s\n" name;
    incr failures
  end

let contains hay needle =
  let nl = String.length needle and hl = String.length hay in
  let rec go i = i + nl <= hl && (String.sub hay i nl = needle || go (i + 1)) in
  nl = 0 || go 0

let zero (_ : string) = 0

let ablate_text ?(spec = Ablate.default_spec) ?(seed = 0) text =
  let spans = Span.parse_spans text in
  (Ablate.ablate spans spec (Ablate.Rng.make (Int64.of_int seed)) zero).text

let ablate_full ?(spec = Ablate.default_spec) ?(seed = 0) text =
  let spans = Span.parse_spans text in
  Ablate.ablate spans spec (Ablate.Rng.make (Int64.of_int seed)) zero

(* ---- lexer ---- *)

let () =
  let samples =
    [ "Lemma a : 1 = 1.\nProof. reflexivity. Qed.\n";
      "Nat.add x.(field) 3.14 .. \"a string with .\".\n";
      "(* a (* nested *) comment with \"*)\" string *) Definition d := 0.\n";
      "Notation \"[ x ; .. ; y ]\" := (cons x .. (cons y nil) ..).\n";
      "" ]
  in
  List.iteri
    (fun i s ->
      let toks = Tokenize.tokenize s in
      check (Printf.sprintf "tokenize round-trip #%d" i) (Token.implode toks = s))
    samples

let () =
  let toks t = Tokenize.tokenize t in
  let one_ident s =
    match List.filter Token.is_proper (toks s) with [ t ] -> Token.is_ident t | _ -> false
  in
  check "qualified name is one ident" (one_ident "Nat.add_comm");
  let ts = toks "add." in
  check "trailing dot is a Dot token"
    (match List.filter Token.is_proper ts with
     | [ a; b ] -> Token.is_ident a && Token.is_dot b
     | _ -> false);
  let ts = toks "3.14" in
  check "3.14 is one number"
    (match List.filter Token.is_proper ts with [ t ] -> Token.is_number t | _ -> false)

(* ---- round-trip identity (prob 0) ---- *)

let demo =
  "Require Import Arith.\n\n\
   Lemma add_zero : forall n, n + 0 = n.\n\
   Proof.\n  intros n. induction n.\n  - reflexivity.\n  - simpl. rewrite IHn. reflexivity.\nQed.\n\n\
   Definition double (n : nat) : nat := n + n.\n\n\
   Theorem t : 1 = 1. Proof. reflexivity. Qed.\n"

let () =
  check "prob 0 is identity" (ablate_text ~spec:{ Ablate.default_spec with prob = 0.0 } demo = demo)

(* ---- top-level ablation ---- *)

let () =
  let r = ablate_full ~spec:{ Ablate.default_spec with prob = 1.0 } demo in
  check "top-level: statements kept" (contains r.text "Lemma add_zero : forall n, n + 0 = n.");
  check "top-level: proof -> Proof. Admitted." (contains r.text "Proof. Admitted.");
  check "top-level: induction body gone" (not (contains r.text "induction n"));
  check "top-level: Definition body kept" (contains r.text "Definition double (n : nat) : nat := n + n.");
  check "top-level: ablated == total" (r.ablated = r.total && r.total = 2)

(* ---- nested: bullets + Qed->Admitted ---- *)

let () =
  let spec = { Ablate.default_spec with prob = 1.0; min_depth = 2; max_depth = Ablate.inf } in
  let r = ablate_full ~spec demo in
  check "nested: bullets -> admit" (contains r.text "- admit.");
  check "nested: Qed rewritten to Admitted" (contains r.text "Admitted.");
  check "nested: no leftover Qed in add_zero"
    (not (contains r.text "reflexivity.\nQed."));
  check "nested: top-level statement preserved" (contains r.text "Lemma add_zero")

(* ---- nested: brace blocks ---- *)

let () =
  let src = "Lemma c : True /\\ True.\nProof. split.\n{ exact I. }\n{ exact I. }\nQed.\n" in
  let spec = { Ablate.default_spec with prob = 1.0; min_depth = 2; max_depth = Ablate.inf } in
  let t = ablate_text ~spec src in
  check "brace block -> { admit. }" (contains t "{ admit. }");
  check "brace: Qed -> Admitted" (contains t "Admitted." && not (contains t "Qed."))

(* ---- nested: by-clause ---- *)

let () =
  let src = "Lemma b : True.\nProof. assert (H : True) by exact I. exact H. Qed.\n" in
  let spec = { Ablate.default_spec with prob = 1.0; min_depth = 2; max_depth = Ablate.inf } in
  let t = ablate_text ~spec src in
  check "by-clause -> by admit" (contains t "by admit.");
  check "by-clause: Qed -> Admitted" (contains t "Admitted.")

(* ---- comment / notation safety ---- *)

let () =
  let src = "Lemma cmt : True. Proof. (* Qed. here *) exact I. Qed.\n" in
  let r = ablate_full ~spec:{ Ablate.default_spec with prob = 1.0 } src in
  (* the comment's "Qed." must NOT split the proof: it ablates as one unit *)
  check "comment Qed. not a terminator" (r.ablated = 1 && r.total = 1);
  check "comment: whole proof gone" (not (contains r.text "exact I"));
  (* statement count preserved through ablation *)
  let goals s =
    Array.fold_left (fun a sp -> if Span.is_goal sp then a + 1 else a) 0 (Span.parse_spans s)
  in
  check "goal count preserved" (goals src = goals r.text)

(* ---- Defined excluded by default, included with allow_defined ---- *)

let () =
  let src = "Definition f : nat. Proof. exact 0. Defined.\n" in
  let t0 = ablate_text ~spec:{ Ablate.default_spec with prob = 1.0 } src in
  check "Defined excluded by default" (not (contains t0 "Admitted."));
  let t1 = ablate_text ~spec:{ Ablate.default_spec with prob = 1.0; allow_defined = true } src in
  check "Defined ablated with --allow-defined" (contains t1 "Proof. Admitted.")

(* ---- count mode is deterministic & bounded ---- *)

let () =
  let spec = { Ablate.default_spec with count = Some 1 } in
  let r = ablate_full ~spec demo in
  check "count mode ablates exactly 1" (r.ablated = 1);
  check "count mode total = #candidates" (r.total = 2)

(* ---- shrink-challenge / shrink-solution ---- *)

let () =
  (* ablate the single theorem `a`; everything after it must be dropped on the
     shrunk side EXCEPT the structural closer `End M.`. *)
  (* `later` uses Defined (excluded by default), so --all ablates only `a` and
     `a` is the last ablation. *)
  let src =
    "Module M.\n\
     Lemma a : 1 = 1.\nProof. reflexivity. Qed.\n\n\
     Definition d := 5.\n\n\
     Lemma later : 2 = 2.\nProof. reflexivity. Defined.\n\n\
     End M.\n"
  in
  let spec = { Ablate.default_spec with prob = 1.0 } in
  let r0 = ablate_full ~spec src in
  check "no shrink: challenge keeps trailing decls" (contains r0.text "Definition d" && contains r0.text "Lemma later");
  check "no shrink: solution is full original" (r0.solution = src);
  let rc = ablate_full ~spec:{ spec with shrink_challenge = true } src in
  check "shrink-challenge: drops trailing Definition" (not (contains rc.text "Definition d"));
  check "shrink-challenge: drops trailing theorem" (not (contains rc.text "Lemma later"));
  check "shrink-challenge: keeps closer End M." (contains rc.text "End M.");
  check "shrink-challenge: keeps ablated theorem a" (contains rc.text "Lemma a");
  check "shrink-challenge: solution untouched" (rc.solution = src);
  let rs = ablate_full ~spec:{ spec with shrink_solution = true } src in
  check "shrink-solution: solution drops trailing decls" (not (contains rs.solution "Definition d") && not (contains rs.solution "Lemma later"));
  check "shrink-solution: solution keeps closer + ablated proof" (contains rs.solution "End M." && contains rs.solution "reflexivity");
  check "shrink-solution: challenge untouched" (contains rs.text "Definition d" && contains rs.text "Lemma later")

(* ---- --delete-lemmas ---- *)

let () =
  (* `helper` is used by `main`; `unused_pub` is used by nobody; `cited_in_stmt`
     is referenced in a later statement (non-proof use). *)
  let src =
    "Lemma helper : 1 = 1.\nProof. reflexivity. Qed.\n\n\
     Lemma unused_pub : 2 = 2.\nProof. reflexivity. Qed.\n\n\
     Definition cited_in_stmt : nat := 0.\n\n\
     Lemma main : 1 = 1.\nProof. apply helper. Qed.\n\n\
     Lemma about : cited_in_stmt = cited_in_stmt.\nProof. reflexivity. Qed.\n"
  in
  let spec = { Ablate.default_spec with delete_lemmas = true; prob = 1.0 } in
  let r = ablate_full ~spec src in
  let deleted_names = List.map fst r.deleted in
  check "delete: helper (used in a proof) is deleted" (List.mem "helper" deleted_names);
  check "delete: unused_pub (no user) is NOT deleted" (not (List.mem "unused_pub" deleted_names));
  check "delete: helper's statement is gone from challenge" (not (contains r.text "Lemma helper"));
  check "delete: user `main`'s statement is kept" (contains r.text "Lemma main : 1 = 1.");
  check "delete: user `main`'s proof is holed" (contains r.text "Proof. Admitted." && not (contains r.text "apply helper"));
  check "delete: solution is the full original (helper present)" (r.solution = src);
  check "delete: unused_pub kept verbatim in challenge" (contains r.text "Lemma unused_pub")

(* a lemma whose name appears in a later statement is ineligible (deleting it
   would dangle the statement) *)
let () =
  let src =
    "Lemma key : 1 = 1.\nProof. reflexivity. Qed.\n\n\
     Lemma uses_key_in_proof : 1 = 1.\nProof. exact key. Qed.\n\n\
     Definition d := key.\n"
  in
  let spec = { Ablate.default_spec with delete_lemmas = true; prob = 1.0 } in
  let r = ablate_full ~spec src in
  check "delete: lemma used in a Definition is NOT deleted"
    (not (List.mem "key" (List.map fst r.deleted)) && contains r.text "Lemma key")

(* ---- sha1 known-answer (task_id must be stable across targets) ---- *)

let () =
  check "sha1(\"\")" (Sha1.hex "" = "da39a3ee5e6b4b0d3255bfef95601890afd80709");
  check "sha1(\"abc\")" (Sha1.hex "abc" = "a9993e364706816aba3e25717850c26c9cd0d89d")

(* ---- solution_diff (self-rolled unified diff) ---- *)

let () =
  (* apply(challenge, unified(challenge, solution)) must recover the solution *)
  let rt label r =
    let d = Diff.unified r.Ablate.text r.Ablate.solution in
    check (label ^ ": round-trip") (Diff.apply r.text d = r.solution)
  in
  rt "prob1" (ablate_full ~spec:{ Ablate.default_spec with prob = 1.0 } demo);
  rt "L2" (ablate_full ~spec:{ Ablate.default_spec with prob = 1.0; min_depth = 2; max_depth = Ablate.inf } demo);
  let del_src =
    "Lemma helper : 1 = 1.\nProof. reflexivity. Qed.\n\n\
     Lemma main : 1 = 1.\nProof. apply helper. Qed.\n"
  in
  rt "delete" (ablate_full ~spec:{ Ablate.default_spec with prob = 1.0; delete_lemmas = true } del_src);
  check "prob0: empty diff" (Diff.unified demo demo = "");
  check "apply empty diff = identity" (Diff.apply demo "" = demo);
  (* scattered changes round-trip through multiple hunks *)
  let a = "l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\nl9\nl10\n" in
  let b = "l1\nX2\nl3\nl4\nl5\nl6\nl7\nl8\nY9\nl10\n" in
  check "diff: scattered round-trip" (Diff.apply a (Diff.unified a b) = b);
  check "diff: no-trailing-newline round-trip" (Diff.apply "a\nb" (Diff.unified "a\nb" "a\nc") = "a\nc");
  (* the point of the feature: a localized change in a big file gives a tiny diff *)
  let big = String.concat "" (List.init 300 (fun i -> Printf.sprintf "Definition d%d : nat := %d.\n" i i)) in
  let sol = big ^ "Lemma foo : 1 = 1.\nProof. reflexivity. Qed.\n" in
  let chal = big ^ "Lemma foo : 1 = 1.\nProof. Admitted.\n" in
  let dd = Diff.unified chal sol in
  check "diff: large+localized round-trip" (Diff.apply chal dd = sol);
  check "diff: large+localized is tiny" (String.length dd < String.length sol / 4)

let () =
  if !failures = 0 then print_endline "\nALL TESTS PASSED"
  else begin
    Printf.printf "\n%d TEST(S) FAILED\n" !failures;
    exit 1
  end
