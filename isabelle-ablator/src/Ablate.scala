/*  Isabelle semantic-ablation tool.

    Parse Isabelle theories with the bundled Isabelle/Scala outer-syntax parser
    and replace proofs with `sorry`, preserving everything else (statements,
    definitions, comments, whitespace) byte-for-byte.

    Usage:
      ablate [OPTIONS] PATH...            emit (challenge, solution) JSONL pairs
      ablate --check [OPTIONS] PATH...    run the corpus self-test instead
      ablate --check-build DIR ...        copy+ablate+`isabelle build` a session

    PATH... is any mix of .thy files and directories (walked for *.thy). The
    ablated theory is the *challenge*; the original is the ground-truth *solution*.

    The unit of ablation is the proof of a *goal* command. Goals nest:

        lemma foo: "P"          depth 1   (thy_goal*)
        proof -
          have a: "Q"           depth 2   (prf_goal / prf_asm_goal)
            by simp
          show "P" ...          depth 2
        qed

    Which proofs are ablated is controlled by a difficulty Spec (depth range,
    leaves-only, proof-size window, per-proof probability), or a `--difficulty`
    preset that composes those knobs. Script-style goals (`subgoal` &c.:
    prf_script_goal/_asm_goal) are never replaced but still nest.
*/

package proofablate

import isabelle._

import scala.util.Random
import scala.collection.mutable
import scala.collection.immutable.ListMap


object Ablate {
  /* keyword-kind predicates (see Pure/Isar/keyword.scala) */

  private def is_goal(k: String): Boolean =
    Keyword.theory_goal.contains(k) || Keyword.proof_goal.contains(k)
  private def is_ablatable(k: String): Boolean =
    Keyword.theory_goal.contains(k) || k == Keyword.PRF_GOAL || k == Keyword.PRF_ASM_GOAL
  private def closes(k: String): Boolean = Keyword.proof_close.contains(k)
  private def closes_global(k: String): Boolean = Keyword.qed_global.contains(k)
  private def is_theory(k: String): Boolean = Keyword.theory.contains(k)
  // commands that precede the "real" proof method (using/unfolding/from/then/note/
  // also/moreover/ultimately/fix/assume/let/case) — skipped when classifying method
  private def is_prefatory(k: String): Boolean =
    k == Keyword.PRF_CHAIN || k == Keyword.PRF_DECL || k == Keyword.PRF_ASM

  private def kind_of(span: Command_Span.Span): Option[String] = span.kind.keyword_kind

  private def n_lines(s: String): Int = if (s.isEmpty) 0 else s.count(_ == '\n') + 1

  /** A short label for a proof's main method, for difficulty stratification. */
  private def classify_method(span: Command_Span.Span): String =
    span.name match {
      case "by" =>
        span.content.dropWhile(t => !t.is_command).drop(1).filter(_.is_proper)
          .find(_.is_ident).map("by:" + _.source).getOrElse("by")
      case "apply" | "apply_end" => "apply"
      case "proof" => "structured"
      case "." | ".." => "trivial"
      case other => other
    }

  /** Best-effort theorem name from a goal statement span. "" when anonymous. */
  def goal_name(span: Command_Span.Span): String = {
    val after = span.content.dropWhile(t => !t.is_command).drop(1).filter(_.is_proper)
    val rest =
      if (after.headOption.exists(_.is_keyword("(")))
        after.dropWhile(t => !t.is_keyword(")")).drop(1)
      else after
    rest match {
      case t :: next :: _ if t.is_ident && (next.is_keyword(":") || next.is_keyword("[")) => t.source
      case _ => ""
    }
  }

