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

  // Seeded FNV-1a hash of a statement (XOR the per-run cutSalt) -> a reproducible
  // pseudo-random value used to pick an apply-script cut point (per (seed, lemma)).
  private def cutHash(s: String, salt: Long): Long = {
    var h = 0xcbf29ce484222325L ^ salt
    for (b <- s.getBytes("UTF-8")) { h ^= (b & 0xff).toLong; h *= 0x100000001b3L }
    h
  }

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
    max_centrality: Int = Int.MaxValue,
    truncate: Boolean = false,         // drop everything after the last inserted `sorry`
    shrink_challenge: Boolean = false, // drop challenge top-level goals after the N-th hole
    shrink_solution: Boolean = false,  // drop solution top-level goals after the N-th hole
    shrink_challenge_minimal: Boolean = false, // challenge: keep only N holes + dep closure
    shrink_solution_minimal: Boolean = false,  // solution: keep only N holes' decls + dep closure
    delete_lemmas: Boolean = false,    // delete eligible used lemmas + ablate their users
    delete_count: Option[Int] = None,  // delete-lemmas: delete exactly this many lemmas (None = count/prob)
    delete_uniform: Boolean = false,   // delete-lemmas: draw deletions uniformly, not by user count
    delete_leaves: Boolean = false,    // delete-lemmas: hole only leaf steps (falls back to whole proof)
    aggressive: Boolean = false,       // delete-lemmas: relax guards (BE; needs --check-build)
    corollary: Boolean = false,        // delete-lemmas: restrict candidates to one random theorem's dep closure
    ablate_scripts: Boolean = false    // ablate apply-scripts whole, not the default prefix-cut
  ) {
    def uses_centrality: Boolean =
      min_centrality > 0 || max_centrality != Int.MaxValue || by_centrality
  }

  /** A removed proof and its difficulty signals. */
  sealed case class Hole(theorem_name: String, depth: Int, n_commands: Int, n_lines: Int,
    is_leaf: Boolean, centrality: Int, method: String, proof_text: String)

  sealed case class Result(text: String, solution: String, total: Int, ablated: Int,
    holes: List[Hole], deleted: List[(String, String)] = Nil)

  // Measured properties of a goal body.
  private sealed case class Body(end: Int, lead: String, text: String,
    n_commands: Int, is_leaf: Boolean, method: String, clean: Boolean)

  // Drop top-level goal segments that start after the last ablated top-level
  // goal (keeping definitions / `end` / comments). The same operation shrinks
  // either the challenge or the solution — only the offsets in `segs` differ.
  // `segs` is the list of top-level (end-offset, is-closer, had-sorry) in order.
  private def shrink(full: String, segs: List[(Int, Boolean, Boolean)], count: Option[Int]): String = {
    // cut at the last hole, or with count=Some(n) the n-th hole (keep first n holes)
    val last = {
      var seen = 0
      var l = -1
      for (((_, _c, had), i) <- segs.zipWithIndex if had) {
        seen += 1
        if (count.forall(seen <= _)) l = i
      }
      l
    }
    if (last < 0) full
    else {
      val sb = new mutable.StringBuilder
      var prev = 0
      var idx = 0
      var gap = false   // dropped a segment since the last kept one
      for ((end, is_closer, _) <- segs) {
        if (idx <= last || is_closer) {
          // a kept closer after a gap (e.g. `end`) must not glue onto the
          // previous token — ensure a newline separates them.
          if (gap && sb.nonEmpty && sb.last != '\n') sb += '\n'
          sb ++= full.substring(prev, end)
          gap = false
        } else gap = true
        prev = end
        idx += 1
      }
      // collapse the blank-line runs left where dropped decls used to be
      sb.toString.replaceAll("\n[ \t]*\n([ \t]*\n)+", "\n\n")
    }
  }

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

    val cutSalt: Long = rng.nextLong() // seeds the apply-script cut point (reproducible)

    // Top-level (depth `base`) apply/apply_end step indices of a proof body [start,end).
    // Steps inside a nested have/show/{ are excluded (depth tracked as in `measure`).
    def applySteps(start: Int, end: Int, base: Int): List[Int] = {
      val steps = mutable.ListBuffer[Int]()
      var j = start; var dep = base
      while (j < end) {
        kind_of(spans(j)) match {
          case Some(k) =>
            if (dep == base && (spans(j).name == "apply" || spans(j).name == "apply_end"))
              steps += j
            if (Keyword.proof_goal.contains(k) || k == Keyword.PRF_OPEN) dep += 1
            else if (closes(k)) dep -= 1
            else if (closes_global(k)) dep = base - 1
          case None =>
        }
        j += 1
      }
      steps.toList
    }

    // One pass; `decide(stmt_index)` says whether to ablate each matching proof.
    // Returns the result plus, for every matching proof, (stmt index, centrality).
    def walk_all(decide: Int => Boolean): (Result, List[(Int, Int)]) = {
    val out = new mutable.StringBuilder
    val holes = new mutable.ListBuffer[Hole]
    val matches = new mutable.ListBuffer[(Int, Int)]
    // (challenge end offset, original end offset, is goal, had sorry)
    val top_segs = new mutable.ListBuffer[(Int, Int, Boolean, Boolean)]
    var last_sorry_end = -1                                          // out offset just after the last `sorry`
    var orig = 0                                                     // chars of the original consumed so far
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
          // Apply-script: keep a seeded-random prefix of `apply` steps and admit the
          // rest with `sorry` (default). Otherwise (or --ablate-scripts, or a single
          // step) sit `sorry` right after the statement, dropping the whole proof.
          val steps = applySteps(i, m.end, goal_depth)
          val keepK =
            if (!spec.ablate_scripts && steps.length >= 2)
              1 + Math.floorMod(cutHash(src(stmt_idx), cutSalt), (steps.length - 1).toLong).toInt
            else 0
          if (keepK > 0) out ++= (i until steps(keepK - 1) + 1).map(src).mkString
          out ++= " sorry"
          last_sorry_end = out.length
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
        case Some(k) if is_goal(k) =>
          val ablated0 = ablated
          val start = i
          handle_goal()
          for (j <- start until i) orig += src(j).length
          // a goal is never a structural closer
          top_segs += ((out.length, orig, false, ablated > ablated0))
        case _ =>
          // keep `end` closers AND block openers (kind thy_decl_block, which absorb
          // their `begin`) so shrink keeps a balanced theory/locale/context skeleton.
          val closer = spans(i).name == "end" ||
            spans(i).kind.keyword_kind.contains(Keyword.THY_DECL_BLOCK)
          orig += src(i).length
          out ++= src(i); i += 1
          top_segs += ((out.length, orig, closer, false))
      }
    }

    // optional context shaping (--check / --check-build disable these). Each
    // segment carries both its challenge- and original-offset, so the challenge
    // and the solution can be shrunk independently.
    val full = out.toString
    val original = text.toString
    val chal_segs = top_segs.toList.map { case (c, _, g, a) => (c, g, a) }
    val sol_segs = top_segs.toList.map { case (_, o, g, a) => (o, g, a) }
    val shaped_text =
      if (spec.truncate && last_sorry_end >= 0) full.substring(0, last_sorry_end)
      else if (spec.shrink_challenge) shrink(full, chal_segs, spec.count)
      else full
    val solution =
      if (spec.shrink_solution) shrink(original, sol_segs, spec.count)
      else original

    (Result(shaped_text, solution, total, ablated, holes.toList), matches.toList)
    }  // walk_all

    if (spec.delete_lemmas) ablate_delete(syntax, text, spec, rng)
    else spec.count match {
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

  /** --delete-lemmas: delete eligible used lemmas + whole-proof-ablate users.
   *  Correct-by-construction (see uses.ml / uses.rs): a lemma is deletable only
   *  when every occurrence of its name outside its block is in a proof body, it
   *  closes normally, carries no attribute (`lemma foo [simp]:`), and has ≥1
   *  in-file user. `aggressive` relaxes the normal-close requirement. */
  private def ablate_delete(syntax: Outer_Syntax, text: CharSequence, spec: Spec, rng: Random): Result = {
    val spans = syntax.parse_spans(text).toArray
    val n = spans.length
    def src(j: Int): String = Token.implode(spans(j).content)
    def names_in(content: List[Token]): List[String] =
      content.filter(t => t.is_ident || t.kind == Token.Kind.LONG_IDENT).flatMap { t =>
        val s = t.source; val dot = s.lastIndexOf('.')
        if (dot >= 0 && dot < s.length - 1) List(s, s.substring(dot + 1)) else List(s)
      }
    def has_attr(span: Command_Span.Span): Boolean =
      span.content.filter(_.is_proper).takeWhile(t => !t.is_keyword(":")).exists(_.is_keyword("["))

    final case class Goal(opener: Int, end: Int, name: String, normal: Boolean,
      attr: Boolean, cited: Set[String])
    val goalsBuf = new mutable.ListBuffer[Goal]
    val nonproof = new mutable.ListBuffer[(String, Int)]
    var i = 0
    while (i < n) {
      val k = spans(i).kind.keyword_kind
      if (k.exists(Keyword.theory_goal.contains)) {
        val opener = i
        val name = goal_name(spans(i))
        val attr = has_attr(spans(i))
        for (s <- names_in(spans(i).content)) nonproof += ((s, opener))
        i += 1
        var depth = 1; var normal = false; val cited = mutable.Set.empty[String]
        while (i < n && depth > 0) {
          for (s <- names_in(spans(i).content)) cited += s
          spans(i).kind.keyword_kind match {
            case Some(kk) if Keyword.theory.contains(kk) => depth = 0
            case Some(kk) =>
              if (Keyword.proof_goal.contains(kk) || kk == Keyword.PRF_OPEN) depth += 1
              else if (Keyword.proof_close.contains(kk)) { depth -= 1; if (depth == 0) normal = true }
              else if (Keyword.qed_global.contains(kk)) depth = 0
            case None =>
          }
          i += 1
        }
        goalsBuf += Goal(opener, i, name, normal, attr, cited.toSet)
      } else { for (s <- names_in(spans(i).content)) nonproof += ((s, i)); i += 1 }
    }
    val goals = goalsBuf.toList
    val np = nonproof.toList
    final case class Lemma(name: String, opener: Int, end: Int, users: List[Int],
      stmtNames: List[String], bodyNames: List[String], eligible: Boolean)
    val lemmas = goals.map { g =>
      val users = goals.collect { case g2 if g2.opener != g.opener && g2.cited.contains(g.name) => g2.opener }
      val onlyProofs = np.forall { case (s, j) => s != g.name || (j >= g.opener && j < g.end) }
      val eligible = (g.normal || spec.aggressive) && !g.attr && g.name.length >= 3 &&
        users.nonEmpty && onlyProofs
      Lemma(g.name, g.opener, g.end, users, names_in(spans(g.opener).content), g.cited.toList, eligible)
    }
    val totalEligible = lemmas.count(_.eligible)
    val cands = lemmas.filter(l => l.eligible &&
      l.users.length >= spec.min_centrality && l.users.length <= spec.max_centrality)
    // `--count k` is a target number of *ablations* (holed users), not deletions:
    // deleting a lemma forces every one of its users to be ablated, so ablations
    // arrive in chunks. We draw deletions at random *without replacement*, each with
    // probability proportional to its weight (user count — or 1 under delete_uniform),
    // accumulating their forced ablations until the distinct total reaches >= k, then
    // stop. Seed-driven (diverse evals), favours popular lemmas, yet keeps a non-zero
    // chance on the tail. Without --count the per-lemma prob coin decides. (With
    // --truncate we later keep only the first k.)
    def weight(l: Lemma): Double = if (spec.delete_uniform) 1.0 else l.users.length.toDouble
    // index of one weighted pick (proportional to `weight`) from a non-empty vector
    def pickIdx(remaining: Vector[Lemma]): Int = {
      val total = remaining.iterator.map(weight).sum
      val r = rng.nextDouble() * total
      var acc = 0.0; var idx = remaining.length - 1; var found = false; var j = 0
      while (j < remaining.length && !found) {
        acc += weight(remaining(j))
        if (acc > r) { idx = j; found = true }
        j += 1
      }
      idx
    }
    // Corollary mode: pick a random theorem, take its transitive in-file dependency
    // closure, and draw deletions from the *eligible* members of that closure (fan-in
    // weighted, or uniform). One corollary is exhausted before a fresh one is drawn, so
    // deletions stay concentrated in a single proof's subtree unless the target forces
    // spilling. Mirrors the rust ablator's corollary_select.
    def corollarySelect(): List[Lemma] = {
      if (cands.isEmpty) Nil
      else {
        val byNameFirst = lemmas.foldLeft(Map.empty[String, Lemma]) {
          (m, l) => if (m.contains(l.name)) m else m + (l.name -> l)
        }
        def depsOf(l: Lemma): List[Lemma] =
          (l.stmtNames ++ l.bodyNames).flatMap(byNameFirst.get)
            .filter(_.opener != l.opener).distinct
        val candOpeners = cands.map(_.opener).toSet
        def closureCands(start: Lemma): Vector[Lemma] = {
          val seen = mutable.Set.empty[Int]
          val acc = new mutable.ListBuffer[Lemma]
          var stack = depsOf(start)
          while (stack.nonEmpty) {
            val l = stack.head; stack = stack.tail
            if (l.opener != start.opener && seen.add(l.opener)) {
              acc += l
              stack = depsOf(l) ::: stack
            }
          }
          acc.toVector.filter(l => candOpeners.contains(l.opener)).sortBy(_.opener)
        }
        val order = rng.shuffle(lemmas.toVector)
        val del = mutable.Set.empty[Int]
        val chosen = new mutable.ListBuffer[Lemma]
        val targetDeletions = spec.delete_count
        val targetAblations = if (spec.delete_count.isEmpty) spec.count else None
        val useProb = spec.delete_count.isEmpty && spec.count.isEmpty
        def coveredCount: Int = chosen.flatMap(_.users).filterNot(del.contains).distinct.size
        def reached: Boolean = (targetDeletions, targetAblations) match {
          case (Some(nd), _) => chosen.length >= nd
          case (_, Some(k))  => coveredCount >= k
          case _ => false
        }
        var oi = 0; var stop = false
        while (oi < order.length && !stop) {
          val cor = order(oi); oi += 1
          if (useProb) {
            val pool = closureCands(cor).filterNot(l => del.contains(l.opener))
            if (pool.nonEmpty) {
              for (pp <- pool if rng.nextDouble() < spec.prob) { del += pp.opener; chosen += pp }
              stop = true
            }
          } else if (reached) stop = true
          else {
            var pool = closureCands(cor).filterNot(l => del.contains(l.opener))
            while (pool.nonEmpty && !reached) {
              val idx = pickIdx(pool); val l = pool(idx)
              del += l.opener; chosen += l
              pool = pool.patch(idx, Nil, 1)
            }
          }
        }
        chosen.toList
      }
    }
    val selected: List[Lemma] = if (spec.corollary) corollarySelect()
    else spec.delete_count match {
      // --delete-lemmas N: delete exactly N lemmas (weighted draw), any ablation count
      case Some(kd) =>
        val chosen = new mutable.ListBuffer[Lemma]
        var remaining = cands.toVector
        val target = math.min(kd, cands.length)
        while (chosen.length < target && remaining.nonEmpty) {
          val idx = pickIdx(remaining); chosen += remaining(idx); remaining = remaining.patch(idx, Nil, 1)
        }
        chosen.toList
      case None => spec.count match {
        case None => cands.filter(_ => rng.nextDouble() < spec.prob)
        case Some(0) => Nil
        case Some(k) =>
          val covered = mutable.Set.empty[Int]
          val chosen = new mutable.ListBuffer[Lemma]
          var remaining = cands.toVector
          while (covered.size < k && remaining.nonEmpty) {
            val idx = pickIdx(remaining)
            val l = remaining(idx)
            covered ++= l.users; chosen += l
            remaining = remaining.patch(idx, Nil, 1)
          }
          chosen.toList
      }
    }
    val delSet = selected.map(_.opener).toSet
    // distinct forced ablations (users not themselves deleted), in file order
    val usersSorted = selected.flatMap(_.users).filterNot(delSet.contains).distinct.sorted
    // with --truncate + --count, ablate EXACTLY the first k and cut the rest;
    // otherwise every user must be ablated (a dangling reference would not compile).
    val userSet = ((spec.truncate, spec.count) match {
      case (true, Some(k)) => usersSorted.take(k)
      case _ => usersSorted
    }).toSet
    val byOpener = lemmas.map(l => l.opener -> l).toMap
    val deletedNamesSet = selected.map(_.name).toSet

    // --- leaf-level user ablation (--delete-lemmas-leaves), mirroring the rust ablator.
    // Hole the smallest enclosing structured step (have/show/…) citing a deleted name at
    // its own level; whole-proof fallback when a top-level citation can't be isolated.
    // Boundaries mirror the tested depth logic (proof_goal/proof_close/qed_global).
    def measureEnd(start: Int, d: Int): Int = {
      var jj = start; var dep = d + 1; var stop = false
      while (jj < n && dep > d && !stop) {
        kind_of(spans(jj)) match {
          case Some(k) if is_theory(k) => stop = true
          case Some(k) =>
            if (Keyword.proof_goal.contains(k) || k == Keyword.PRF_OPEN) dep += 1
            else if (closes(k)) dep -= 1
            else if (closes_global(k)) dep = d
            jj += 1
          case None => jj += 1
        }
      }
      jj
    }
    def spanCites(idx: Int): Boolean = names_in(spans(idx).content).exists(deletedNamesSet.contains)
    def ownCites(lo: Int, hi: Int, d: Int): Boolean = {
      var jj = lo; var found = false
      while (jj < hi && !found) {
        kind_of(spans(jj)) match {
          case Some(k) if Keyword.proof_goal.contains(k) => jj = measureEnd(jj + 1, d + 1)
          case _ => if (spanCites(jj)) found = true else jj += 1
        }
      }
      found
    }
    def subtreeCites(lo: Int, hi: Int): Boolean = (lo until hi).exists(spanCites)
    // First OWN-level (depth d) span citing a deleted name (skips nested goal-units);
    // -1 if none. Used to cut an apply-script at the step using a deleted lemma.
    def firstCitingToplevel(lo: Int, hi: Int, d: Int): Int = {
      var jj = lo; var res = -1
      while (jj < hi && res < 0) {
        kind_of(spans(jj)) match {
          case Some(k) if Keyword.proof_goal.contains(k) => jj = measureEnd(jj + 1, d + 1)
          case _ => if (spanCites(jj)) res = jj else jj += 1
        }
      }
      res
    }
    def leafRender(lo: Int, hi: Int, d: Int): (String, Boolean) = {
      val sb = new mutable.StringBuilder; var ok = true; var jj = lo
      while (jj < hi) {
        kind_of(spans(jj)) match {
          case Some(k) if Keyword.proof_goal.contains(k) =>
            val ue = measureEnd(jj + 1, d + 1)
            if (subtreeCites(jj, ue)) {
              if (ownCites(jj + 1, ue, d + 1)) { sb ++= src(jj); sb ++= " sorry" }
              else { sb ++= src(jj); val (sub, sok) = leafRender(jj + 1, ue, d + 1); sb ++= sub; ok &= sok }
            } else sb ++= (jj until ue).map(src).mkString
            jj = ue
          case _ =>
            if (spanCites(jj)) ok = false
            sb ++= src(jj); jj += 1
        }
      }
      (sb.toString, ok)
    }
    def renderUser(opener: Int, end: Int): String =
      if (spec.delete_leaves) {
        val (body, ok) = leafRender(opener + 1, end, 0)
        if (ok) src(opener) + body
        else if (!spec.ablate_scripts) {
          // apply-script: cut at the first top-level step citing the deleted name,
          // keeping the citation-free prefix + sorry.
          val cut = firstCitingToplevel(opener + 1, end, 0)
          if (cut >= 0 && !subtreeCites(opener + 1, cut))
            src(opener) + (opener + 1 until cut).map(src).mkString + " sorry"
          else src(opener) + " sorry"
        } else src(opener) + " sorry"
      } else src(opener) + " sorry"

    // Emit the challenge + record per-item challenge/solution segments (offset, closer,
    // hadHole) so --shrink-* can trim each side to the first N holes. With
    // --delete-lemmas-leaves, renderUser holes the smallest enclosing citing step.
    val out = new mutable.StringBuilder
    val holes = new mutable.ListBuffer[Hole]
    val deleted = new mutable.ListBuffer[(String, String)]
    var ablated = 0
    var last_sorry_end = -1
    val chal_segs = new mutable.ListBuffer[(Int, Boolean, Boolean)]
    val sol_segs = new mutable.ListBuffer[(Int, Boolean, Boolean)]
    var orig_len = 0
    var j = 0
    while (j < n) {
      val isGoal = spans(j).kind.keyword_kind.exists(Keyword.theory_goal.contains)
      if (isGoal && byOpener.contains(j)) {
        val l = byOpener(j); val e = l.end
        val itemSrc = (j until e).map(src).mkString
        if (delSet.contains(j)) {
          deleted += ((l.name, itemSrc))
          orig_len += itemSrc.length; sol_segs += ((orig_len, false, false))
        } else if (userSet.contains(j)) {
          out ++= renderUser(j, e)
          last_sorry_end = out.length
          val proofText = (j + 1 until e).map(src).mkString
          holes += Hole(l.name, 1, 0, n_lines(proofText), true, 0, "deleted-dep", proofText)
          ablated += 1
          chal_segs += ((out.length, false, true))
          orig_len += itemSrc.length; sol_segs += ((orig_len, false, true))
        } else {
          out ++= itemSrc
          chal_segs += ((out.length, false, false))
          orig_len += itemSrc.length; sol_segs += ((orig_len, false, false))
        }
        j = e
      } else {
        val s = src(j)
        val closer = spans(j).name == "end" ||
          spans(j).kind.keyword_kind.contains(Keyword.THY_DECL_BLOCK)
        out ++= s
        chal_segs += ((out.length, closer, false))
        orig_len += s.length; sol_segs += ((orig_len, closer, false))
        j += 1
      }
    }
    val raw = out.toString
    val original = text.toString
    val collapse = (x: String) => x.replaceAll("\n[ \t]*\n([ \t]*\n)+", "\n\n")

    // Minimal dependency-closed slice (mirrors rocq slice_delete).
    def sliceDelete(solution: Boolean): String = {
      val openerOfName = lemmas.foldLeft(Map.empty[String, Int]) {
        case (m, l) => if (m.contains(l.name)) m else m + (l.name -> l.opener)
      }
      val deletedNames = selected.map(_.name).toSet
      def mustHole(o: Int): Boolean = byOpener.get(o).exists(_.bodyNames.exists(deletedNames.contains))
      val seedAll = userSet.toList.sorted
      val seed = spec.count match { case Some(k) => seedAll.take(k); case None => seedAll }
      // Keep-set computed BEFORE ablating, shared by challenge & solution: the full
      // statement+body closure of the holes over the original. Keeps every lemma the
      // real proofs need (never throws away more than the deleted lemma); deleted
      // lemma stays in the set (restored in solution, omitted from challenge).
      val keep = mutable.Set.empty[Int]
      val q = mutable.Queue.empty[Int]
      def add(o: Int): Unit =
        if (!keep.contains(o) && byOpener.contains(o)) { keep += o; q.enqueue(o) }
      seed.foreach(add)
      while (q.nonEmpty) {
        val o = q.dequeue(); val l = byOpener(o)
        for (nm <- l.stmtNames ++ l.bodyNames; o2 <- openerOfName.get(nm)) add(o2)
      }
      val sb = new mutable.StringBuilder
      var k = 0
      while (k < n) {
        val isGoal = spans(k).kind.keyword_kind.exists(Keyword.theory_goal.contains)
        if (isGoal && byOpener.contains(k)) {
          val e = byOpener(k).end
          if (keep.contains(k)) {
            if (!solution && delSet.contains(k)) () // deleted lemma: omitted from challenge
            else if (!solution && (mustHole(k) || userSet.contains(k))) sb ++= renderUser(k, byOpener(k).end)
            else sb ++= (k until e).map(src).mkString
          }
          k = e
        } else { sb ++= src(k); k += 1 }
      }
      collapse(sb.toString)
    }

    val shaped_text =
      if (spec.truncate && last_sorry_end >= 0) collapse(raw.substring(0, last_sorry_end))
      else if (spec.shrink_challenge_minimal) sliceDelete(false)
      else if (spec.shrink_challenge) shrink(raw, chal_segs.toList, spec.count)
      else collapse(raw)
    val solution =
      if (spec.shrink_solution_minimal) sliceDelete(true)
      else if (spec.shrink_solution) shrink(original, sol_segs.toList, spec.count)
      else original
    Result(shaped_text, solution, totalEligible, ablated, holes.toList, deleted.toList)
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

  private def task_id(file_path: String, variant: Option[Int]): String =
    "ablate_" + SHA1.digest(file_path).toString.substring(0, 12) + variant.map("_" + _).getOrElse("")

  private def theory_name(file_path: String): String = {
    val b = file_path.split('/').lastOption.getOrElse(file_path)
    if (b.endsWith(".thy")) b.dropRight(4) else b
  }

  private def depth_json(d: Int): JSON.T = if (d == Int.MaxValue) "inf" else d

  def record(file_path: String, session: String, spec: Spec, seed: Long, variant: Option[Int],
    difficulty: Option[String], result: Result): JSON.Object.T = {
    val holes: List[JSON.T] =
      result.holes.map(h => ListMap[String, JSON.T](
        "theorem_name" -> h.theorem_name, "depth" -> h.depth, "n_commands" -> h.n_commands,
        "n_lines" -> h.n_lines, "is_leaf" -> h.is_leaf, "centrality" -> h.centrality,
        "method" -> h.method, "proof_text" -> h.proof_text))
    val deleted_lemmas: List[JSON.T] =
      result.deleted.map { case (nm, txt) => ListMap[String, JSON.T]("name" -> nm, "text" -> txt) }
    ListMap[String, JSON.T](
      "task_id" -> task_id(file_path, variant),
      "proof_assistant" -> "isabelle",
      "session" -> session,
      "file_path" -> file_path,
      "theory" -> theory_name(file_path),
      "variant" -> variant.map(v => v: JSON.T).getOrElse(null),
      "challenge_type" -> (if (result.deleted.isEmpty) "proof_ablate" else "lemma_delete"),
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
      "deleted_lemmas" -> deleted_lemmas,
      "challenge_file_content" -> result.text,
      // solution stored as a diff against the challenge (apply to recover) — full
      // files are huge for big theories (issue #107)
      "solution_diff" -> Diff.unified(result.text, result.solution))
  }

  /** Single-theory compile-test used by `--aggressively-delete-lemmas`: write
   *  `content` at `thy`, build a throwaway session that lists only that theory
   *  (so `isabelle build` resolves its imports but never builds its dependents —
   *  the upward closure only), then restore the original. `quick_and_dirty` lets
   *  a challenge's `sorry`s elaborate. Returns whether the build succeeded.
   *
   *  (The heavyweight `--check-build` certifies a whole renamed session; this is
   *  the cheap per-row check the delete-lemmas pipeline needs.) */
  def check_compiles(thy: Path, content: String): Boolean = {
    val toks = content.split("\\s+").iterator.filter(_.nonEmpty).toList
    val name = toks.indexOf("theory") match {
      case i if i >= 0 && i + 1 < toks.length => toks(i + 1)
      case _ => ""
    }
    if (name.isEmpty) return false
    val dir = thy.absolute.file.getParentFile.getAbsolutePath
    val tmp = Isabelle_System.tmp_dir("ablate-check")
    val root =
      "session \"AblateCheck\" = \"HOL\" +\n" +
      "  directories \"" + dir + "\"\n" +
      "  theories\n    \"" + name + "\"\n"
    java.nio.file.Files.write(new java.io.File(tmp, "ROOT").toPath, root.getBytes("UTF-8"))
    val orig = File.read(thy)
    val ok =
      try {
        File.write(thy, content)
        Isabelle_System.bash(
          "\"$ISABELLE_HOME/bin/isabelle\" build -o quick_and_dirty=true -d " +
          Bash.string(tmp.getAbsolutePath) + " AblateCheck").ok
      } finally File.write(thy, orig)
    Isabelle_System.rm_tree(tmp)
    ok
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
      |  Lemma deletion (instead of per-proof ablation):
      |    --delete-lemmas [N] delete eligible used lemmas + ablate their users. Optional
      |                        N deletes exactly N lemmas (weighted); works on every
      |                        --delete-* flag. Omit N for the --count/-p behavior. With
      |                        --count N, deletions are drawn weighted by in-file user
      |                        count until >= N ablations result (seed-driven; popular
      |                        lemmas favoured, the tail still reachable).
      |    --delete-lemmas-uniform
      |                        like --delete-lemmas but draw deletions uniformly.
      |    --delete-lemmas-leaves
      |                        like --delete-lemmas; leaf-level holing is deferred to the
      |                        heavyweight semantic ablator, so falls back to whole-proof.
      |    --aggressively-delete-lemmas
      |                        as above, relaxed guards, validated with `isabelle build`
      |                        (drops non-compiling challenges; needs the HOL heap)
      |    --corollary-delete-lemmas [N]
      |                        like --delete-lemmas but restrict deletions to one random
      |                        theorem's (a "corollary") transitive in-file dependency
      |                        closure (fan-in weighted; re-picks a corollary only when the
      |                        closure runs dry). Variants: -uniform, -leaves.
      |    --ablate-scripts    ablate apply-scripts whole (drop the entire script)
      |                        instead of the default prefix-cut (keep some `apply`
      |                        steps, `sorry` the rest)
      |
      |  Context shaping (ignored by --check / --check-build):
      |    --truncate          drop challenge text after the last inserted `sorry`
      |    --shrink-challenge  drop challenge top-level lemmas/theorems after the N-th hole
      |    --shrink-solution   same, for the solution
      |    --shrink-challenge-minimal / --shrink-solution-minimal
      |                        keep only the N holes + their dependency closure (drop
      |                        unrelated decls); solution restores the deleted lemma + deps
      |
      |  Other:
      |    -s SESSION       session whose syntax/keywords to parse with (default: HOL)
      |    -d DIR           extra session root dir; also stripped from emitted paths (repeatable)
      |    --afp DIR        AFP `thys` dir added (-d) for --check-build deps (repeatable)
      |    --keep           keep --check-build working copies
      |    --repeat N       emit up to N deduplicated ablations per theory (default: 1)
      |    --seed N         RNG seed for reproducibility (default: nondeterministic)
      |    --text           output the ablated theory text instead of JSONL records
      |    --compact        emit strict one-object-per-line JSONL (no indentation)
      |    -v               verbose: show progress/summary on stderr
      |""".stripMargin

  def main(args: Array[String]): Unit = {
    var session = "HOL"
    var dirs: List[Path] = Nil
    var seed: Option[Long] = None
    var verbose = false
    var check = false
    var compact = false
    var text_mode = false
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
    var truncate = false
    var shrinkChallenge = false
    var shrinkSolution = false
    var shrinkChallengeMinimal = false
    var shrinkSolutionMinimal = false
    var deleteLemmas = false
    var deleteCount: Option[Int] = None
    var deleteUniform = false
    var deleteLeaves = false
    var aggressive = false
    var corollary = false
    var ablateScripts = false
    var repeat = 1
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
        case "--text" :: tl => text_mode = true; rest = tl
        case "--difficulty" :: v :: tl => difficulty = Some(v); rest = tl
        case "--min-depth" :: v :: tl => minDepthOpt = Some(parse_depth(v)); rest = tl
        case "--max-depth" :: v :: tl => maxDepthOpt = Some(parse_depth(v)); rest = tl
        case "--leaves-only" :: tl => leavesOpt = Some(true); rest = tl
        case "--min-size" :: v :: tl => minSizeOpt = Some(parse_depth(v)); rest = tl
        case "--max-size" :: v :: tl => maxSizeOpt = Some(parse_depth(v)); rest = tl
        case "--min-centrality" :: v :: tl => minCentralityOpt = Some(parse_depth(v)); rest = tl
        case "--max-centrality" :: v :: tl => maxCentralityOpt = Some(parse_depth(v)); rest = tl
        case "--by-centrality" :: tl => byCentrality = true; rest = tl
        case "--truncate" :: tl => truncate = true; rest = tl
        case "--shrink-challenge" :: tl => shrinkChallenge = true; rest = tl
        case "--shrink-solution" :: tl => shrinkSolution = true; rest = tl
        case "--shrink-challenge-minimal" :: tl => shrinkChallengeMinimal = true; rest = tl
        case "--shrink-solution-minimal" :: tl => shrinkSolutionMinimal = true; rest = tl
        // each --delete-lemmas* flag optionally takes N (the number of lemmas to delete)
        case "--delete-lemmas" :: n :: tl if n.toIntOption.exists(_ >= 0) =>
          deleteLemmas = true; deleteCount = n.toIntOption; rest = tl
        case "--delete-lemmas" :: tl => deleteLemmas = true; rest = tl
        case "--delete-lemmas-uniform" :: n :: tl if n.toIntOption.exists(_ >= 0) =>
          deleteLemmas = true; deleteUniform = true; deleteCount = n.toIntOption; rest = tl
        case "--delete-lemmas-uniform" :: tl => deleteLemmas = true; deleteUniform = true; rest = tl
        case "--delete-lemmas-leaves" :: n :: tl if n.toIntOption.exists(_ >= 0) =>
          deleteLemmas = true; deleteLeaves = true; deleteCount = n.toIntOption; rest = tl
        case "--delete-lemmas-leaves" :: tl => deleteLemmas = true; deleteLeaves = true; rest = tl
        case "--aggressively-delete-lemmas" :: n :: tl if n.toIntOption.exists(_ >= 0) =>
          deleteLemmas = true; aggressive = true; deleteCount = n.toIntOption; rest = tl
        case "--aggressively-delete-lemmas" :: tl => deleteLemmas = true; aggressive = true; rest = tl
        // corollary mode: deletions restricted to one random theorem's dependency closure
        case "--corollary-delete-lemmas" :: n :: tl if n.toIntOption.exists(_ >= 0) =>
          deleteLemmas = true; corollary = true; deleteCount = n.toIntOption; rest = tl
        case "--corollary-delete-lemmas" :: tl => deleteLemmas = true; corollary = true; rest = tl
        case "--corollary-delete-lemmas-uniform" :: n :: tl if n.toIntOption.exists(_ >= 0) =>
          deleteLemmas = true; corollary = true; deleteUniform = true; deleteCount = n.toIntOption; rest = tl
        case "--corollary-delete-lemmas-uniform" :: tl =>
          deleteLemmas = true; corollary = true; deleteUniform = true; rest = tl
        case "--corollary-delete-lemmas-leaves" :: n :: tl if n.toIntOption.exists(_ >= 0) =>
          deleteLemmas = true; corollary = true; deleteLeaves = true; deleteCount = n.toIntOption; rest = tl
        case "--corollary-delete-lemmas-leaves" :: tl =>
          deleteLemmas = true; corollary = true; deleteLeaves = true; rest = tl
        case "--ablate-scripts" :: tl => ablateScripts = true; rest = tl
        case "--repeat" :: v :: tl => repeat = v.toInt; rest = tl
        case "-s" :: v :: tl => session = v; rest = tl
        case "-d" :: v :: tl => dirs = dirs ::: List(Path.explode(v)); rest = tl
        case "-p" :: v :: tl => probOpt = Some(v.toDouble); rest = tl
        case "--count" :: v :: tl => countOpt = Some(v.toInt); rest = tl
        case "--seed" :: v :: tl => seed = Some(v.toLong); rest = tl
        case "--all" :: tl => probOpt = Some(1.0); rest = tl
        case "-v" :: tl => verbose = true; rest = tl
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
      max_centrality = maxCentralityOpt.getOrElse(Int.MaxValue),
      truncate = truncate,
      // shrinking the solution implies shrinking the challenge (a shrunk solution
      // against a full challenge is meaningless)
      shrink_challenge = shrinkChallenge || shrinkSolution,
      shrink_solution = shrinkSolution,
      shrink_challenge_minimal = shrinkChallengeMinimal || shrinkSolutionMinimal,
      shrink_solution_minimal = shrinkSolutionMinimal,
      delete_lemmas = deleteLemmas,
      delete_count = deleteCount,
      delete_uniform = deleteUniform,
      delete_leaves = deleteLeaves,
      aggressive = aggressive,
      corollary = corollary,
      ablate_scripts = ablateScripts)

    if (spec.min_depth < 1) { Console.err.println("--min-depth must be >= 1"); sys.exit(2) }
    if (paths.isEmpty && build_targets.isEmpty) { Console.err.println(usage); sys.exit(2) }

    // progress to stderr so stdout stays clean for JSONL / --text output
    val progress = if (verbose) new Console_Progress(stderr = true) else new Progress
    // -d dirs serve double duty: those that are session roots (have ROOT/ROOTS)
    // feed session resolution; all of them strip emitted file-path prefixes.
    val session_dirs = dirs.filter(d =>
      (d + Path.basic("ROOT")).file.isFile || (d + Path.basic("ROOTS")).file.isFile)
    val syntax = load_syntax(session, session_dirs, progress)
    val base = seed.getOrElse(new Random().nextLong())
    // java.util.Random produces nearly identical first outputs for small, sequential
    // seeds, which would kill eval diversity when generating with seeds 0,1,2,…. Run
    // the seed through a SplitMix64 finalizer first so consecutive seeds diverge —
    // matching the custom RNGs in the rocq/rust/lean ablators.
    def mix64(z0: Long): Long = {
      var z = z0 + 0x9E3779B97F4A7C15L
      z = (z ^ (z >>> 30)) * 0xBF58476D1CE4E5B9L
      z = (z ^ (z >>> 27)) * 0x94D049BB133111EBL
      z ^ (z >>> 31)
    }

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
      if (verbose) progress.echo(s"[session $session; $spec_line; ${build_targets.length} build target(s)]")
      val ok = CheckBuild.run(syntax, build_targets.toList.map(Path.explode),
        afp_dirs.toList.map(Path.explode), spec, base, keep, progress)
      if (!ok) sys.exit(1)
      return
    }

    val theories = collect_theories(paths.toList)
    if (verbose) progress.echo(s"[session $session: ${syntax.keywords.kinds.size} keywords; " +
      s"${theories.length} theories; $spec_line]")

    // corpus fan-in over all input theories: always for JSONL emit (so the
    // metadata is complete for post-hoc stratification), and otherwise only
    // when a centrality filter is actually in play.
    val centrality: String => Int =
      if (spec.uses_centrality || (!check && !text_mode)) {
        val fan = Centrality.fan_in(syntax, theories, progress)
        (name => fan.getOrElse(name, 0))
      } else (_ => 0)

    if (check) {
      val ok = Check.run(syntax, theories, spec, centrality)
      if (!ok) sys.exit(1)
      return
    }

    // emitted file path: strip the longest matching -d prefix (so JSONL paths
    // are relative, not /home/.../...); otherwise the absolute path.
    val strip = dirs.map(_.absolute.implode).sortBy(-_.length)
    def display_path(thy: Path): String = {
      val abs = thy.absolute.implode
      strip.iterator.flatMap { base =>
        if (abs == base) Some(thy.file.getName)
        else if (abs.startsWith(base + "/")) Some(abs.substring(base.length + 1))
        else None
      }.nextOption().getOrElse(abs)
    }

    val n_repeat = math.max(1, repeat)
    var emitted = 0
    for (thy <- theories) {
      val original = File.read(thy)
      val display = display_path(thy)
      val seen = new mutable.HashSet[String]   // dedup identical ablations of this theory
      var k = 0
      var produced = 0
      while (k < n_repeat) {
        val rng = new Random(mix64(base ^ thy.implode.hashCode.toLong ^ (k.toLong * 0x9E3779B97F4A7C15L)))
        val result = ablate(syntax, original, spec, rng, centrality)
        // aggressive delete-lemmas: only keep challenges that actually compile
        val valid = !spec.aggressive || check_compiles(thy, result.text)
        // only emit *real* challenges: at least one hole was inserted AND the challenge
        // differs from the solution. A theory with no eligible lemmas (or a no-op
        // ablation) otherwise yields a trivial, already-complete challenge that would
        // inflate any downstream baseline
        val nontrivial = result.ablated > 0 && result.text != result.solution
        if (valid && nontrivial && seen.add(result.text)) {  // best-effort dedup across repeats
          if (text_mode) System.out.print(result.text)   // raw ablated theory, byte-exact
          else {
            val variant = if (n_repeat > 1) Some(produced) else None
            val obj = record(display, session, spec, base, variant, difficulty, result)
            System.out.println(if (compact) JSON.Format(obj) else JSON.Format.pretty_print(obj))
          }
          produced += 1
          emitted += 1
        }
        k += 1
      }
    }
    System.out.flush()
    if (verbose) progress.echo(s"[emitted $emitted ${if (text_mode) "theories" else "records"}]")
  }
}
