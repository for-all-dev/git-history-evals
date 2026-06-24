/*  Corpus-wide proof-dependency centrality.

    Builds an approximate dependency graph by *textual name citation*: a
    top-level fact (lemma/theorem/…) named `B` is "cited" by theorem `A` if `A`'s
    proof body mentions the identifier `B` (in `using B`, `simp add: B`,
    `rule B`, a bundle, …). The **centrality** of `B` is its fan-in — the number
    of distinct top-level theorems across the corpus whose proof cites it.

    This needs only the parser (no prover, no build) and works on any corpus, so
    it is cheap but approximate: anonymous facts have centrality 0 (they can't be
    cited by name) and citations are matched on the (last-segment) identifier.
    The big failure mode is short names that collide with ordinary variables — a
    field `K` used as a term in 90 proofs would read as 90 "citations" — so we
    ignore declared names shorter than `MIN_NAME_LEN`. The accurate alternative
    is Isabelle's own theorem dependencies via `export_theory`, which requires
    building the session — a planned upgrade.
*/

package proofablate

import isabelle._

import scala.collection.mutable


object Centrality {
  // Names shorter than this are dropped from the graph: they collide with
  // ordinary variables (a field `K`, an index `i`) and produce spurious fan-in.
  private val MIN_NAME_LEN = 3

  private def is_cite_token(t: Token): Boolean =
    t.is_ident || t.kind == Token.Kind.LONG_IDENT

  /** Identifiers a proof body might be citing: each name token's source, plus
   *  its last dotted segment (so a qualified `Foo.bar` also matches `bar`). */
  private def cited_names(tokens: Iterable[Token], into: mutable.Set[String]): Unit =
    for (t <- tokens if is_cite_token(t)) {
      val s = t.source
      into += s
      val dot = s.lastIndexOf('.')
      if (dot >= 0 && dot < s.length - 1) into += s.substring(dot + 1)
    }

  /** name -> fan-in (distinct top-level theorems whose proof cites it). */
  def fan_in(syntax: Outer_Syntax, theories: List[Path], progress: Progress): Map[String, Int] = {
    // per top-level goal: (name, set of identifiers cited anywhere in its proof)
    val goals = new mutable.ListBuffer[(String, Set[String])]

    for (thy <- theories) {
      try {
        val spans = syntax.parse_spans(File.read(thy)).toArray
        val n = spans.length
        var i = 0
        while (i < n) {
          val k = spans(i).kind.keyword_kind
          if (k.exists(Keyword.theory_goal.contains)) {
            val name = Ablate.goal_name(spans(i))
            i += 1
            var depth = 1
            val cited = mutable.Set.empty[String]
            while (i < n && depth > 0) {
              val span = spans(i)
              cited_names(span.content, cited)
              span.kind.keyword_kind match {
                case Some(kk) if Keyword.theory.contains(kk) => depth = 0   // desync: bail
                case Some(kk) =>
                  if (Keyword.proof_goal.contains(kk) || kk == Keyword.PRF_OPEN) depth += 1
                  else if (Keyword.proof_close.contains(kk)) depth -= 1
                  else if (Keyword.qed_global.contains(kk)) depth = 0
                case None =>
              }
              i += 1
            }
            goals += ((name, cited.toSet))
          } else i += 1
        }
      } catch { case _: Throwable => () }
    }

    val declared = goals.iterator.map(_._1).filter(_.length >= MIN_NAME_LEN).toSet
    val fan = mutable.Map.empty[String, Int].withDefaultValue(0)
    for ((name, cited) <- goals; d <- cited if d != name && declared.contains(d)) fan(d) += 1
    progress.echo(s"[centrality: ${declared.size} named facts, " +
      s"${fan.count(_._2 > 0)} cited; max fan-in ${if (fan.isEmpty) 0 else fan.values.max}]")
    fan.toMap
  }
}
