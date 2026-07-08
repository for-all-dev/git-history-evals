/*  Regression test for the Scala ablator, mirroring the rocq test/test_ablate.ml.

    There is no unit-test framework bundled with the Isabelle/Scala classpath, so
    this is a small `main` that loads the HOL syntax (like the tool itself) and
    asserts the `--corollary-delete-lemmas-all` behaviour. Run it under the same
    environment as the tool, choosing this main class via ABLATE_MAIN:

      nix develop
      bash build.sh
      ABLATE_MAIN=proofablate.AblateTest ./bin/ablate

    Exits non-zero if any check fails.
*/

package proofablate

import isabelle._

import scala.util.Random


object AblateTest {
  private var failures = 0
  private def check(name: String, cond: Boolean): Unit =
    if (cond) println(s"ok   $name")
    else { println(s"FAIL $name"); failures += 1 }

  // Two independent corollaries: cor_a's closure is {base1}, cor_b's is {base2}
  // (base1/base2 have empty closures). --corollary-delete-lemmas-all must emit one
  // ablation per eligible corollary (>= 2), each deleting exactly one lemma, each
  // non-trivial. The non-all path is a singleton.
  private val corollaryAllSrc =
    """theory T
      |imports Main
      |begin
      |
      |lemma base1: "(1::nat) = 1" by simp
      |
      |lemma base2: "(2::nat) = 2" by simp
      |
      |lemma cor_a: "(1::nat) = 1" using base1 by simp
      |
      |lemma cor_b: "(2::nat) = 2" using base2 by simp
      |
      |end
      |""".stripMargin

  def main(args: Array[String]): Unit = {
    val syntax = Ablate.load_syntax("HOL", Nil, new Progress)

    val spec = Ablate.Spec(delete_lemmas = true, corollary = true, corollary_all = true)
    val results = Ablate.ablate_all(syntax, corollaryAllSrc, spec, new Random(5))
    check("corollary-all: emits >= 2 distinct ablations", results.length >= 2)
    check("corollary-all: each result deletes exactly one lemma",
      results.forall(_.deleted.length == 1))
    check("corollary-all: every result is non-trivial (holes + differs from solution)",
      results.forall(r => r.ablated > 0 && r.text != r.solution))
    // the non-all path is the singleton List(ablate ...)
    val one = Ablate.ablate_all(syntax, corollaryAllSrc, spec.copy(corollary_all = false), new Random(5))
    check("corollary (non-all): ablate_all is a singleton", one.length == 1)

    if (failures == 0) println("\nALL TESTS PASSED")
    else { println(s"\n$failures TEST(S) FAILED"); sys.exit(1) }
  }
}
