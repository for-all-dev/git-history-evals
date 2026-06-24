//! Isabelle outer-syntax tokenizer — a port of `Symbol` + `Scan` + the
//! `Token.explode` flow (Pure/General/symbol.scala, General/scan.scala,
//! General/comment.scala, Isar/token.scala).
//!
//! We scan over a `Vec<char>` (one Rust `char` = one Unicode scalar; Isabelle's
//! UTF-16 surrogate pair is a single `char` here, and `\r\n` is one symbol).
//! Concatenating every token's source reproduces the input byte-for-byte.

use crate::token::{Kind, Token};
use std::collections::HashMap;
use std::collections::HashSet;
use std::sync::OnceLock;

/* ---------- Symbol layer ---------- */

/// Length (in `char`s) of the Isabelle symbol starting at `i` (match_length).
fn symbol_len(cs: &[char], i: usize) -> usize {
    let n = cs.len();
    if i >= n {
        return 0;
    }
    let a = cs[i];
    let b = if i + 1 < n { Some(cs[i + 1]) } else { None };
    if a == '\r' && b == Some('\n') {
        return 2;
    }
    if a == '\\' && b == Some('<') {
        // \<  ^?  [A-Za-z][A-Za-z0-9_']*  >
        let mut j = i + 2;
        if j < n && cs[j] == '^' {
            j += 1;
        }
        if j < n && cs[j].is_ascii_alphabetic() {
            j += 1;
            while j < n && is_ascii_letdig(cs[j]) {
                j += 1;
            }
        }
        if j < n && cs[j] == '>' {
            j += 1;
        }
        return j - i;
    }
    1
}

fn is_ascii_letdig(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '\''
}

/// The symbol string at `i` (its source slice).
fn symbol_at(cs: &[char], i: usize) -> String {
    let len = symbol_len(cs, i);
    cs[i..i + len].iter().collect()
}

// Identifier-letter alphabet: ASCII letters + the enumerated `\<...>` letter
// symbols (single/double latin, Greek). (Decoded Unicode glyphs omitted; HOL
// sources are stored encoded — flagged if the corpus check disagrees.)
fn letters() -> &'static HashSet<String> {
    static L: OnceLock<HashSet<String>> = OnceLock::new();
    L.get_or_init(|| {
        let mut s = HashSet::new();
        for c in 'A'..='Z' {
            s.insert(c.to_string());
        }
        for c in 'a'..='z' {
            s.insert(c.to_string());
        }
        for c in 'A'..='Z' {
            s.insert(format!("\\<{}>", c));
            s.insert(format!("\\<{}{}>", c, c));
        }
        for c in 'a'..='z' {
            s.insert(format!("\\<{}>", c));
            s.insert(format!("\\<{}{}>", c, c));
        }
        for g in [
            "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa",
            "mu", "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
            "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi",
            "Omega",
        ] {
            s.insert(format!("\\<{}>", g));
        }
        s
    })
}

const SYM_CHARS: &str = "!#$%&*+-/<=>?@^_|~";

fn is_letter(s: &str) -> bool {
    letters().contains(s)
}
fn is_digit(s: &str) -> bool {
    s.len() == 1 && s.as_bytes()[0].is_ascii_digit()
}
fn is_quasi(s: &str) -> bool {
    s == "_" || s == "'"
}
fn is_letdig(s: &str) -> bool {
    is_letter(s) || is_digit(s) || is_quasi(s)
}
fn is_blank(s: &str) -> bool {
    matches!(s, " " | "\t" | "\n" | "\u{000b}" | "\u{000c}" | "\r" | "\r\n")
}
fn is_sym_char(s: &str) -> bool {
    s.chars().count() == 1 && SYM_CHARS.contains(s)
}
fn is_open(s: &str) -> bool {
    s == "\\<open>" || s == "\u{2039}"
}
fn is_close(s: &str) -> bool {
    s == "\\<close>" || s == "\u{203A}"
}
fn is_control(s: &str) -> bool {
    s.starts_with("\\<^") && s.ends_with('>') && s.len() > 4
}
fn raw_symbolic(s: &str) -> bool {
    s.starts_with("\\<") && s.ends_with('>') && !s.starts_with("\\<^") && s.len() > 3
}
fn is_symbolic(s: &str) -> bool {
    !is_open(s) && !is_close(s) && raw_symbolic(s)
}
// formal-comment markers (encoded forms; \<comment> permits trailing blanks)
fn is_comment_marker(s: &str) -> bool {
    matches!(s, "\\<comment>" | "\\<^cancel>" | "\\<^latex>" | "\\<^marker>")
}
const SUB: &str = "\\<^sub>";

