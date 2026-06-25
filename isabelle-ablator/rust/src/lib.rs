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
pub mod diff;
pub mod token;
pub mod tokenize;
pub mod uses;

#[cfg(feature = "wasm")]
pub mod wasm;

// Native compile-test (shells out to `isabelle build`); never in the WASM core.
#[cfg(feature = "cli")]
pub mod build_check;

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
        assert_eq!(id.solution, SAMPLE, "solution defaults to the full original");

        // --all: every top-level proof -> sorry
        let all_spec = ablate::Spec { prob: 1.0, ..Default::default() };
        let all = ablate::ablate(&spans, &all_spec, &mut rng, &z);
        assert_eq!(all.ablated, all.total);
        assert!(all.text.contains("by simp sorry") || all.text.contains("sorry"));
        assert!(all.text.contains("lemma add_zero: \"n + 0 = (n::nat)\" sorry"));
        // statements preserved, proofs gone
        assert!(!all.text.contains("by simp\n"));
    }

    #[test]
    fn diff_round_trips() {
        let cases = [
            ("a\nb\nc\n", "a\nB\nc\n"),
            ("l1\nl2\nl3\nl4\nl5\n", "l1\nX\nl3\nl4\nY\n"),
            ("a\nb", "a\nc"),         // no trailing newline
            ("same\nsame\n", "same\nsame\n"), // identical -> empty diff
            ("", "x\ny\n"),
        ];
        for (a, b) in cases {
            let d = diff::unified(a, b);
            assert_eq!(diff::apply(a, &d), b, "round-trip for {a:?} -> {b:?}");
        }
        // large file + localized change -> tiny diff
        let big: String = (0..300).map(|i| format!("definition d{i} = {i}\n")).collect();
        let sol = format!("{big}lemma foo: \"True\" by simp\n");
        let chal = format!("{big}lemma foo: \"True\" sorry\n");
        let d = diff::unified(&chal, &sol);
        assert_eq!(diff::apply(&chal, &d), sol);
        assert!(d.len() < sol.len() / 4, "diff should be tiny for a localized change");
    }

    #[test]
    fn delete_lemmas() {
        let syn = span::Syntax::hol();
        // `helper` is used by `main`; `unused` has no user; `key` is used in a
        // later *statement* (non-proof use) so it must not be deletable.
        let src = "theory T\nimports Main\nbegin\n\n\
            lemma helper: \"(1::nat) = 1\" by simp\n\n\
            lemma unused: \"(2::nat) = 2\" by simp\n\n\
            lemma main: \"(1::nat) = 1\" using helper by simp\n\n\
            lemma key: \"(3::nat) = 3\" by simp\n\n\
            lemma uses_key_stmt: \"key = key\" by simp\n\nend\n";
        let spans = syn.parse_spans(src);
        let z = |_: &str| 0i64;
        let spec = ablate::Spec { delete_lemmas: true, prob: 1.0, ..Default::default() };
        let mut rng = ablate::Rng::new(0);
        let r = ablate::ablate(&spans, &spec, &mut rng, &z);
        let deleted: Vec<&str> = r.deleted.iter().map(|(n, _)| n.as_str()).collect();
        assert!(deleted.contains(&"helper"), "helper (used in a proof) deleted");
        assert!(!deleted.contains(&"unused"), "unused (no user) not deleted");
        assert!(!deleted.contains(&"key"), "key (used in a statement) not deleted");
        assert!(!r.text.contains("lemma helper"), "helper's statement gone");
        assert!(r.text.contains("lemma main"), "user main's statement kept");
        assert!(!r.text.contains("using helper"), "user main's proof holed");
        assert_eq!(r.solution, src, "solution is the full original");
    }

    #[test]
    fn shrink_challenge_and_solution() {
        let syn = span::Syntax::hol();
        let src = "theory T\nimports Main\nbegin\n\n\
            lemma g1: \"a = (a::nat)\" by simp\n\n\
            definition d :: nat where \"d = 0\"\n\n\
            lemma g2: \"b = (b::nat)\" by simp\n\nend\n";
        let spans = syn.parse_spans(src);
        // pick exactly g1 (most-cited) so later decls (d, g2) are all dropped.
        let cent = |name: &str| if name == "g1" { 1 } else { 0 };
        let base = ablate::Spec { count: Some(1), by_centrality: true, ..Default::default() };

        let mut rng = ablate::Rng::new(0);
        let r0 = ablate::ablate(&spans, &base, &mut rng, &cent);
        assert!(r0.text.contains("lemma g2"), "g1 ablated, g2 statement kept in challenge");
        assert_eq!(r0.solution, src, "no shrink: full solution");

        // shrink the SOLUTION: the later top-level goal g2 is dropped, g1 kept.
        let ss = ablate::Spec { shrink_solution: true, ..base.clone() };
        let mut rng = ablate::Rng::new(0);
        let r1 = ablate::ablate(&spans, &ss, &mut rng, &cent);
        assert!(!r1.solution.contains("lemma g2"), "shrink_solution drops the later goal");
        assert!(!r1.solution.contains("definition d"), "shrink_solution drops the later definition");
        assert!(r1.solution.contains("lemma g1"), "shrink_solution keeps the ablated goal");
        assert!(r1.solution.contains("\nend"), "shrink_solution keeps the closing `end`");
        assert!(r1.text.contains("lemma g2"), "challenge untouched by shrink_solution");

        // shrink the CHALLENGE: g2 dropped from the challenge instead.
        let sc = ablate::Spec { shrink_challenge: true, ..base.clone() };
        let mut rng = ablate::Rng::new(0);
        let r2 = ablate::ablate(&spans, &sc, &mut rng, &cent);
        assert!(!r2.text.contains("lemma g2"), "shrink_challenge drops the later goal from the challenge");
        assert_eq!(r2.solution, src, "solution untouched by shrink_challenge");
    }
}
