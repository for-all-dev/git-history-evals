/*  A tiny self-rolled line differ. We store `solution_diff` — a unified diff
    turning the challenge into the solution — instead of the whole solution file,
    which is huge for big theories (l4v: 100k+ chars; see issue #107).

    Convention: lines are `split("\n", -1)` (a trailing newline yields a final ""
    element); `apply` joins them back with "\n", so the round-trip is byte-exact.
    Hunks are `@@ -oldStart,oldLen +newStart,newLen @@` with ` `/`-`/`+` prefixes;
    an empty diff means challenge = solution.

    Algorithm: Myers O(ND) greedy diff capped at `DMax` edits; beyond the cap we
    fall back to a single hunk replacing the differing middle. */

package proofablate

import scala.collection.mutable

object Diff {
  private val DMax = 2000
  private val Context = 3

  private sealed trait Op { def s: String }
  private case class Eq(s: String) extends Op
  private case class Del(s: String) extends Op
  private case class Ins(s: String) extends Op
  private def isChange(o: Op): Boolean = o match { case Eq(_) => false; case _ => true }

  private def myers(a: Array[String], b: Array[String]): Option[Array[Op]] = {
    val n = a.length; val m = b.length
    val maxD = n + m
    if (maxD == 0) return Some(Array.empty[Op])
    val off = maxD
    val v = Array.fill(2 * maxD + 1)(0)
    val trace = mutable.ArrayBuffer.empty[Array[Int]]
    var found = -1
    var d = 0
    var stop = false
    while (d <= maxD && d <= DMax && !stop) {
      trace += v.clone()
      var k = -d
      while (k <= d && !stop) {
        var x = if (k == -d || (k != d && v(off + k - 1) < v(off + k + 1))) v(off + k + 1)
                else v(off + k - 1) + 1
        var y = x - k
        while (x < n && y < m && a(x) == b(y)) { x += 1; y += 1 }
        v(off + k) = x
        if (x >= n && y >= m) { found = d; stop = true }
        k += 2
      }
      d += 1
    }
    if (found < 0) return None
    val ops = mutable.ArrayBuffer.empty[Op]
    var x = n; var y = m; var dd = found
    while (dd >= 0) {
      val vprev = trace(dd)
      val k = x - y
      val prevK = if (k == -dd || (k != dd && vprev(off + k - 1) < vprev(off + k + 1))) k + 1 else k - 1
      val prevX = vprev(off + prevK)
      val prevY = prevX - prevK
      while (x > prevX && y > prevY) { ops += Eq(a(x - 1)); x -= 1; y -= 1 }
      if (dd > 0) {
        if (x == prevX) { ops += Ins(b(y - 1)); y -= 1 }
        else { ops += Del(a(x - 1)); x -= 1 }
      }
      dd -= 1
    }
    Some(ops.reverse.toArray)
  }

  private def formatHunks(ops: Array[Op]): String = {
    val len = ops.length
    val oldNo = Array.fill(len + 1)(1)
    val newNo = Array.fill(len + 1)(1)
    for (i <- 0 until len) {
      val (o, nw) = ops(i) match {
        case Eq(_) => (1, 1); case Del(_) => (1, 0); case Ins(_) => (0, 1)
      }
      oldNo(i + 1) = oldNo(i) + o
      newNo(i + 1) = newNo(i) + nw
    }
    val sb = new mutable.StringBuilder
    var i = 0
    while (i < len) {
      if (isChange(ops(i))) {
        var s = i; var back = Context
        while (s > 0 && !isChange(ops(s - 1)) && back > 0) { s -= 1; back -= 1 }
        var e = i; var lastChange = i; var cont = true
        while (cont) {
          if (e + 1 >= len) cont = false
          else if (isChange(ops(e + 1))) { e += 1; lastChange = e }
          else {
            var run = 0
            while (e + 1 + run < len && !isChange(ops(e + 1 + run))) run += 1
            val nextChange = e + 1 + run
            if (nextChange < len && run <= 2 * Context) { e = nextChange; lastChange = nextChange }
            else cont = false
          }
        }
        var stop = lastChange; var fwd = Context
        while (stop + 1 < len && !isChange(ops(stop + 1)) && fwd > 0) { stop += 1; fwd -= 1 }
        val ol = oldNo(stop + 1) - oldNo(s)
        val nl = newNo(stop + 1) - newNo(s)
        sb ++= s"@@ -${oldNo(s)},$ol +${newNo(s)},$nl @@\n"
        for (j <- s to stop) {
          sb ++= (ops(j) match { case Eq(l) => " " + l; case Del(l) => "-" + l; case Ins(l) => "+" + l })
          sb += '\n'
        }
        i = stop + 1
      } else i += 1
    }
    sb.toString
  }

  private def fallback(a: Array[String], b: Array[String]): String = {
    val n = a.length; val m = b.length
    var p = 0
    while (p < n && p < m && a(p) == b(p)) p += 1
    var s = 0
    while (s < n - p && s < m - p && a(n - 1 - s) == b(m - 1 - s)) s += 1
    val ol = n - p - s; val nl = m - p - s
    if (ol == 0 && nl == 0) return ""
    val sb = new mutable.StringBuilder
    sb ++= s"@@ -${p + 1},$ol +${p + 1},$nl @@\n"
    for (j <- p until n - s) { sb += '-'; sb ++= a(j); sb += '\n' }
    for (j <- p until m - s) { sb += '+'; sb ++= b(j); sb += '\n' }
    sb.toString
  }

  /** Unified diff turning `challenge` into `solution`; "" if identical. */
  def unified(challenge: String, solution: String): String = {
    if (challenge == solution) return ""
    val a = challenge.split("\n", -1)
    val b = solution.split("\n", -1)
    myers(a, b) match { case Some(ops) => formatHunks(ops); case None => fallback(a, b) }
  }

  /** Apply a `unified` diff to `challenge`, recovering the solution. */
  def apply(challenge: String, diff: String): String = {
    if (diff.isEmpty) return challenge
    val a = challenge.split("\n", -1)
    val out = mutable.ArrayBuffer.empty[String]
    var oi = 0
    for (line <- diff.split("\n", -1)) {
      if (line.startsWith("@@")) {
        val os = line.split("-", 2)(1).split(",", 2)(0).toInt
        while (oi < os - 1) { out += a(oi); oi += 1 }
      } else if (line.isEmpty) {
        // trailing split artifact
      } else line.charAt(0) match {
        case ' ' => out += line.substring(1); oi += 1
        case '-' => oi += 1
        case '+' => out += line.substring(1)
        case _   =>
      }
    }
    while (oi < a.length) { out += a(oi); oi += 1 }
    out.mkString("\n")
  }
}