/* ---------- keyword lexicon (char trie, longest match) ---------- */

#[derive(Default)]
pub struct Lexicon {
    terminal: bool,
    children: HashMap<char, Lexicon>,
}

impl Lexicon {
    pub fn from_names<'a>(names: impl IntoIterator<Item = &'a str>) -> Lexicon {
        let mut root = Lexicon::default();
        for name in names {
            let mut node = &mut root;
            for c in name.chars() {
                node = node.children.entry(c).or_default();
            }
            node.terminal = true;
        }
        root
    }

    /// Longest keyword (in `char`s) that is a prefix of `cs[pos..]`; 0 if none.
    fn scan(&self, cs: &[char], pos: usize) -> usize {
        let mut node = self;
        let mut best = 0;
        let mut i = pos;
        while i < cs.len() {
            match node.children.get(&cs[i]) {
                Some(next) => {
                    i += 1;
                    if next.terminal {
                        best = i - pos;
                    }
                    node = next;
                }
                None => break,
            }
        }
        best
    }
}

/* ---------- scanners (return end char-index) ---------- */

// quoted string: quote, body, quote. The body is *symbol*-aware: a `\<name>` is
// one symbol (ordinary content, not an escape); only a lone `\` starts an escape
// (\quote, \\, \ddd<=255). Returns Some(end) if closed; recover stops at body end.
fn scan_quoted(cs: &[char], pos: usize, quote: char, require_close: bool) -> Option<usize> {
    let n = cs.len();
    if pos >= n || cs[pos] != quote {
        return None;
    }
    let mut j = pos + 1;
    loop {
        if j >= n {
            return if require_close { None } else { Some(j) };
        }
        if cs[j] == quote {
            return Some(if require_close { j + 1 } else { j });
        }
        let slen = symbol_len(cs, j);
        if slen == 1 && cs[j] == '\\' {
            // escape: \quote | \\ | \ddd (<=255)
            if j + 1 < n && (cs[j + 1] == quote || cs[j + 1] == '\\') {
                j += 2;
            } else if j + 3 < n
                && cs[j + 1].is_ascii_digit()
                && cs[j + 2].is_ascii_digit()
                && cs[j + 3].is_ascii_digit()
                && (cs[j + 1] as u32 - '0' as u32) * 100
                    + (cs[j + 2] as u32 - '0' as u32) * 10
                    + (cs[j + 3] as u32 - '0' as u32)
                    <= 255
            {
                j += 4;
            } else {
                // lone backslash terminates the body
                return if require_close { None } else { Some(j) };
            }
        } else {
            j += slen; // ordinary content symbol (incl `\<name>`)
        }
    }
}

// nested cartouche delimited by \<open>/\<close> (or decoded). Returns (end, balanced).
fn scan_cartouche(cs: &[char], pos: usize) -> Option<(usize, bool)> {
    let n = cs.len();
    let first = symbol_at(cs, pos);
    if !is_open(&first) {
        return None;
    }
    let mut j = pos;
    let mut depth = 0i32;
    while j < n {
        let len = symbol_len(cs, j);
        let sym = cs[j..j + len].iter().collect::<String>();
        if is_open(&sym) {
            depth += 1;
            j += len;
        } else if is_close(&sym) && depth > 0 {
            depth -= 1;
            j += len;
            if depth == 0 {
                return Some((j, true));
            }
        } else if depth > 0 {
            j += len;
        } else {
            break;
        }
    }
    Some((j, depth == 0))
}

// nested ASCII comment (* ... *). Returns (end, balanced).
fn scan_comment(cs: &[char], pos: usize) -> Option<(usize, bool)> {
    let n = cs.len();
    if !(pos + 1 < n && cs[pos] == '(' && cs[pos + 1] == '*') {
        return None;
    }
    let mut j = pos;
    let mut depth = 0i32;
    loop {
        if j + 1 < n && cs[j] == '(' && cs[j + 1] == '*' {
            depth += 1;
            j += 2;
        } else if depth > 0 && j + 1 < n && cs[j] == '*' && cs[j + 1] == ')' {
            depth -= 1;
            j += 2;
            if depth == 0 {
                return Some((j, true));
            }
        } else if j >= n {
            return Some((j, false));
        } else {
            j += 1;
        }
    }
}

// formal comment: marker (+ blanks for \<comment>) + balanced cartouche.
fn scan_comment_cartouche(cs: &[char], pos: usize) -> Option<usize> {
    let marker = symbol_at(cs, pos);
    if !is_comment_marker(&marker) {
        return None;
    }
    let mut j = pos + marker.chars().count();
    if marker == "\\<comment>" {
        // many(is_blank)
        while j < cs.len() {
            let s = symbol_at(cs, j);
            if is_blank(&s) {
                j += s.chars().count();
            } else {
                break;
            }
        }
    }
    match scan_cartouche(cs, j) {
        Some((end, true)) => Some(end),
        _ => None,
    }
}

