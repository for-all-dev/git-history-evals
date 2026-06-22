/*  Build-level validation, invoked via `ablate --check-build DIR ...`.

    For each target session directory (one containing a `ROOT`):
      1. copy it to a fresh temp dir (the original is never touched);
      2. rename the declared session(s) in the copy's ROOT (a `__ablated`
         suffix) so the copy never clashes with the original on the build path
         (important when the original is reachable via an `--afp` directory);
      3. ablate every *.thy in the copy at the configured prob / depth range;
      4. run `isabelle build -o quick_and_dirty=true` on the copy, with any
         `--afp` directories added via `-d` so dependencies resolve.

    `quick_and_dirty=true` is required: otherwise `sorry` is a build error rather
    than an admitted proof. A clean build (rc 0) therefore certifies that the
    ablated theory is still well-formed Isabelle — it parses, the statements and
    definitions elaborate and type-check, and only the proof bodies are missing.
    This is the gate that parsing alone cannot give, and it matters most for
    sub-proof ablation (nested `sorry` validity).

    Builds are intensive; the bundled HOL/Pure heaps are reused, but anything
    importing heavier sessions (HOL-Analysis, ...) will build those first.
*/

package proofablate

import isabelle._

import scala.util.Random
import scala.util.matching.Regex
import scala.collection.mutable


object CheckBuild {
  private val SUFFIX = "__ablated"

  /** Rename declared session names (at the `session NAME` declaration and at
   *  `= PARENT` references to a declared name) with a suffix, so the copy does
   *  not clash with the original. Theory/document names are left untouched.
   *  Returns (rewritten ROOT, declared names). */
  def rename_sessions(root: String): (String, List[String]) = {
    // session names may be quoted and contain hyphens (e.g. "General-Triangle")
    // group 2 = quoted name (no quotes), group 3 = bare name
    def name_of(m: Regex.Match): String = if (m.group(2) != null) m.group(2) else m.group(3)
    def requote(m: Regex.Match, prefix: String, nm: String): String =
      if (m.group(2) != null) prefix + "\"" + nm + "\"" else prefix + nm

    val decl = """(?m)^([ \t]*session[ \t]+)(?:"([^"]+)"|([^\s"()=]+))""".r
    val names = decl.findAllMatchIn(root).map(name_of).toSet
    val r1 = decl.replaceAllIn(root, m =>
      Regex.quoteReplacement(requote(m, m.group(1), name_of(m) + SUFFIX)))

    val parent = """(=[ \t]*)(?:"([^"]+)"|([^\s"()+]+))""".r
    val r2 = parent.replaceAllIn(r1, m =>
      Regex.quoteReplacement(
        if (names.contains(name_of(m))) requote(m, m.group(1), name_of(m) + SUFFIX)
        else m.matched))
    (r2, names.toList.sorted)
  }

  private sealed case class Outcome(
    name: String, sessions: List[String], n_files: Int, n_total: Int, n_ablated: Int,
    rc: Int, ok: Boolean, secs: Double, note: String)

  /** Returns true if every target built cleanly. */
  def run(syntax: Outer_Syntax, targets: List[Path], afp: List[Path],
    spec: Ablate.Spec, seed_base: Long, keep: Boolean, progress: Progress): Boolean = {

    val isabelle = Isabelle_System.getenv("ISABELLE_HOME") + "/bin/isabelle"
    val afp_args = afp.map(d => "-d " + Bash.string(d.absolute.implode)).mkString(" ")
    val results = new mutable.ListBuffer[Outcome]

    for (target <- targets) {
      val name = target.file.getName
      progress.echo(s"\n================ check-build: $name ================")

      if (!(target + Path.basic("ROOT")).file.isFile) {
        progress.echo(s"  no ROOT in ${target.implode} — skipping")
        results += Outcome(name, Nil, 0, 0, 0, -1, ok = false, 0.0, "no ROOT")
      }
      else {
        val base = File.path(java.nio.file.Files.createTempDirectory("ablate-build-").toFile)
        val work = base + Path.basic(name)
        try {
          Isabelle_System.copy_dir(target, work, direct = true)

          // 1. rename sessions in the copied ROOT
          val root_path = work + Path.basic("ROOT")
          val (new_root, sessions) = rename_sessions(File.read(root_path))
          File.write(root_path, new_root)

          // 2. ablate every theory in the copy
          val thys = Ablate.collect_theories(List(work.implode))
          val centrality: String => Int =
            if (spec.uses_centrality) {
              val fan = Centrality.fan_in(syntax, thys, progress)
              (nm => fan.getOrElse(nm, 0))
            } else (_ => 0)
          var n_files = 0; var n_total = 0; var n_ablated = 0
          for (thy <- thys) {
            val text = File.read(thy)
            val rng = new Random(seed_base ^ thy.implode.hashCode.toLong)
            val res = Ablate.ablate(syntax, text, spec, rng, centrality)
            File.write(thy, res.text)
            n_files += 1; n_total += res.total; n_ablated += res.ablated
          }
          progress.echo(s"  sessions: ${sessions.mkString(", ")}  " +
            s"(ablated $n_ablated/$n_total proofs across $n_files theories)")

          // 3. build the ablated copy
          val cmd = isabelle + " build -o quick_and_dirty=true " +
            (if (afp_args.nonEmpty) afp_args + " " else "") +
            "-D " + Bash.string(work.absolute.implode)
          progress.echo("  $ " + cmd)
          val t0 = System.nanoTime()
          val res =
            Isabelle_System.bash(cmd,
              progress_stdout = s => progress.echo("    " + s),
              progress_stderr = s => progress.echo("    " + s))
          val secs = (System.nanoTime() - t0) / 1e9
          results += Outcome(name, sessions, n_files, n_total, n_ablated,
            res.rc, res.ok, secs, if (res.ok) "" else res.err.takeRight(200))
        } catch {
          case e: Throwable =>
            results += Outcome(name, Nil, 0, 0, 0, -1, ok = false, 0.0, e.toString.takeRight(200))
        } finally {
          if (keep) progress.echo(s"  [kept working copy: ${work.implode}]")
          else Isabelle_System.rm_tree(base)
        }
      }
    }

    // summary
    println("")
    println("================ check-build summary ================")
    for (o <- results) {
      val status = if (o.ok) "PASS" else s"FAIL (rc=${o.rc})"
      println(f"  ${o.name}%-32s $status%-14s " +
        f"${o.n_ablated}%5d/${o.n_total}%-5d proofs  ${o.secs}%7.1f s")
      if (!o.ok && o.note.nonEmpty) println(s"       ${o.note}")
    }
    val ok = results.forall(_.ok)
    println(s"\nRESULT: ${if (ok) "OK" else "BUILD FAILURES PRESENT"} " +
      s"(${results.count(_.ok)}/${results.length} built)")
    ok
  }
}