  /** Which proofs to ablate. `count`, when set, overrides `prob`: ablate a
   *  uniformly-random subset of that many matching proofs (see `ablate`). */
  sealed case class Spec(
    prob: Double = 0.5,
    count: Option[Int] = None,
    by_centrality: Boolean = false,  // for `count`: pick the most-cited, not random
    min_depth: Int = 1,
    max_depth: Int = 1,
    leaves_only: Boolean = false,
    min_size: Int = 0,               // proof-command count, inclusive lower bound
    max_size: Int = Int.MaxValue,    // inclusive upper bound
    min_centrality: Int = 0,         // fan-in (distinct theorems citing this lemma)
    max_centrality: Int = Int.MaxValue
  ) {
    def uses_centrality: Boolean =
      min_centrality > 0 || max_centrality != Int.MaxValue || by_centrality
  }

  /** A removed proof and its difficulty signals. */
  sealed case class Hole(theorem_name: String, depth: Int, n_commands: Int, n_lines: Int,
    is_leaf: Boolean, centrality: Int, method: String, proof_text: String)

  sealed case class Result(text: String, total: Int, ablated: Int, holes: List[Hole])

  // Measured properties of a goal body.
  private sealed case class Body(end: Int, lead: String, text: String,
    n_commands: Int, is_leaf: Boolean, method: String, clean: Boolean)

  /** Ablate proofs selected by `spec`, each replaced by `sorry`.
   *
   *  With `spec.prob`, each matching proof is ablated by an independent coin.
   *  With `spec.count = Some(N)`, we first enumerate the T matching proofs, pick
   *  a uniformly-random subset of min(N, T) (seeded, reproducible), and ablate
   *  exactly those — exact when matches don't nest (a single depth, or
   *  `--leaves-only`); otherwise an ablated ancestor may shadow selected
   *  descendants, so it is best-effort up to N. */
  def ablate(syntax: Outer_Syntax, text: CharSequence, spec: Spec,
    rng: Random, centrality: String => Int = (_ => 0)): Result = {
    val spans = syntax.parse_spans(text).toArray
    val n = spans.length

    def src(j: Int): String = Token.implode(spans(j).content)

    // One pass; `decide(stmt_index)` says whether to ablate each matching proof.
    // Returns the result plus, for every matching proof, (stmt index, centrality).
    def walk_all(decide: Int => Boolean): (Result, List[(Int, Int)]) = {
    val out = new mutable.StringBuilder
    val holes = new mutable.ListBuffer[Hole]
    val matches = new mutable.ListBuffer[(Int, Int)]
    var total = 0
    var ablated = 0
    var i = 0
    var depth = 0

    // Pure lookahead — does NOT mutate i/depth/out. Scans the whole subtree.
    def measure(start: Int, d: Int): Body = {
      val lead = new mutable.StringBuilder
      val body = new mutable.StringBuilder
      var j = start; var dep = d + 1
      var seen = false; var clean = true
      var n_cmds = 0; var is_leaf = true; var method = ""
      while (j < n && dep > d && clean) {
        kind_of(spans(j)) match {
          case Some(k) if is_theory(k) => clean = false
          case Some(k) =>
            seen = true; body ++= src(j); n_cmds += 1
            if (is_ablatable(k)) is_leaf = false
            if (method.isEmpty && !is_prefatory(k)) method = classify_method(spans(j))
            if (Keyword.proof_goal.contains(k) || k == Keyword.PRF_OPEN) dep += 1
            else if (closes(k)) dep -= 1
            else if (closes_global(k)) dep = d
            j += 1
          case None =>
            if (seen) body ++= src(j) else lead ++= src(j)
            j += 1
        }
      }
      Body(j, lead.toString, body.toString, n_cmds, is_leaf,
        if (method.isEmpty) "?" else method, clean && dep == d)
    }

    // Emit the body verbatim, recursing into nested goals (the "keep" path).
    def walk_body(d: Int): Unit =
      while (i < n && depth > d) {
        kind_of(spans(i)) match {
          case Some(k) if is_theory(k) => depth = d                 // desync: bail
          case Some(k) if is_goal(k) => handle_goal()                // recurse
          case Some(k) =>
            out ++= src(i)
            if (k == Keyword.PRF_OPEN) depth += 1
            else if (closes(k)) depth -= 1
            else if (closes_global(k)) depth = d
            i += 1
          case None => out ++= src(i); i += 1
        }
      }

    def handle_goal(): Unit = {
      val d = depth
      val stmt_idx = i                                       // stable id of this goal across passes
      val k = kind_of(spans(i)).get
      val name = goal_name(spans(i))
      out ++= src(i)
      i += 1
      depth = d + 1
      val goal_depth = d + 1
      val m = measure(i, d)
      val cent = centrality(name)

      val candidate =
        is_ablatable(k) && m.clean &&
          goal_depth >= spec.min_depth && goal_depth <= spec.max_depth &&
          (!spec.leaves_only || m.is_leaf) &&
          m.n_commands >= spec.min_size && m.n_commands <= spec.max_size &&
          cent >= spec.min_centrality && cent <= spec.max_centrality

      if (candidate) {
        total += 1
        matches += ((stmt_idx, cent))
        if (decide(stmt_idx)) {
          out ++= (if (m.lead.nonEmpty) m.lead else " ")    // separate glued proofs
          out ++= "sorry"
          ablated += 1
          holes += Hole(name, goal_depth, m.n_commands, n_lines(m.text), m.is_leaf, cent, m.method, m.text)
          i = m.end; depth = d
        } else walk_body(d)                                  // not selected: keep, recurse deeper
      }
      else if (goal_depth > spec.max_depth) {                // too deep: verbatim (keep lead!)
        out ++= m.lead; out ++= m.text; i = m.end; depth = d
      }
      else walk_body(d)                                      // too shallow / filtered out: recurse
    }

    while (i < n) {
      kind_of(spans(i)) match {
        case Some(k) if is_goal(k) => handle_goal()
        case _ => out ++= src(i); i += 1
      }
    }

    (Result(out.toString, total, ablated, holes.toList), matches.toList)
    }  // walk_all

    spec.count match {
      case Some(target) =>
        // enumerate matching proofs (decide=false), pick a subset of N, then
        // ablate exactly those. Random spread, or the most-cited if --by-centrality.
        val cands = walk_all(_ => false)._2   // List[(stmt_idx, centrality)]
        val selected: Set[Int] =
          if (cands.length <= target) cands.map(_._1).toSet
          else if (spec.by_centrality)
            cands.sortBy { case (idx, c) => (-c, idx) }.take(target).map(_._1).toSet
          else rng.shuffle(cands.map(_._1)).take(target).toSet
        walk_all(selected.contains)._1.copy(total = cands.length)
      case None =>
        walk_all(_ => rng.nextDouble() < spec.prob)._1
    }
  }


