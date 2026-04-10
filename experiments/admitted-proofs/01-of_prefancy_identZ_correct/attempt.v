Require Import Coq.ZArith.ZArith Coq.micromega.Lia.
Require Import Coq.derive.Derive.
Require Import Coq.Bool.Bool.
Require Import Coq.Strings.String.
Require Import Coq.Lists.List.
Require Crypto.Util.Strings.String.
Require Import Crypto.Util.Strings.Decimal.
Require Import Crypto.Util.Strings.HexString.
Require Import QArith.QArith_base QArith.Qround Crypto.Util.QUtil.
Require Import Crypto.Algebra.Ring Crypto.Util.Decidable.Bool2Prop.
Require Import Crypto.Algebra.Ring.
Require Import Crypto.Algebra.SubsetoidRing.
Require Import Crypto.Util.ZRange.
Require Import Crypto.Util.ListUtil.FoldBool.
Require Import Crypto.Util.LetIn.
Require Import Crypto.Arithmetic.PrimeFieldTheorems.
Require Import Crypto.Util.ZUtil.Tactics.LtbToLt.
Require Import Crypto.Util.ZUtil.Tactics.PullPush.Modulo.
Require Import Crypto.Util.ZUtil.Tactics.DivModToQuotRem.
Require Import Crypto.Util.ZUtil.Tactics.ZeroBounds.
Require Import Crypto.Util.Tactics.SplitInContext.
Require Import Crypto.Util.Tactics.SubstEvars.
Require Import Crypto.Util.Tactics.DestructHead.
Require Import Crypto.Util.Tuple.
Require Import Crypto.Util.ListUtil Coq.Lists.List.
Require Import Crypto.Util.Equality.
Require Import Crypto.Util.Tactics.GetGoal.
Require Import Crypto.Arithmetic.BarrettReduction.Generalized.
Require Import Crypto.Util.Tactics.UniquePose.
Require Import Crypto.Util.ZUtil.Rshi.
Require Import Crypto.Util.Option.
Require Import Crypto.Util.Tactics.BreakMatch.
Require Import Crypto.Util.Tactics.SpecializeBy.
Require Import Crypto.Util.ZUtil.Zselect.
Require Import Crypto.Util.ZUtil.AddModulo.
Require Import Crypto.Util.ZUtil.CC.
Require Import Crypto.Util.ZUtil.Modulo.
Require Import Crypto.Util.ZUtil.Notations.
Require Import Crypto.Util.ZUtil.Tactics.RewriteModSmall.
Require Import Crypto.Util.ZUtil.Definitions.
Require Import Crypto.Util.ZUtil.EquivModulo.
Require Import Crypto.Util.ZUtil.Tactics.SplitMinMax.
Require Import Crypto.Arithmetic.MontgomeryReduction.Definition.
Require Import Crypto.Arithmetic.MontgomeryReduction.Proofs.
Require Import Crypto.Util.ErrorT.
Require Import Crypto.Util.Strings.Show.
Require Import Crypto.Util.ZRange.Operations.
Require Import Crypto.Util.ZRange.BasicLemmas.
Require Import Crypto.Util.ZRange.Show.
Require Import Crypto.Experiments.NewPipeline.Arithmetic.
Require Crypto.Experiments.NewPipeline.Language.
Require Crypto.Experiments.NewPipeline.UnderLets.
Require Crypto.Experiments.NewPipeline.AbstractInterpretation.
Require Crypto.Experiments.NewPipeline.AbstractInterpretationProofs.
Require Crypto.Experiments.NewPipeline.Rewriter.
Require Crypto.Experiments.NewPipeline.MiscCompilerPasses.
Require Crypto.Experiments.NewPipeline.CStringification.
Require Export Crypto.Experiments.NewPipeline.Toplevel1.
Require Import Crypto.Util.Notations.
Import ListNotations. Local Open Scope Z_scope.

Import Associational Positional.

Import
  Crypto.Experiments.NewPipeline.Language
  Crypto.Experiments.NewPipeline.UnderLets
  Crypto.Experiments.NewPipeline.AbstractInterpretation
  Crypto.Experiments.NewPipeline.AbstractInterpretationProofs
  Crypto.Experiments.NewPipeline.Rewriter
  Crypto.Experiments.NewPipeline.MiscCompilerPasses
  Crypto.Experiments.NewPipeline.CStringification.