// control cartouche: control symbol + balanced cartouche.
fn scan_control_cartouche(cs: &[char], pos: usize) -> Option<usize> {
    let ctrl = symbol_at(cs, pos);
    if !is_control(&ctrl) {
        return None;
    }
    let j = pos + ctrl.chars().count();
    match scan_cartouche(cs, j) {
        Some((end, true)) => Some(end),
        _ => None,
    }
}

fn scan_blanks(cs: &[char], pos: usize) -> usize {
    let mut j = pos;
    while j < cs.len() {
        let s = symbol_at(cs, j);
        if is_blank(&s) {
            j += s.chars().count();
        } else {
            break;
        }
    }
    j
}

// identifier: letter, then rep(letdig+ | \<^sub> letdig+)
fn scan_id(cs: &[char], pos: usize) -> Option<usize> {
    let n = cs.len();
    let first = symbol_at(cs, pos);
    if !is_letter(&first) {
        return None;
    }
    let mut j = pos + first.chars().count();
    loop {
        // a run of >=1 letdig
        let mut k = j;
        loop {
            if k >= n {
                break;
            }
            let s = symbol_at(cs, k);
            if is_letdig(&s) {
                k += s.chars().count();
            } else {
                break;
            }
        }
        if k > j {
            j = k;
            continue;
        }
        // or \<^sub> followed by >=1 letdig
        let sym = symbol_at(cs, j);
        if sym == SUB {
            let after = j + sym.chars().count();
            let mut k2 = after;
            loop {
                if k2 >= n {
                    break;
                }
                let s = symbol_at(cs, k2);
                if is_letdig(&s) {
                    k2 += s.chars().count();
                } else {
                    break;
                }
            }
            if k2 > after {
                j = k2;
                continue;
            }
        }
        break;
    }
    Some(j)
}

fn scan_nat(cs: &[char], pos: usize) -> Option<usize> {
    let mut j = pos;
    while j < cs.len() && cs[j].is_ascii_digit() {
        j += 1;
    }
    if j > pos {
        Some(j)
    } else {
        None
    }
}

// id ~ opt("." nat)
fn scan_id_nat(cs: &[char], pos: usize) -> Option<usize> {
    let e = scan_id(cs, pos)?;
    if e < cs.len() && cs[e] == '.' {
        if let Some(e2) = scan_nat(cs, e + 1) {
            return Some(e2);
        }
    }
    Some(e)
}

/// One identifier-family token (ordered choice, like token.scala other_token).
fn scan_ident_family(cs: &[char], pos: usize) -> Option<(Kind, usize)> {
    let n = cs.len();
    // ident: id ~ rep("." id)  -> IDENT or LONG_IDENT
    if let Some(mut e) = scan_id(cs, pos) {
        let mut long = false;
        while e < n && cs[e] == '.' {
            if let Some(e2) = scan_id(cs, e + 1) {
                e = e2;
                long = true;
            } else {
                break;
            }
        }
        return Some((if long { Kind::LongIdent } else { Kind::Ident }, e));
    }
    // var_: "?" id_nat ; type_var: "?'" id_nat
    if pos < n && cs[pos] == '?' {
        if pos + 1 < n && cs[pos + 1] == '\'' {
            if let Some(e) = scan_id_nat(cs, pos + 2) {
                return Some((Kind::TypeVar, e));
            }
        }
        if let Some(e) = scan_id_nat(cs, pos + 1) {
            return Some((Kind::Var, e));
        }
    }
    // type_ident: "'" id
    if pos < n && cs[pos] == '\'' {
        if let Some(e) = scan_id(cs, pos + 1) {
            return Some((Kind::TypeIdent, e));
        }
    }
    // float: ("-" natdot | natdot) ; natdot = nat "." nat
    if let Some(e) = scan_float(cs, pos) {
        return Some((Kind::Float, e));
    }
    // nat
    if let Some(e) = scan_nat(cs, pos) {
        return Some((Kind::Nat, e));
    }
    // sym_ident: many1(is_sym_char) | one(is_symbolic)
    if let Some(e) = scan_sym_ident(cs, pos) {
        return Some((Kind::SymIdent, e));
    }
    None
}

fn scan_float(cs: &[char], pos: usize) -> Option<usize> {
    let mut p = pos;
    if p < cs.len() && cs[p] == '-' {
        p += 1;
    }
    let a = scan_nat(cs, p)?;
    if a < cs.len() && cs[a] == '.' {
        if let Some(b) = scan_nat(cs, a + 1) {
            return Some(b);
        }
    }
    None
}

