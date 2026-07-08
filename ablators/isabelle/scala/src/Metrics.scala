/*  Proof-complexity metrics (spec §2), computed inside the ablator so they surface
    in the JSONL record (and any downstream website / difficulty classifier). These
    are deliberately simple, tokenizer-driven heuristics — informative features, not
    canonical semantics. See docs/difficulty-features.md §2 for the definitions, which
    the OCaml/Lean ablators mirror for their own provers.

    To guarantee the Rust and Scala Isabelle ablators agree TOKEN-FOR-TOKEN, the
    metrics use a small SELF-CONTAINED scanner defined identically here and in
    `rust/src/metrics.rs`, rather than either tool's full outer-syntax tokenizer
    (whose keyword-table classification can differ between the baked-in HOL table and
    a loaded session — e.g. `..` scanned as one token vs `.` `.`). The scanner skips
    whitespace, nested comments, strings, and cartouches, then emits the "code tokens"
    the keyword banks match on: identifier runs (incl. qualified names) and the
    standalone symbols `.`, `..`, `|`.  */

package proofablate

import scala.collection.mutable

object Metrics {
  // Every proof carries these five integers (spec §2).
  sealed case class T(n_lines: Int, n_chars: Int, n_subproofs: Int, n_tactics: Int,
    cyclomatic: Int)

  // Isabelle keyword banks (see spec §2).
  private val subproofKw = Set("have", "obtain", "hence", "thus")
  private val caseSplitters = Set("cases", "induct", "induction", "split", "case", "next")
  private val structuredSteps = Set("have", "show", "hence", "thus", "obtain")

  // cartouche delimiters (unicode forms); the encoded \<open>/\<close> forms are
  // handled in the backslash-symbol branch below.
  private val CART_OPEN: Char = 0x2039.toChar  // '‹'
  private val CART_CLOSE: Char = 0x203a.toChar // '›'
  private val VT: Char = 0x0b.toChar           // vertical tab
  private val FF: Char = 0x0c.toChar           // form feed

  private def nLines(s: String): Int = if (s.isEmpty) 0 else s.count(_ == '\n') + 1

  private def isBlank(c: Char): Boolean =
    c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == VT || c == FF
  private def isIdStart(c: Char): Boolean =
    (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
  private def isIdChar(c: Char): Boolean =
    isIdStart(c) || (c >= '0' && c <= '9') || c == '_' || c == '\''
  private def isDigit(c: Char): Boolean = c >= '0' && c <= '9'

  // char length of a `\<...>` symbol starting at `i` (i points at the backslash), per
  // Isabelle's symbol layer (backslash-less-than, optional `^`, letters, `>`).
  private def backslashLen(cs: Array[Char], i: Int): Int = {
    val n = cs.length
    var j = i + 2 // past the `\<`
    if (j < n && cs(j) == '^') j += 1
    if (j < n && isIdStart(cs(j))) {
      j += 1
      while (j < n && isIdChar(cs(j))) j += 1
    }
    if (j < n && cs(j) == '>') j += 1
    j - i
  }

  // The self-contained scanner: the code tokens the banks match on, in order.
  private def codeTokens(s: String): List[String] = {
    val cs = s.toCharArray
    val n = cs.length
    val out = new mutable.ListBuffer[String]
    var i = 0
    while (i < n) {
      val c = cs(i)
      if (isBlank(c)) i += 1
      else if (c == '(' && i + 1 < n && cs(i + 1) == '*') {
        // nested comment
        var depth = 1; i += 2
        while (i < n && depth > 0) {
          if (cs(i) == '(' && i + 1 < n && cs(i + 1) == '*') { depth += 1; i += 2 }
          else if (cs(i) == '*' && i + 1 < n && cs(i + 1) == ')') { depth -= 1; i += 2 }
          else i += 1
        }
      }
      else if (c == '"') {
        // double-quoted string (with backslash escapes)
        i += 1
        while (i < n && cs(i) != '"') { if (cs(i) == '\\' && i + 1 < n) i += 2 else i += 1 }
        if (i < n) i += 1
      }
      else if (c == '`') {
        // backquoted alt string
        i += 1
        while (i < n && cs(i) != '`') { if (cs(i) == '\\' && i + 1 < n) i += 2 else i += 1 }
        if (i < n) i += 1
      }
      else if (c == CART_OPEN) {
        // unicode cartouche
        var depth = 1; i += 1
        while (i < n && depth > 0) {
          if (cs(i) == CART_OPEN) depth += 1 else if (cs(i) == CART_CLOSE) depth -= 1
          i += 1
        }
      }
      else if (c == '\\' && i + 1 < n && cs(i + 1) == '<') {
        // backslash symbol — either a cartouche opener or an opaque symbol
        val len = backslashLen(cs, i)
        val sym = new String(cs, i, len)
        if (sym == "\\<open>") {
          var depth = 1; i += len
          while (i < n && depth > 0) {
            if (cs(i) == '\\' && i + 1 < n && cs(i + 1) == '<') {
              val l2 = backslashLen(cs, i)
              val s2 = new String(cs, i, l2)
              if (s2 == "\\<open>") depth += 1 else if (s2 == "\\<close>") depth -= 1
              i += l2
            } else if (cs(i) == CART_OPEN) { depth += 1; i += 1 }
            else if (cs(i) == CART_CLOSE) { depth -= 1; i += 1 }
            else i += 1
          }
        } else i += len // opaque symbol (letter symbol, control symbol, …): ignore
      }
      else if (isIdStart(c)) {
        // identifier: id ('.' id)*  (keeps qualified names as one token)
        val start = i; i += 1
        while (i < n && isIdChar(cs(i))) i += 1
        while (i + 1 < n && cs(i) == '.' && isIdStart(cs(i + 1))) {
          i += 1 // the dot
          i += 1 // first id char
          while (i < n && isIdChar(cs(i))) i += 1
        }
        out += new String(cs, start, i - start)
      }
      else if (isDigit(c)) {
        // number: digits ('.' digits)? — consumed so a decimal point isn't miscounted
        i += 1
        while (i < n && isDigit(cs(i))) i += 1
        if (i + 1 < n && cs(i) == '.' && isDigit(cs(i + 1))) {
          i += 1
          while (i < n && isDigit(cs(i))) i += 1
        }
      }
      else if (c == '.') {
        // proof terminators `.` / `..`
        if (i + 1 < n && cs(i + 1) == '.') { out += ".."; i += 2 }
        else { out += "."; i += 1 }
      }
      else if (c == '|') { out += "|"; i += 1 } // method-alternation separator
      else i += 1 // any other symbol: skip
    }
    out.toList
  }

  /** Compute metrics. `block` is the whole source slice (statement + proof) used for
   *  `n_lines`/`n_chars`; `body` is the proof body used for the tactic/branch
   *  heuristics. For holes the two are the same (only the proof body is available). */
  def compute(block: String, body: String): T = {
    var nSub = 0; var branches = 0; var nApply = 0; var nCloser = 0; var nStruct = 0
    for (t <- codeTokens(body)) {
      if (subproofKw.contains(t)) nSub += 1
      if (caseSplitters.contains(t) || t == "|" || t == "moreover") branches += 1
      if (t == "apply") nApply += 1
      if (t == "by" || t == ".." || t == ".") nCloser += 1
      if (structuredSteps.contains(t)) nStruct += 1
    }
    T(nLines(block), block.getBytes("UTF-8").length, nSub, nApply + nCloser + nStruct,
      1 + branches)
  }

  /** Metrics for a holed proof, whose only available text is the proof body. */
  def hole(body: String): T = compute(body, body)
}