  /* difficulty preset ladder (easy -> hard); raw knobs override any preset field */

  sealed case class Preset(prob: Double, min_depth: Int, max_depth: Int, leaves_only: Boolean)

  val LADDER: Vector[Preset] = Vector(
    Preset(0.3, 1, Int.MaxValue, leaves_only = true),    // L0: a few leaf steps
    Preset(1.0, 1, Int.MaxValue, leaves_only = true),    // L1: all leaf steps, skeletons kept
    Preset(1.0, 2, Int.MaxValue, leaves_only = false),   // L2: all sub-proofs, top skeleton only
    Preset(0.5, 1, 1, leaves_only = false),              // L3: half of whole top-level proofs
    Preset(1.0, 1, 1, leaves_only = false))              // L4: every proof (code + spec only)

  def preset_of(s: String): Option[Preset] = {
    val t = s.toLowerCase.stripPrefix("l")
    t.toIntOption.filter(i => i >= 0 && i < LADDER.length).map(LADDER)
  }


  /* theory discovery: files as-is, directories walked recursively for *.thy */

  def collect_theories(paths: List[String]): List[Path] = {
    val buf = new mutable.ListBuffer[Path]
    def walk(p: Path): Unit = {
      val f = p.file
      if (f.isDirectory)
        Option(f.listFiles()).getOrElse(Array.empty[java.io.File])
          .sortBy(_.getName).foreach(k => walk(Path.explode(k.getPath)))
      else if (f.getName.endsWith(".thy")) buf += p
    }
    paths.foreach(p => walk(Path.explode(p)))
    buf.toList.distinct
  }


