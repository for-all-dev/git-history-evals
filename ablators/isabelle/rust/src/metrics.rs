//! Proof-complexity metrics (spec §2), computed inside the ablator so they surface
//! in the JSONL record (and any downstream website / difficulty classifier). These
//! are deliberately simple, tokenizer-driven heuristics — informative features, not
//! canonical semantics. See `docs/difficulty-features.md` §2 for the definitions,
//! which the OCaml/Lean ablators mirror for their own provers.
//!
//! To guarantee the Rust and Scala Isabelle ablators agree **token-for-token**, the
//! metrics use a small SELF-CONTAINED scanner defined identically here and in
//! `scala/src/Metrics.scala`, rather than either tool's full outer-syntax tokenizer
//! (whose keyword-table classification can differ between the baked-in HOL table and
//! a loaded session — e.g. `..` scanned as one token vs `.` `.`). The scanner skips
//! whitespace, nested `(* *)` comments, `"..."`/`` `...` `` strings, and
//! `\<open>..\<close>` cartouches, then emits the "code tokens" the keyword banks
//! match on: identifier runs (incl. `a.b.c` qualified names) and the standalone
//! symbols `.`, `..`, `|`.

#[derive(Clone, Debug)]
pub struct Metrics {
    pub n_lines: i64,
    pub n_chars: i64,
    pub n_subproofs: i64, // intermediate-assertion keywords: have/obtain/hence/thus
    pub n_tactics: i64,   // apply steps + terminal closers (by/../.) + structured steps
    pub cyclomatic: i64,  // 1 + #case-splitters + #alternation combinators
}

// Isabelle keyword banks (see spec §2).
const SUBPROOF_KW: [&str; 4] = ["have", "obtain", "hence", "thus"];
const CASE_SPLITTERS: [&str; 6] = ["cases", "induct", "induction", "split", "case", "next"];
const STRUCTURED_STEPS: [&str; 5] = ["have", "show", "hence", "thus", "obtain"];

fn n_lines(s: &str) -> i64 {
    if s.is_empty() {
        0
    } else {
        (s.matches('\n').count() + 1) as i64
    }
}

fn is_blank(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\r' | '\u{000b}' | '\u{000c}')
}
fn is_id_start(c: char) -> bool {
    c.is_ascii_alphabetic()
}
fn is_id_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '\''
}

// char length of a `\<...>` symbol starting at `i` (i points at the backslash), per
// Isabelle's symbol layer (`\<`, optional `^`, letters, `>`).
fn backslash_len(cs: &[char], i: usize) -> usize {
    let n = cs.len();
    let mut j = i + 2; // past `\<`
    if j < n && cs[j] == '^' {
        j += 1;
    }
    if j < n && cs[j].is_ascii_alphabetic() {
        j += 1;
        while j < n && (cs[j].is_ascii_alphanumeric() || cs[j] == '_' || cs[j] == '\'') {
            j += 1;
        }
    }
    if j < n && cs[j] == '>' {
        j += 1;
    }
    j - i
}

