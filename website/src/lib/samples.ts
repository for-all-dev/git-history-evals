import type { Lang } from '../ablators/types'

// Small, self-contained sample theories per prover — enough structure (several
// lemmas with cross-citations) that fan-in-weighted corollary deletion has
// something to chew on. Lifted from each ablator's standalone playground.

export const SAMPLES: Record<Lang, string> = {
  lean: `namespace Demo

/-- zero is a right identity for addition. -/
theorem add_zero (n : Nat) : n + 0 = n := by
  simp

theorem add_comm' (a b : Nat) : a + b = b + a := by
  omega

/-- a small corollary that leans on the two lemmas above. -/
theorem add_zero_comm (n : Nat) : 0 + n = n := by
  rw [add_comm']
  exact add_zero n

def classify (n : Nat) : Nat :=
  match n with
  | 0 => 100
  | k + 1 => k

theorem and_comm (p q : Prop) (h : p ∧ q) : q ∧ p := by
  apply And.intro
  · exact h.2
  · exact h.1

theorem double_nonneg (n : Nat) : 0 ≤ n + n := by
  have h : 0 ≤ n := Nat.zero_le n
  omega

end Demo
`,
  isabelle: `theory Demo
  imports Main
begin

lemma add_zero: "n + 0 = (n::nat)"
  by simp

lemma add_comm': "a + b = b + (a::nat)"
  by simp

lemma rev_rev: "rev (rev xs) = xs"
  apply (induct xs)
   apply simp
  apply simp
  done

lemma key: "x = (x::nat)" by simp
lemma uses_key: "a + 0 = (a::nat)" using key add_zero by simp

lemma conj_comm:
  assumes ab: "A \\<and> B"
  shows "B \\<and> A"
proof
  show "B" using ab by simp
  show "A" using ab by simp
qed

end
`,
  rocq: `Require Import Arith.

Lemma add_zero : forall n, n + 0 = n.
Proof.
  induction n.
  - reflexivity.
  - simpl. rewrite IHn. reflexivity.
Qed.

Lemma mul_one : forall n, n * 1 = n.
Proof. intros n. ring. Qed.

(* a corollary citing the lemmas above *)
Theorem add_zero_comm : forall n, 0 + n = n + 0.
Proof.
  intros n. rewrite add_zero. simpl. reflexivity.
Qed.

Lemma conj_comm : forall A B : Prop, A /\\ B -> B /\\ A.
Proof.
  intros A B [HA HB]. split.
  { exact HB. }
  { exact HA. }
Qed.

Lemma via_assert : forall n, n + 0 = n.
Proof.
  intros n. assert (H : n + 0 = n) by (rewrite add_zero; reflexivity).
  exact H.
Qed.

Definition double (n : nat) : nat := n + n.
`,
}