  /* session syntax */

  def load_syntax(session: String, dirs: List[Path], progress: Progress): Outer_Syntax = {
    val options = Options.init()
    val structure = Sessions.load_structure(options, dirs = dirs)
    val selected = structure.selection(Sessions.Selection(sessions = List(session)))
    val deps = Sessions.deps(selected, progress = progress)
    deps(session).overall_syntax
  }


  /* (challenge, solution) record, aligned with the dataset row schema */

  private def task_id(path: Path): String =
    "ablate_" + SHA1.digest(path.implode).toString.substring(0, 12)

  private def theory_name(path: Path): String = {
    val b = path.file.getName
    if (b.endsWith(".thy")) b.dropRight(4) else b
  }

  private def depth_json(d: Int): JSON.T = if (d == Int.MaxValue) "inf" else d

  def record(path: Path, session: String, spec: Spec, seed: Long, difficulty: Option[String],
    original: String, result: Result): JSON.Object.T = {
    val holes: List[JSON.T] =
      result.holes.map(h => ListMap[String, JSON.T](
        "theorem_name" -> h.theorem_name, "depth" -> h.depth, "n_commands" -> h.n_commands,
        "n_lines" -> h.n_lines, "is_leaf" -> h.is_leaf, "centrality" -> h.centrality,
        "method" -> h.method, "proof_text" -> h.proof_text))
    ListMap[String, JSON.T](
      "task_id" -> task_id(path),
      "proof_assistant" -> "isabelle",
      "session" -> session,
      "file_path" -> path.implode,
      "theory" -> theory_name(path),
      "challenge_type" -> "proof_ablate",
      "difficulty" -> difficulty.map(d => d: JSON.T).getOrElse(null),
      "count" -> spec.count.map(c => c: JSON.T).getOrElse(null),
      "by_centrality" -> spec.by_centrality,
      "ablation_prob" -> (if (spec.count.isDefined) (null: JSON.T) else spec.prob),
      "min_depth" -> spec.min_depth,
      "max_depth" -> depth_json(spec.max_depth),
      "leaves_only" -> spec.leaves_only,
      "min_size" -> spec.min_size,
      "max_size" -> depth_json(spec.max_size),
      "min_centrality" -> spec.min_centrality,
      "max_centrality" -> depth_json(spec.max_centrality),
      "seed" -> seed,
      "n_proofs" -> result.total,
      "n_ablated" -> result.ablated,
      "holes_filled" -> holes,
      "challenge_file_content" -> result.text,
      "solution_file_content" -> original)
  }


  /* command line */

  private def parse_depth(s: String): Int =
    if (s == "inf" || s == "infinity") Int.MaxValue else s.toInt

  private val usage =
    """Usage: ablate [OPTIONS] PATH...
      |
      |  Ablate proofs in Isabelle theories, replacing them with `sorry`.
      |  PATH... is any mix of .thy files and directories (walked for *.thy).
      |  Default: emit one indented JSON (challenge, solution) record per theory.
      |
      |  Modes:
      |    --check          run the corpus self-test instead of emitting records
      |    --check-build D  copy session dir D, ablate it, and `isabelle build` it
      |                     (-o quick_and_dirty); certifies it still type-checks.
      |                     Repeatable. --afp DIR / --keep apply.
      |
      |  Difficulty (raw knobs override any --difficulty preset):
      |    --difficulty L   preset ladder L0 (easy) .. L4 (code+spec only)
      |    --min-depth N    ablate goals at nesting depth >= N (default: 1)
      |    --max-depth N    ablate goals at nesting depth <= N; N may be `inf` (default: 1)
      |    --leaves-only    only ablate goals whose proof has no nested goal
      |    --min-size N     only ablate proofs with >= N proof commands (default: 0)
      |    --max-size N     only ablate proofs with <= N proof commands; N may be `inf`
      |    --min-centrality N  only ablate lemmas cited by >= N other proofs (corpus fan-in)
      |    --max-centrality N  only ablate lemmas cited by <= N other proofs; N may be `inf`
      |    -p PROB          probability of ablating each selected proof (default: 0.5)
      |    --all            ablate every selected proof (equivalent to -p 1.0)
      |    --count N        ablate exactly min(N, matching) selected proofs per theory
      |                     (mutually exclusive with -p / --all)
      |    --by-centrality  with --count, pick the most-cited proofs (not random)
      |
      |  Other:
      |    -s SESSION       session whose syntax/keywords to parse with (default: HOL)
      |    -d DIR           extra session root directory (repeatable)
      |    --afp DIR        AFP `thys` dir added (-d) for --check-build deps (repeatable)
      |    --keep           keep --check-build working copies
      |    --seed N         RNG seed for reproducibility (default: nondeterministic)
      |    --compact        emit strict one-object-per-line JSONL (no indentation)
      |    -q               quiet: suppress incidental progress on stderr
      |""".stripMargin

