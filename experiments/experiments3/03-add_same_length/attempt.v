Require Import List.
Require Import ZArith.ZArith ZArith.Zdiv.
  Require Import Omega.

Lemma nth_error_map : forall A B (f:A->B) xs i y,
  nth_error (map f xs) i = Some y ->
  exists x, nth_error xs i = Some x /\ f x = y.
Admitted.

Lemma nth_error_seq : forall start len i,
  nth_error (seq start len) i =
  if lt_dec i len
  then Some (start + i)
  else None.
Admitted.

Lemma nth_error_length_error : forall A (xs:list A) i, nth_error xs i = None ->
  i >= length xs.
Admitted.

Local Open Scope Z.

Lemma pos_pow_nat_pos : forall x n, 
  Z.pos x ^ Z.of_nat n > 0.
Admitted.

Module Type BaseCoefs.
  (** [BaseCoefs] represent the weights of each digit in a positional number system, with the weight of least significant digit presented first. The following requirements on the base are preconditions for using it with BaseSystem. *)
  Parameter base : list Z.
  Axiom b0_1 : nth_default 0 base 0 = 1.
  Axiom base_positive : forall b, In b base -> b > 0. (* nonzero would probably work too... *)
  Axiom base_good :
    forall i j, (i+j < length base)%nat ->
    let b := nth_default 0 base in
    let r := (b i * b j) / b (i+j)%nat in
    b i * b j = r * b (i+j)%nat.
End BaseCoefs.

