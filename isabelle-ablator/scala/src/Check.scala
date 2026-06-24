/*  Corpus self-test for the ablator, invoked via `ablate --check PATH...`.

    For each theory it checks (at the configured --min-depth/--max-depth):
      (1) round-trip:  imploding the parsed spans reproduces the source exactly
                       (i.e. ablating with prob 0 is the identity);
      (2) delimitation: every in-range goal's proof is cleanly delimited, so
                       prob 1 ablates *all* of them (no desyncs / leftovers);
      (3) re-parse:    the ablated text still parses with all theory-level goal
                       statements preserved (a mangled rewrite would drop one).
    Reports aggregate statistics and lists any files that fail a check.
*/

package proofablate

import isabelle._

import scala.util.Random
import scala.collection.mutable


object Check {
  private def count_goals(syntax: Outer_Syntax, text: CharSequence): Int =
    syntax.parse_spans(text).count(sp =>
      sp.kind.keyword_kind.exists(Keyword.theory_goal.contains))

  /** Returns true if all checks pass. */
  def run(syntax: Outer_Syntax, theories: List[Path], spec: Ablate.Spec,
    centrality: String => Int = (_ => 0)): Boolean = {
    var n_files = 0
    var n_goals = 0
    var n_ablated = 0
    val roundtrip_fail = new mutable.ListBuffer[String]
    val delimit_fail = new mutable.ListBuffer[(String, Int, Int)]
    val reparse_fail = new mutable.ListBuffer[(String, Int, Int)]
    val errored = new mutable.ListBuffer[(String, String)]
    var ablate_ns = 0L   // time spent in ablate() alone, across all theories

    val wall0 = System.nanoTime()
    for (thy <- theories) {
      val name = thy.implode
      try {
        val text = File.read(thy)

        // --check ignores --count/-p and context shaping: prob 0 must be the
        // identity, prob 1 must ablate all candidates.
        val base = spec.copy(count = None, truncate = false, shrink_challenge = false, shrink_solution = false)
        val identity = Ablate.ablate(syntax, text, base.copy(prob = 0.0), new Random(0), centrality)
        if (identity.text != text) roundtrip_fail += name

        val t = System.nanoTime()
        val all = Ablate.ablate(syntax, text, base.copy(prob = 1.0), new Random(0), centrality)
        ablate_ns += System.nanoTime() - t
        n_files += 1
        n_goals += all.total
        n_ablated += all.ablated
        if (all.ablated != all.total) delimit_fail += ((name, all.ablated, all.total))

        // theory-level goal statements must survive ablation verbatim
        if (count_goals(syntax, all.text) != count_goals(syntax, text))
          reparse_fail += ((name, count_goals(syntax, text), count_goals(syntax, all.text)))
      } catch {
        case e: Throwable => errored += ((name, e.toString))
      }
    }
    val wall_s = (System.nanoTime() - wall0) / 1e9
    val ablate_s = ablate_ns / 1e9

    println("")
    println("================ ablation self-test ================")
    println(s"theories checked     : $n_files")
    println(s"in-range goals       : $n_goals")
    println(s"cleanly ablated      : $n_ablated " +
      (if (n_goals > 0) f"(${100.0 * n_ablated / n_goals}%.2f%%)" else ""))
    println(f"ablation time        : $ablate_s%.2f s " +
      (if (ablate_s > 0) f"(${n_files / ablate_s}%,.0f theories/s, ${n_goals / ablate_s}%,.0f goals/s)" else ""))
    println(f"self-test wall time  : $wall_s%.2f s")
    println(s"round-trip failures  : ${roundtrip_fail.length}")
    println(s"delimitation misses  : ${delimit_fail.length}")
    println(s"re-parse mismatches  : ${reparse_fail.length}")
    println(s"errored theories     : ${errored.length}")

    def sample[A](label: String, xs: mutable.ListBuffer[A]): Unit =
      if (xs.nonEmpty) {
        println(s"\n-- $label (${xs.length}), first 10:")
        xs.take(10).foreach(x => println("   " + x.toString))
      }
    sample("round-trip failures", roundtrip_fail)
    sample("delimitation misses", delimit_fail)
    sample("re-parse mismatches", reparse_fail)
    sample("errored", errored)

    val ok = roundtrip_fail.isEmpty && reparse_fail.isEmpty && errored.isEmpty
    println(if (ok) "\nRESULT: OK" else "\nRESULT: FAILURES PRESENT")
    ok
  }
}