  def main(args: Array[String]): Unit = {
    var session = "HOL"
    var dirs: List[Path] = Nil
    var seed: Option[Long] = None
    var quiet = false
    var check = false
    var compact = false
    var keep = false
    var difficulty: Option[String] = None
    var probOpt: Option[Double] = None
    var countOpt: Option[Int] = None
    var minDepthOpt: Option[Int] = None
    var maxDepthOpt: Option[Int] = None
    var leavesOpt: Option[Boolean] = None
    var minSizeOpt: Option[Int] = None
    var maxSizeOpt: Option[Int] = None
    var minCentralityOpt: Option[Int] = None
    var maxCentralityOpt: Option[Int] = None
    var byCentrality = false
    val paths = new mutable.ListBuffer[String]
    val build_targets = new mutable.ListBuffer[String]
    val afp_dirs = new mutable.ListBuffer[String]

    var rest = args.toList
    while (rest.nonEmpty) {
      rest match {
        case "--check" :: tl => check = true; rest = tl
        case "--check-build" :: v :: tl => build_targets += v; rest = tl
        case "--afp" :: v :: tl => afp_dirs += v; rest = tl
        case "--keep" :: tl => keep = true; rest = tl
        case "--compact" :: tl => compact = true; rest = tl
        case "--difficulty" :: v :: tl => difficulty = Some(v); rest = tl
        case "--min-depth" :: v :: tl => minDepthOpt = Some(parse_depth(v)); rest = tl
        case "--max-depth" :: v :: tl => maxDepthOpt = Some(parse_depth(v)); rest = tl
        case "--leaves-only" :: tl => leavesOpt = Some(true); rest = tl
        case "--min-size" :: v :: tl => minSizeOpt = Some(parse_depth(v)); rest = tl
        case "--max-size" :: v :: tl => maxSizeOpt = Some(parse_depth(v)); rest = tl
        case "--min-centrality" :: v :: tl => minCentralityOpt = Some(parse_depth(v)); rest = tl
        case "--max-centrality" :: v :: tl => maxCentralityOpt = Some(parse_depth(v)); rest = tl
        case "--by-centrality" :: tl => byCentrality = true; rest = tl
        case "-s" :: v :: tl => session = v; rest = tl
        case "-d" :: v :: tl => dirs = dirs ::: List(Path.explode(v)); rest = tl
        case "-p" :: v :: tl => probOpt = Some(v.toDouble); rest = tl
        case "--count" :: v :: tl => countOpt = Some(v.toInt); rest = tl
        case "--seed" :: v :: tl => seed = Some(v.toLong); rest = tl
        case "--all" :: tl => probOpt = Some(1.0); rest = tl
        case "-q" :: tl => quiet = true; rest = tl
        case ("-h" | "--help") :: _ => println(usage); return
        case arg :: tl if arg.startsWith("-") && arg != "-" =>
          Console.err.println("Unknown option: " + arg); Console.err.println(usage); sys.exit(2)
        case arg :: tl => paths += arg; rest = tl
        case Nil =>
      }
    }

    val preset = difficulty.flatMap(preset_of)
    if (difficulty.isDefined && preset.isEmpty) {
      Console.err.println(s"Unknown --difficulty ${difficulty.get} (expected L0..L${LADDER.length - 1})")
      sys.exit(2)
    }
    if (countOpt.isDefined && probOpt.isDefined) {
      Console.err.println("--count cannot be combined with -p / --all (they set the rate two ways)")
      sys.exit(2)
    }
    if (byCentrality && countOpt.isEmpty) {
      Console.err.println("--by-centrality only applies with --count"); sys.exit(2)
    }
    val spec = Spec(
      prob = probOpt.orElse(preset.map(_.prob)).getOrElse(0.5),
      count = countOpt,
      by_centrality = byCentrality,
      min_depth = minDepthOpt.orElse(preset.map(_.min_depth)).getOrElse(1),
      max_depth = maxDepthOpt.orElse(preset.map(_.max_depth)).getOrElse(1),
      leaves_only = leavesOpt.orElse(preset.map(_.leaves_only)).getOrElse(false),
      min_size = minSizeOpt.getOrElse(0),
      max_size = maxSizeOpt.getOrElse(Int.MaxValue),
      min_centrality = minCentralityOpt.getOrElse(0),
      max_centrality = maxCentralityOpt.getOrElse(Int.MaxValue))

    if (spec.min_depth < 1) { Console.err.println("--min-depth must be >= 1"); sys.exit(2) }
    if (paths.isEmpty && build_targets.isEmpty) { Console.err.println(usage); sys.exit(2) }

    val progress = if (quiet) new Progress else new Console_Progress()
    val syntax = load_syntax(session, dirs, progress)
    val base = seed.getOrElse(new Random().nextLong())

    def spec_line: String =
      s"depth ${spec.min_depth}..${depth_json(spec.max_depth)}" +
        (if (spec.leaves_only) " leaves-only" else "") +
        (if (spec.min_size > 0 || spec.max_size != Int.MaxValue)
          s" size ${spec.min_size}..${depth_json(spec.max_size)}" else "") +
        (if (spec.uses_centrality)
          s" centrality ${spec.min_centrality}..${depth_json(spec.max_centrality)}" +
            (if (spec.by_centrality) " by-centrality" else "") else "") +
        spec.count.map(c => s" count=$c").getOrElse(f" p=${spec.prob}%.2f") +
        difficulty.map(d => s" [$d]").getOrElse("")

    // build-validation mode
    if (build_targets.nonEmpty) {
      if (!quiet) progress.echo(s"[session $session; $spec_line; ${build_targets.length} build target(s)]")
      val ok = CheckBuild.run(syntax, build_targets.toList.map(Path.explode),
        afp_dirs.toList.map(Path.explode), spec, base, keep, progress)
      if (!ok) sys.exit(1)
      return
    }

    val theories = collect_theories(paths.toList)
    if (!quiet) progress.echo(s"[session $session: ${syntax.keywords.kinds.size} keywords; " +
      s"${theories.length} theories; $spec_line]")

    // corpus fan-in over all input theories: always for the emit path (so the
    // metadata is complete for post-hoc stratification), and for --check only
    // when a centrality filter is actually in play.
    val centrality: String => Int =
      if (!check || spec.uses_centrality) {
        val fan = Centrality.fan_in(syntax, theories, progress)
        (name => fan.getOrElse(name, 0))
      } else (_ => 0)

    if (check) {
      val ok = Check.run(syntax, theories, spec, centrality)
      if (!ok) sys.exit(1)
      return
    }

    var emitted = 0
    for (thy <- theories) {
      val original = File.read(thy)
      val rng = new Random(base ^ thy.implode.hashCode.toLong)
      val result = ablate(syntax, original, spec, rng, centrality)
      val obj = record(thy, session, spec, base, difficulty, original, result)
      System.out.println(if (compact) JSON.Format(obj) else JSON.Format.pretty_print(obj))
      emitted += 1
    }
    System.out.flush()
    if (!quiet) progress.echo(s"[emitted $emitted records]")
  }
}
