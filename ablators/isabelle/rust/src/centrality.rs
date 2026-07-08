//! Corpus-wide proof-dependency centrality (fan-in by textual name citation).
//! Port of `scala/src/Centrality.scala`.

use crate::keyword as kw;
use crate::span::Syntax;
use crate::token::Kind;
use std::collections::{HashMap, HashSet};

const MIN_NAME_LEN: usize = 3;

fn cited_names(content: &[crate::token::Token], into: &mut HashSet<String>) {
    for t in content {
        if t.is_ident() || t.kind == Kind::LongIdent {
            into.insert(t.source.clone());
            if let Some(dot) = t.source.rfind('.') {
                if dot + 1 < t.source.len() {
                    into.insert(t.source[dot + 1..].to_string());
                }
            }
        }
    }
}

/// name -> fan-in (distinct top-level theorems whose proof cites it).
pub fn fan_in<'a>(
    syntax: &Syntax,
    theories: impl IntoIterator<Item = &'a str>,
) -> HashMap<String, i64> {
    // per top-level goal: (name, set of cited identifiers)
    let mut goals: Vec<(String, HashSet<String>)> = Vec::new();

    for text in theories {
        let spans = syntax.parse_spans(text);
        let n = spans.len();
        let mut i = 0;
        while i < n {
            let is_thy_goal = spans[i]
                .keyword_kind()
                .map(kw::is_theory_goal)
                .unwrap_or(false);
            if is_thy_goal {
                let name = crate::ablate::goal_name(&spans[i]);
                i += 1;
                let mut depth = 1i32;
                let mut cited = HashSet::new();
                while i < n && depth > 0 {
                    cited_names(&spans[i].content, &mut cited);
                    match spans[i].keyword_kind() {
                        Some(k) if kw::is_theory(k) => depth = 0,
                        Some(k) => {
                            if kw::is_proof_goal(k) || k == kw::PRF_OPEN {
                                depth += 1;
                            } else if kw::is_proof_close(k) {
                                depth -= 1;
                            } else if kw::is_qed_global(k) {
                                depth = 0;
                            }
                        }
                        None => {}
                    }
                    i += 1;
                }
                goals.push((name, cited));
            } else {
                i += 1;
            }
        }
    }

    let declared: HashSet<&str> = goals
        .iter()
        .map(|(n, _)| n.as_str())
        .filter(|n| n.chars().count() >= MIN_NAME_LEN)
        .collect();

    let mut fan: HashMap<String, i64> = HashMap::new();
    for (name, cited) in &goals {
        for d in cited {
            if d != name && declared.contains(d.as_str()) {
                *fan.entry(d.clone()).or_insert(0) += 1;
            }
        }
    }
    fan
}
