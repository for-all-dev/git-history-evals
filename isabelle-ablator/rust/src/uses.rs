//! Single-file usage analysis for `--delete-lemmas`. Mirrors the rocq ablator's
//! `uses.ml`. For each top-level theory goal we record its block span range,
//! whether it closes normally (a real fact, not `oops`), whether its header
//! carries an automation attribute (`lemma foo [simp]:`), the names cited in its
//! proof, and whether the name is ever used *outside* a proof body (which would
//! make deletion unsafe).
//!
//! Correct-by-construction: deletable only when every occurrence of the name
//! outside its own block sits inside an ablatable proof body AND its header has
//! no attribute — so deleting it + whole-proof-ablating its users leaves a
//! well-formed theory. (Standalone `declare foo [simp]` / `lemmas` commands name
//! the lemma → counted as non-proof uses.)

use crate::keyword as kw;
use crate::span::Span;
use crate::token::Kind;

pub struct Lemma {
    pub name: String,
    pub opener: usize,    // span index of the goal statement
    pub block_end: usize, // span index just past the terminator
    pub users: Vec<usize>,
    pub eligible: bool,
}

const MIN_NAME_LEN: usize = 3;

fn names_in(content: &[crate::token::Token]) -> Vec<String> {
    let mut out = Vec::new();
    for t in content {
        if t.is_ident() || t.kind == Kind::LongIdent {
            out.push(t.source.clone());
            if let Some(dot) = t.source.rfind('.') {
                if dot + 1 < t.source.len() {
                    out.push(t.source[dot + 1..].to_string());
                }
            }
        }
    }
    out
}

// `lemma foo [simp]: …` — a bare `[` before the first bare `:` is an attribute.
fn has_attribute(span: &Span) -> bool {
    for t in &span.content {
        if t.source == ":" {
            return false;
        }
        if t.source == "[" {
            return true;
        }
    }
    false
}

struct Goal {
    opener: usize,
    end: usize,
    name: String,
    normal: bool, // closed by a real qed (not oops)
    attr: bool,
    cited: std::collections::HashSet<String>,
}

/// `aggressive` relaxes the normal-close requirement (BE only; caller validates
/// with `--check-build`). The non-proof-use guard is never relaxed.
pub fn analyze(spans: &[Span], aggressive: bool) -> Vec<Lemma> {
    let n = spans.len();
    let mut goals: Vec<Goal> = Vec::new();
    let mut nonproof: Vec<(String, usize)> = Vec::new(); // (name, span index)
    let mut i = 0;
    while i < n {
        let is_goal = spans[i].keyword_kind().map(kw::is_theory_goal).unwrap_or(false);
        if is_goal {
            let opener = i;
            let name = crate::ablate::goal_name(&spans[i]);
            let attr = has_attribute(&spans[i]);
            for nm in names_in(&spans[i].content) {
                nonproof.push((nm, opener)); // the statement is non-proof context
            }
            i += 1;
            let mut depth = 1i32;
            let mut normal = false;
            let mut cited = std::collections::HashSet::new();
            while i < n && depth > 0 {
                let s = &spans[i];
                for nm in names_in(&s.content) {
                    cited.insert(nm);
                }
                match s.keyword_kind() {
                    Some(k) if kw::is_theory(k) => depth = 0, // theory boundary (malformed)
                    Some(k) => {
                        if kw::is_proof_goal(k) || k == kw::PRF_OPEN {
                            depth += 1;
                        } else if kw::is_proof_close(k) {
                            depth -= 1;
                            if depth == 0 {
                                normal = true;
                            }
                        } else if kw::is_qed_global(k) {
                            depth = 0; // oops: no fact added
                        }
                    }
                    None => {}
                }
                i += 1;
            }
            goals.push(Goal { opener, end: i, name, normal, attr, cited });
        } else {
            for nm in names_in(&spans[i].content) {
                nonproof.push((nm, i));
            }
            i += 1;
        }
    }

    goals
        .iter()
        .map(|g| {
            let users: Vec<usize> = goals
                .iter()
                .filter(|g2| g2.opener != g.opener && g2.cited.contains(&g.name))
                .map(|g2| g2.opener)
                .collect();
            let only_in_proofs = nonproof
                .iter()
                .all(|(nm, j)| *nm != g.name || (*j >= g.opener && *j < g.end));
            let eligible = (g.normal || aggressive)
                && !g.attr
                && g.name.chars().count() >= MIN_NAME_LEN
                && !users.is_empty()
                && only_in_proofs;
            Lemma { name: g.name.clone(), opener: g.opener, block_end: g.end, users, eligible }
        })
        .collect()
}
