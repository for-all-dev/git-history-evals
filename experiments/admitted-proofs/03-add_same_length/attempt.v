Lemma add_same_length : forall us vs l, (length us = l) -> (length vs = l) ->
    length (us .+ vs) = l.
  Proof.
    induction us; destruct vs; intros; simpl in *; try omega.
    destruct l; try omega.
    simpl.
    apply eq_S.
    apply IHus with (l := l); omega.
  Qed.