Import
  Language.Compilers
  UnderLets.Compilers
  AbstractInterpretation.Compilers
  AbstractInterpretationProofs.Compilers
  Rewriter.Compilers
  MiscCompilerPasses.Compilers
  CStringification.Compilers.

Import Compilers.defaults.
Local Coercion Z.of_nat : nat >-> Z.
Local Coercion QArith_base.inject_Z : Z >-> Q.
(* Notation "x" := (expr.Var x) (only printing, at level 9) : expr_scope. *)

Import UnsaturatedSolinas.

Module X25519_64.
  Definition n := 5%nat.
  Definition s := 2^255.
  Definition c := [(1, 19)].
  Definition machine_wordsize := 64.
  Local Notation tight_bounds := (tight_bounds n s c).
  Local Notation loose_bounds := (loose_bounds n s c).
  Local Notation prime_bound := (prime_bound s c).

  Derive base_51_relax
         SuchThat (rrelax_correctT n s c machine_wordsize base_51_relax)
         As base_51_relax_correct.
  Proof. Time solve_rrelax machine_wordsize. Time Qed.
  Derive base_51_carry_mul
         SuchThat (rcarry_mul_correctT n s c machine_wordsize base_51_carry_mul)
         As base_51_carry_mul_correct.
  Proof. Time solve_rcarry_mul machine_wordsize. Time Qed.
  Derive base_51_carry
         SuchThat (rcarry_correctT n s c machine_wordsize base_51_carry)
         As base_51_carry_correct.
  Proof. Time solve_rcarry machine_wordsize. Time Qed.
  Derive base_51_add
         SuchThat (radd_correctT n s c machine_wordsize base_51_add)
         As base_51_add_correct.
  Proof. Time solve_radd machine_wordsize. Time Qed.
  Derive base_51_sub
         SuchThat (rsub_correctT n s c machine_wordsize base_51_sub)
         As base_51_sub_correct.
  Proof. Time solve_rsub machine_wordsize. Time Qed.
  Derive base_51_opp
         SuchThat (ropp_correctT n s c machine_wordsize base_51_opp)
         As base_51_opp_correct.
  Proof. Time solve_ropp machine_wordsize. Time Qed.
  Derive base_51_to_bytes
         SuchThat (rto_bytes_correctT n s c machine_wordsize base_51_to_bytes)
         As base_51_to_bytes_correct.
  Proof. Time solve_rto_bytes machine_wordsize. Time Qed.
  Derive base_51_from_bytes
         SuchThat (rfrom_bytes_correctT n s c machine_wordsize base_51_from_bytes)
         As base_51_from_bytes_correct.
  Proof. Time solve_rfrom_bytes machine_wordsize. Time Qed.
  Derive base_51_encode
         SuchThat (rencode_correctT n s c machine_wordsize base_51_encode)
         As base_51_encode_correct.
  Proof. Time solve_rencode machine_wordsize. Time Qed.
  Derive base_51_zero
         SuchThat (rzero_correctT n s c machine_wordsize base_51_zero)
         As base_51_zero_correct.
  Proof. Time solve_rzero machine_wordsize. Time Qed.
  Derive base_51_one
         SuchThat (rone_correctT n s c machine_wordsize base_51_one)
         As base_51_one_correct.
  Proof. Time solve_rone machine_wordsize. Time Qed.
  Lemma base_51_curve_good
    : check_args n s c machine_wordsize (Success tt) = Success tt.
  Proof. vm_compute; reflexivity. Qed.

  Definition base_51_good : GoodT n s c machine_wordsize
    := Good n s c machine_wordsize
            base_51_curve_good
            base_51_carry_mul_correct
            base_51_carry_correct
            base_51_relax_correct
            base_51_add_correct
            base_51_sub_correct
            base_51_opp_correct
            base_51_zero_correct
            base_51_one_correct
            base_51_encode_correct
            base_51_to_bytes_correct
            base_51_from_bytes_correct.

  Print Assumptions base_51_good.
  Import PrintingNotations.
  Set Printing Width 80.
  Open Scope string_scope.
  Local Notation prime_bytes_bounds := (prime_bytes_bounds n s c).
  Print base_51_to_bytes.
  Print base_51_carry_mul.