/// The self-contained scanner: the code tokens the banks match on, in order.
fn code_tokens(s: &str) -> Vec<String> {
    let cs: Vec<char> = s.chars().collect();
    let n = cs.len();
    let mut out: Vec<String> = Vec::new();
    let mut i = 0;
    while i < n {
        let c = cs[i];
        // whitespace
        if is_blank(c) {
            i += 1;
            continue;
        }
        // nested (* ... *) comment
        if c == '(' && i + 1 < n && cs[i + 1] == '*' {
            let mut depth = 1;
            i += 2;
            while i < n && depth > 0 {
                if cs[i] == '(' && i + 1 < n && cs[i + 1] == '*' {
                    depth += 1;
                    i += 2;
                } else if cs[i] == '*' && i + 1 < n && cs[i + 1] == ')' {
                    depth -= 1;
                    i += 2;
                } else {
                    i += 1;
                }
            }
            continue;
        }
        // "..." string (with \" / \\ escapes)
        if c == '"' {
            i += 1;
            while i < n && cs[i] != '"' {
                if cs[i] == '\\' && i + 1 < n {
                    i += 2;
                } else {
                    i += 1;
                }
            }
            if i < n {
                i += 1;
            }
            continue;
        }
        // `...` alt string
        if c == '`' {
            i += 1;
            while i < n && cs[i] != '`' {
                if cs[i] == '\\' && i + 1 < n {
                    i += 2;
                } else {
                    i += 1;
                }
            }
            if i < n {
                i += 1;
            }
            continue;
        }
        // unicode cartouche open ‹ ... ›
        if c == '\u{2039}' {
            let mut depth = 1;
            i += 1;
            while i < n && depth > 0 {
                if cs[i] == '\u{2039}' {
                    depth += 1;
                } else if cs[i] == '\u{203A}' {
                    depth -= 1;
                }
                i += 1;
            }
            continue;
        }
        // backslash symbol \<...> — either a cartouche opener or an opaque symbol
        if c == '\\' && i + 1 < n && cs[i + 1] == '<' {
            let len = backslash_len(cs.as_slice(), i);
            let sym: String = cs[i..i + len].iter().collect();
            if sym == "\\<open>" {
                let mut depth = 1;
                i += len;
                while i < n && depth > 0 {
                    if cs[i] == '\\' && i + 1 < n && cs[i + 1] == '<' {
                        let l2 = backslash_len(cs.as_slice(), i);
                        let s2: String = cs[i..i + l2].iter().collect();
                        if s2 == "\\<open>" {
                            depth += 1;
                        } else if s2 == "\\<close>" {
                            depth -= 1;
                        }
                        i += l2;
                    } else if cs[i] == '\u{2039}' {
                        depth += 1;
                        i += 1;
                    } else if cs[i] == '\u{203A}' {
                        depth -= 1;
                        i += 1;
                    } else {
                        i += 1;
                    }
                }
            } else {
                // opaque symbol (letter symbol \<alpha>, control \<^sub>, …): ignore
                i += len;
            }
            continue;
        }
        // identifier: id ('.' id)*  (keeps qualified names like List.rev as one token)
        if is_id_start(c) {
            let start = i;
            i += 1;
            while i < n && is_id_char(cs[i]) {
                i += 1;
            }
            while i + 1 < n && cs[i] == '.' && is_id_start(cs[i + 1]) {
                i += 1; // the dot
                i += 1; // first id char
                while i < n && is_id_char(cs[i]) {
                    i += 1;
                }
            }
            out.push(cs[start..i].iter().collect());
            continue;
        }
        // number: digits ('.' digits)? — consumed so a decimal point isn't miscounted
        if c.is_ascii_digit() {
            i += 1;
            while i < n && cs[i].is_ascii_digit() {
                i += 1;
            }
            if i + 1 < n && cs[i] == '.' && cs[i + 1].is_ascii_digit() {
                i += 1;
                while i < n && cs[i].is_ascii_digit() {
                    i += 1;
                }
            }
            continue;
        }
        // proof terminators `.` / `..`
        if c == '.' {
            if i + 1 < n && cs[i + 1] == '.' {
                out.push("..".to_string());
                i += 2;
            } else {
                out.push(".".to_string());
                i += 1;
            }
            continue;
        }
        // method-alternation separator
        if c == '|' {
            out.push("|".to_string());
            i += 1;
            continue;
        }
        // any other symbol: skip
        i += 1;
    }
    out
}

/// Compute metrics. `block` is the whole source slice (statement + proof) used for
/// `n_lines`/`n_chars`; `body` is the proof body used for the tactic/branch heuristics.
/// For holes the two are the same (only the proof body is available).
pub fn compute(block: &str, body: &str) -> Metrics {
    let mut n_subproofs = 0i64;
    let mut branches = 0i64;
    let mut n_apply = 0i64;
    let mut n_closer = 0i64;
    let mut n_struct = 0i64;
    for t in code_tokens(body) {
        let s = t.as_str();
        if SUBPROOF_KW.contains(&s) {
            n_subproofs += 1;
        }
        if CASE_SPLITTERS.contains(&s) || s == "|" || s == "moreover" {
            branches += 1;
        }
        if s == "apply" {
            n_apply += 1;
        }
        if s == "by" || s == ".." || s == "." {
            n_closer += 1;
        }
        if STRUCTURED_STEPS.contains(&s) {
            n_struct += 1;
        }
    }
    Metrics {
        n_lines: n_lines(block),
        n_chars: block.len() as i64,
        n_subproofs,
        n_tactics: n_apply + n_closer + n_struct,
        cyclomatic: 1 + branches,
    }
}

/// Metrics for a holed proof, whose only available text is the proof body.
pub fn hole(body: &str) -> Metrics {
    compute(body, body)
}
