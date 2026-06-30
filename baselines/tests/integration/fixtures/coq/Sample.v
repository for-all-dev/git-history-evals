Lemma add_0_r (n : nat) : n + 0 = n.
Proof. induction n; simpl; auto. Qed.

Lemma mul_1_r (n : nat) : n * 1 = n.
Proof. induction n; simpl; auto. Qed.