Module BaseSystem (Import B:BaseCoefs).
  (** [BaseSystem] implements an constrained positional number system.
      A wide variety of bases are supported: the base coefficients are not
      required to be powers of 2, and it is NOT necessarily the case that
      $b_{i+j} = b_j b_j$. Implementations of addition and multiplication are
      provided, with focus on near-optimal multiplication performance on
      non-trivial but small operands: maybe 10 32-bit integers or so. This
      module does not handle carries automatically: if no restrictions are put
      on the use of a [BaseSystem], each digit is unbounded. This has nothing
      to do with modular arithmetic either.
  *)
  Definition digits : Type := list Z.

  Definition accumulate p acc := fst p * snd p + acc.
  Definition decode' bs u  := fold_right accumulate 0 (combine u bs).
  Definition decode := decode' base.
  Hint Unfold decode' accumulate.

	Fixpoint add (us vs:digits) : digits :=
		match us,vs with
			| u::us', v::vs' => (u+v)::(add us' vs')
			| _, nil => us
			| _, _ => vs
		end.
  Infix ".+" := add (at level 50).

  Lemma add_rep : forall bs us vs, decode' bs (add us vs) = decode' bs us + decode' bs vs.
  Proof.
    unfold decode', accumulate.
    induction bs; destruct us; destruct vs; auto; simpl; try rewrite IHbs; ring.
  Qed.

  Lemma decode_nil : forall bs, decode' bs nil = 0.
    auto.
  Qed.

  (* mul_geomseq is a valid multiplication algorithm if b_i = b_1^i *)
  Fixpoint mul_geomseq (us vs:digits) : digits :=
		match us,vs with
			| u::us', v::vs' => u*v :: map (Z.mul u) vs' .+ mul_geomseq us' vs
			| _, _ => nil
		end.

  Definition mul_each u := map (Z.mul u).
  Lemma mul_each_rep : forall bs u vs, decode' bs (mul_each u vs) = u * decode' bs vs.
  Proof.
    unfold decode', accumulate.
    induction bs; destruct vs; auto; simpl; try rewrite IHbs; ring.
  Qed.

  Definition crosscoef i j : Z := 
    let b := nth_default 0 base in
    (b(i) * b(j)) / b(i+j)%nat.

  Fixpoint zeros n := match n with O => nil | S n' => 0::zeros n' end.
  Lemma zeros_rep : forall bs n, decode' bs (zeros n) = 0.
    unfold decode', accumulate.
    induction bs; destruct n; auto; simpl; try rewrite IHbs; ring.
  Qed.
  Lemma length_zeros : forall n, length (zeros n) = n.
    induction n; simpl; auto.
  Qed.

  Lemma app_zeros_zeros : forall n m, (zeros n ++ zeros m) = zeros (n + m).
  Proof.
    intros; 
    induction n; simpl; auto.
    rewrite IHn; auto.
  Qed.

  Lemma zeros_app0 : forall m, (zeros m ++ 0 :: nil) = zeros (S m).
  Proof.
    intros.
    assert (0 :: nil = zeros 1) by auto.
    rewrite H.
    rewrite app_zeros_zeros.
    rewrite NPeano.Nat.add_1_r; auto.
  Qed.

  Lemma rev_zeros : forall n, rev (zeros n) = zeros n.
  Proof.
    intros.
    induction n. {
      unfold zeros; auto.
    } {
      replace (rev (zeros (S n))) with (rev (zeros n) ++ 0 :: nil) by auto.
      rewrite IHn.
      rewrite zeros_app0; auto.
    }
  Qed.

  Lemma app_cons_app_app : forall T xs (y:T) ys, xs ++ y :: ys = (xs ++ (y::nil)) ++ ys.
  Proof.
    intros.
    rewrite app_assoc_reverse.
    replace ((y :: nil) ++ ys) with (y :: ys); auto.
  Qed.

  (* mul' is multiplication with the SECOND ARGUMENT REVERSED and OUTPUT REVERSED *)
  Fixpoint mul_bi' (i:nat) (vsr:digits) := 
    match vsr with
    | v::vsr' => v * crosscoef i (length vsr') :: mul_bi' i vsr'
    | nil => nil
    end.
  Definition mul_bi (i:nat) (vs:digits) : digits :=
    zeros i ++ rev (mul_bi' i (rev vs)).

  (*
  Definition mul_bi (i:nat) (vs:digits) : digits :=
    let mkEntry := (fun (p:(nat*Z)) => let (j, v) := p in v * crosscoef i j) in
    zeros i ++ map mkEntry (@enumerate Z vs).
  *)

  Lemma decode_single : forall n bs x,
    decode' bs (zeros n ++ x :: nil) = nth_default 0 bs n * x.
  Proof.
    induction n; intros; simpl.
    destruct bs; auto; unfold decode', accumulate, nth_default; simpl; ring.
    destruct bs; simpl; auto.
    unfold decode', accumulate, nth_default in *; simpl in *; auto.
  Qed.

  Lemma peel_decode : forall xs ys x y, decode' (x::xs) (y::ys) = x*y + decode' xs ys.
    intros.
    unfold decode', accumulate, nth_default in *; simpl in *; ring_simplify; auto.
  Qed.

  Lemma decode_highzeros : forall xs bs n, decode' bs (xs ++ zeros n) = decode' bs xs.
    induction xs; intros; simpl; try rewrite zeros_rep; auto.
    destruct bs; simpl; auto.
    repeat (rewrite peel_decode).
    rewrite IHxs; auto.
  Qed.

  Lemma mul_bi_single : forall m n x,
    (n + m < length base)%nat ->
    decode (mul_bi n (zeros m ++ x :: nil)) = nth_default 0 base m * x * nth_default 0 base n.
  Proof.
    unfold mul_bi, decode.
    destruct m; simpl; ssimpl_list; simpl; intros. {
      rewrite decode_single.
      unfold crosscoef; simpl.
      rewrite plus_0_r.
      ring_simplify.
      replace (nth_default 0 base n * nth_default 0 base 0) with (nth_default 0 base 0 * nth_default 0 base n) by ring.
      SearchAbout Z.div.
      rewrite Z_div_mult; try ring.

      apply base_positive.
      rewrite nth_default_eq.
      apply nth_In.
      rewrite plus_0_r in *.
      auto.
    } {
      simpl; ssimpl_list; simpl.
      replace (mul_bi' n (rev (zeros m) ++ 0 :: nil)) with (zeros (S m)) by admit.
      intros; simpl; ssimpl_list; simpl.
      rewrite length_zeros.
      rewrite app_cons_app_app.
      rewrite rev_zeros.
      intros; simpl; ssimpl_list; simpl.
      rewrite zeros_app0.
      rewrite app_assoc.
      rewrite app_zeros_zeros.
      rewrite decode_single.
      unfold crosscoef; simpl; ring_simplify.
      rewrite NPeano.Nat.add_1_r.
      rewrite base_good by auto.
      rewrite Z_div_mult.
      rewrite <- Z.mul_assoc.
      rewrite <- Z.mul_comm.
      rewrite <- Z.mul_assoc.
      rewrite <- Z.mul_assoc.
      destruct (Z.eq_dec x 0); subst; try ring.
      rewrite Z.mul_cancel_l by auto.
      rewrite <- base_good by auto.
      ring.

      apply base_positive.
      rewrite nth_default_eq.
      apply nth_In; auto.
    }
  Qed.

  Lemma set_higher' : forall vs x, vs++x::nil = vs .+ (zeros (length vs) ++ x :: nil).
    induction vs; auto.
    intros; simpl; rewrite IHvs; f_equal; ring.
  Qed.
  
  Lemma set_higher : forall bs vs x,
    decode' bs (vs++x::nil) = decode' bs vs + nth_default 0 bs (length vs) * x.
  Proof.
    intros.
    rewrite set_higher'.
    rewrite add_rep.
    f_equal.
    apply decode_single.
  Qed.

  Lemma zeros_plus_zeros : forall n, zeros n = zeros n .+ zeros n.
    induction n; auto.
    simpl; f_equal; auto. 
  Qed.

  Lemma mul_bi'_n_nil : forall n, mul_bi' n nil = nil.
  Proof.
    intros.
    unfold mul_bi; auto.
  Qed.

  Lemma add_nil_l : forall us, nil .+ us = us.
    induction us; auto.
  Qed.

  Lemma add_nil_r : forall us, us .+ nil = us.
    induction us; auto.
  Qed.
  
  Lemma add_first_terms : forall us vs a b,
    (a :: us) .+ (b :: vs) = (a + b) :: (us .+ vs).
    auto.
  Qed.
 
  Lemma mul_bi'_cons : forall n x us,
    mul_bi' n (x :: us) = x * crosscoef n (length us) :: mul_bi' n us.
  Proof.
    unfold mul_bi'; auto.
  Qed.

  Lemma cons_length : forall A (xs : list A) a, length (a :: xs) = S (length xs).
  Proof.
    auto.
  Qed.

  Lemma add_same_length : forall us vs l, (length us = l) -> (length vs = l) ->
    length (us .+ vs) = l.
  Proof.
    induction us; intros. {
      rewrite add_nil_l.
      apply H0.
    } {
      destruct vs. {
        rewrite add_nil_r; apply H.
      } {
        rewrite add_first_terms.
        rewrite cons_length.
        rewrite (IHus vs (pred l)).
        apply NPeano.Nat.succ_pred_pos.
        replace l with (length (a :: us)) by (apply H).
        rewrite cons_length; simpl.
        apply gt_Sn_O.
        replace l with (length (a :: us)) by (apply H).
        rewrite cons_length; simpl; auto.
        replace l with (length (z :: vs)) by (apply H0).
        rewrite cons_length; simpl; auto.
      }
    }
  Qed.

  Lemma add_app_same_length : forall us vs a b l, (length (us