fn scan_sym_ident(cs: &[char], pos: usize) -> Option<usize> {
    // many1 of single symbolic chars
    let mut j = pos;
    while j < cs.len() {
        let s = symbol_at(cs, j);
        if is_sym_char(&s) {
            j += s.chars().count();
        } else {
            break;
        }
    }
    if j > pos {
        return Some(j);
    }
    // or one symbolic \<...> symbol
    let s = symbol_at(cs, pos);
    if is_symbolic(&s) {
        return Some(pos + s.chars().count());
    }
    None
}

/* ---------- explode ---------- */

/// Tokenize `text` using the major/minor keyword lexicons.
pub fn explode(major: &Lexicon, minor: &Lexicon, text: &str) -> Vec<Token> {
    let cs: Vec<char> = text.chars().collect();
    let n = cs.len();
    let mut out = Vec::new();
    let mut pos = 0;
    let slice = |a: usize, b: usize| -> String { cs[a..b].iter().collect() };

    while pos < n {
        // delimited_token (each only on matching opener, requiring completion)
        if cs[pos] == '"' {
            if let Some(e) = scan_quoted(&cs, pos, '"', true) {
                out.push(Token::new(Kind::String, slice(pos, e)));
                pos = e;
                continue;
            }
        }
        if cs[pos] == '`' {
            if let Some(e) = scan_quoted(&cs, pos, '`', true) {
                out.push(Token::new(Kind::AltString, slice(pos, e)));
                pos = e;
                continue;
            }
        }
        if let Some((e, true)) = scan_comment(&cs, pos) {
            out.push(Token::new(Kind::InformalComment, slice(pos, e)));
            pos = e;
            continue;
        }
        if let Some(e) = scan_comment_cartouche(&cs, pos) {
            out.push(Token::new(Kind::FormalComment, slice(pos, e)));
            pos = e;
            continue;
        }
        if let Some((e, true)) = scan_cartouche(&cs, pos) {
            out.push(Token::new(Kind::Cartouche, slice(pos, e)));
            pos = e;
            continue;
        }
        if let Some(e) = scan_control_cartouche(&cs, pos) {
            out.push(Token::new(Kind::Control, slice(pos, e)));
            pos = e;
            continue;
        }

        // other_token
        let sym = symbol_at(&cs, pos);
        if is_blank(&sym) {
            let e = scan_blanks(&cs, pos);
            out.push(Token::new(Kind::Space, slice(pos, e)));
            pos = e;
            continue;
        }
        // recover_delimited -> ERROR (unterminated string/cartouche/comment)
        if cs[pos] == '"' {
            let e = scan_quoted(&cs, pos, '"', false).unwrap_or(pos + 1);
            out.push(Token::new(Kind::Error, slice(pos, e.max(pos + 1))));
            pos = e.max(pos + 1);
            continue;
        }
        if cs[pos] == '`' {
            let e = scan_quoted(&cs, pos, '`', false).unwrap_or(pos + 1);
            out.push(Token::new(Kind::Error, slice(pos, e.max(pos + 1))));
            pos = e.max(pos + 1);
            continue;
        }
        if is_open(&sym) {
            if let Some((e, _)) = scan_cartouche(&cs, pos) {
                out.push(Token::new(Kind::Error, slice(pos, e)));
                pos = e;
                continue;
            }
        }
        if let Some((e, false)) = scan_comment(&cs, pos) {
            out.push(Token::new(Kind::Error, slice(pos, e)));
            pos = e;
            continue;
        }

        // keyword (major>=minor on tie)  |||  ident-family (longest wins, keyword on tie)
        let maj = major.scan(&cs, pos);
        let min = minor.scan(&cs, pos);
        let (kw_kind, kw_len) = if maj >= min && maj > 0 {
            (Kind::Command, maj)
        } else if min > 0 {
            (Kind::Keyword, min)
        } else {
            (Kind::Keyword, 0)
        };
        let idf = scan_ident_family(&cs, pos);
        let idf_len = idf.map(|(_, e)| e - pos).unwrap_or(0);

        if kw_len > 0 && kw_len >= idf_len {
            out.push(Token::new(kw_kind, slice(pos, pos + kw_len)));
            pos += kw_len;
            continue;
        }
        if let Some((k, e)) = idf {
            out.push(Token::new(k, slice(pos, e)));
            pos = e;
            continue;
        }

        // bad: one symbol -> ERROR
        let e = pos + sym.chars().count().max(1);
        out.push(Token::new(Kind::Error, slice(pos, e)));
        pos = e;
    }

    out
}