(*base_51_carry_mul =
fun var : type -> Type =>
(λ x x0 : var (type.base (base.type.list (base.type.type_base base.type.Z))),
 expr_let x1 := (uint64)(x[[0]]) *₁₂₈ (uint64)(x0[[0]]) +₁₂₈
                ((uint64)(x[[1]]) *₁₂₈ ((uint64)(x0[[4]]) *₆₄ 19) +₁₂₈
                 ((uint64)(x[[2]]) *₁₂₈ ((uint64)(x0[[3]]) *₆₄ 19) +₁₂₈
                  ((uint64)(x[[3]]) *₁₂₈ ((uint64)(x0[[2]]) *₆₄ 19) +₁₂₈
                   (uint64)(x[[4]]) *₁₂₈ ((uint64)(x0[[1]]) *₆₄ 19)))) in
 expr_let x2 := (uint64)(x1 >> 51) +₁₂₈
                ((uint64)(x[[0]]) *₁₂₈ (uint64)(x0[[1]]) +₁₂₈
                 ((uint64)(x[[1]]) *₁₂₈ (uint64)(x0[[0]]) +₁₂₈
                  ((uint64)(x[[2]]) *₁₂₈ ((uint64)(x0[[4]]) *₆₄ 19) +₁₂₈
                   ((uint64)(x[[3]]) *₁₂₈ ((uint64)(x0[[3]]) *₆₄ 19) +₁₂₈
                    (uint64)(x[[4]]) *₁₂₈ ((uint64)(x0[[2]]) *₆₄ 19))))) in
 expr_let x3 := (uint64)(x2 >> 51) +₁₂₈
                ((uint64)(x[[0]]) *₁₂₈ (uint64)(x0[[2]]) +₁₂₈
                 ((uint64)(x[[1]]) *₁₂₈ (uint64)(x0[[1]]) +₁₂₈
                  ((uint64)(x[[2]]) *₁₂₈ (uint64)(x0[[0]]) +₁₂₈
                   ((uint64)(x[[3]]) *₁₂₈ ((uint64)(x0[[4]]) *₆₄ 19) +₁₂₈
                    (uint64)(x[[4]]) *₁₂₈ ((uint64)(x0[[3]]) *₆₄ 19))))) in
 expr_let x4 := (uint64)(x3 >> 51) +₁₂₈
                ((uint64)(x[[0]]) *₁₂₈ (uint64)(x0[[3]]) +₁₂₈
                 ((uint64)(x[[1]]) *₁₂₈ (uint64)(x0[[2]]) +₁₂₈
                  ((uint64)(x[[2]]) *₁₂₈ (uint64)(x0[[1]]) +₁₂₈
                   ((uint64)(x[[3]]) *₁₂₈ (uint64)(x0[[0]]) +₁₂₈
                    (uint64)(x[[4]]) *₁₂₈ ((uint64)(x0[[4]]) *₆₄ 19))))) in
 expr_let x5 := (uint64)(x4 >> 51) +₁₂₈
                ((uint64)(x[[0]]) *₁₂₈ (uint64)(x0[[4]]) +₁₂₈
                 ((uint64)(x[[1]]) *₁₂₈ (uint64)(x0[[3]]) +₁₂₈
                  ((uint64)(x[[2]]) *₁₂₈ (uint64)(x0[[2]]) +₁₂₈
                   ((uint64)(x[[3]]) *₁₂₈ (uint64)(x0[[1]]) +₁₂₈
                    (uint64)(x[[4]]) *₁₂₈ (uint64)(x0[[0]]))))) in
 expr_let x6 := ((uint64)(x1) & 2251799813685247) +₆₄ (uint64)(x5 >> 51) *₆₄ 19 in
 expr_let x7 := (uint64)(x6 >> 51) +₆₄ ((uint64)(x2) & 2251799813685247) in
 expr_let x8 := ((uint64)(x6) & 2251799813685247) in
 expr_let x9 := ((uint64)(x7) & 2