//! Syntactic ablation of Isabelle theories — a Rust/WASM port of the
//! Isabelle/Scala ablator (`../scala`). Parses `.thy` outer syntax with a
//! reimplementation of Isabelle's tokenizer + keyword classification, then
//! replaces selected proofs with `sorry`, preserving everything else.

pub mod ablate;
pub mod centrality;
pub mod keyword;
pub mod record;
pub mod sha1;
pub mod span;
pub mod token;
pub mod tokenize;

#[cfg(feature = "wasm")]
pub mod wasm;

/// Count theory-level goal statements in `text` (for the self-test's
/// "statements preserved" invariant).
pub fn count_theory_goals(syn: &span::Syntax, text: &str) -> usize {
    syn.parse_spans(text)
        .iter()
        .filter(|s| s.keyword_kind().map(keyword::is_theory_goal).unwrap_or(false))
        .count()
}

#[cfg(test)]
mod tests {
    use super::*;

    pub(crate) const SAMPLE: &str = "theory Nat\n  imports Main\nbegin\n\n\
        lemma add_zero: \"n + 0 = (n::nat)\"\n  by simp\n\n\
        lemma rev_rev: \"rev (rev xs) = xs\"\n  apply (induct xs)\n  apply simp\n  done\n\n\
        text \\<open>a \\<comment> \\<open>nested\\<close> cartouche\\<close>\n\
        lemma c: \"a \\<and> b \\<longrightarrow> b \\<and> a\"\nproof\n  show \"x\" by auto\nqed\n\nend\n";

    #[test]
    fn keyword_table_loads() {
        let kw = keyword::Keywords::hol();
        assert!(kw.kinds.len() > 300);
        assert_eq!(kw.kind("lemma"), Some("thy_goal_stmt"));
        assert_eq!(kw.kind("by"), Some("qed"));
    }

    #[test]
    fn round_trip_is_lossless() {
        let syn = span::Syntax::hol();
        let spans = syn.parse_spans(SAMPLE);
        let rebuilt: String = spans.iter().map(|s| s.source()).collect();
        assert_eq!(rebuilt, SAMPLE, "parse_spans must round-trip byte-exactly");
    }

    #[test]
    fn commands_classified() {
        let syn = span::Syntax::hol();
        let spans = syn.parse_spans(SAMPLE);
        let cmds: Vec<(&str, Option<&str>)> =
            spans.iter().filter(|s| !s.name().is_empty()).map(|s| (s.name(), s.keyword_kind())).collect();
        // theory ... begin, three lemmas, text, proof/show/by/qed, end
        assert!(cmds.contains(&("theory", Some("thy_begin"))));
        assert!(cmds.contains(&("lemma", Some("thy_goal_stmt"))));
        assert!(cmds.contains(&("by", Some("qed"))));
        assert!(cmds.contains(&("qed", Some("qed_block"))));
        assert!(cmds.contains(&("end", Some("thy_end"))));
        // `text \<open>...\<close>` is one command with a cartouche arg (not mis-split)
        assert!(cmds.iter().filter(|(n, _)| *n == "text").count() == 1);
    }

    #[test]
    fn ablate_all_and_identity() {
        let syn = span::Syntax::hol();
        let spans = syn.parse_spans(SAMPLE);
        let z = |_: &str| 0i64;

        // prob 0 is the identity
        let mut rng = ablate::Rng::new(0);
        let id_spec = ablate::Spec { prob: 0.0, ..Default::default() };
        let id = ablate::ablate(&spans, &id_spec, &mut rng, &z);
        assert_eq!(id.text, SAMPLE, "prob 0 must round-trip");

        // --all: every top-level proof -> sorry
        let all_spec = ablate::Spec { prob: 1.0, ..Default::default() };
        let all = ablate::ablate(&spans, &all_spec, &mut rng, &z);
        assert_eq!(all.ablated, all.total);
        assert!(all.text.contains("by simp sorry") || all.text.contains("sorry"));
        assert!(all.text.contains("lemma add_zero: \"n + 0 = (n::nat)\" sorry"));
        // statements preserved, proofs gone
        assert!(!all.text.contains("by simp\n"));
    }
}
