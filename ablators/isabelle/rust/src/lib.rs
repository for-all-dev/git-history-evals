//! Syntactic ablation of Isabelle theories — a Rust/WASM port of the
//! Isabelle/Scala ablator (`../scala`). Parses `.thy` outer syntax with a
//! reimplementation of Isabelle's tokenizer + keyword classification, then
//! replaces selected proofs with `sorry`, preserving everything else.

pub mod ablate;
pub mod centrality;
pub mod diff;
pub mod keyword;
pub mod metrics;
pub mod record;
pub mod sha1;
pub mod span;
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
        .filter(|s| {
            s.keyword_kind()
                .map(keyword::is_theory_goal)
                .unwrap_or(false)
        })
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
        let cmds: Vec<(&str, Option<&str>)> = spans
            .iter()
            .filter(|s| !s.name().is_empty())
            .map(|s| (s.name(), s.keyword_kind()))
            .collect();
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
        let id_spec = ablate::Spec {
            prob: 0.0,
            ..Default::default()
        };
        let id = ablate::ablate(&spans, &id_spec, &mut rng, &z);
        assert_eq!(id.text, SAMPLE, "prob 0 must round-trip");
        assert_eq!(
            id.solution, SAMPLE,
            "solution defaults to the full original"
        );

        // --all: every top-level proof -> sorry
        let all_spec = ablate::Spec {
            prob: 1.0,
            ..Default::default()
        };
        let all = ablate::ablate(&spans, &all_spec, &mut rng, &z);
        assert_eq!(all.ablated, all.total);
        assert!(all.text.contains("by simp sorry") || all.text.contains("sorry"));
        assert!(all
            .text
            .contains("lemma add_zero: \"n + 0 = (n::nat)\" sorry"));
        // statements preserved, proofs gone
        assert!(!all.text.contains("by simp\n"));
    }

    #[test]
    fn diff_round_trips() {
        let cases = [
            ("a\nb\nc\n", "a\nB\nc\n"),
            ("l1\nl2\nl3\nl4\nl5\n", "l1\nX\nl3\nl4\nY\n"),
            ("a\nb", "a\nc"),                 // no trailing newline
            ("same\nsame\n", "same\nsame\n"), // identical -> empty diff
            ("", "x\ny\n"),
        ];
        for (a, b) in cases {
            let d = diff::unified(a, b);
            assert_eq!(diff::apply(a, &d), b, "round-trip for {a:?} -> {b:?}");
        }
        // large file + localized change -> tiny diff
        let big: String = (0..300)
            .map(|i| format!("definition d{i} = {i}\n"))
            .collect();
        let sol = format!("{big}lemma foo: \"True\" by simp\n");
        let chal = format!("{big}lemma foo: \"True\" sorry\n");
        let d = diff::unified(&chal, &sol);
        assert_eq!(diff::apply(&chal, &d), sol);
        assert!(
            d.len() < sol.len() / 4,
            "diff should be tiny for a localized change"
        );
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
        let spec = ablate::Spec {
            delete_lemmas: true,
            prob: 1.0,
            ..Default::default()
        };
        let mut rng = ablate::Rng::new(0);
        let r = ablate::ablate(&spans, &spec, &mut rng, &z);
        let deleted: Vec<&str> = r.deleted.iter().map(|d| d.name.as_str()).collect();
        assert!(
            deleted.contains(&"helper"),
            "helper (used in a proof) deleted"
        );
        assert!(!deleted.contains(&"unused"), "unused (no user) not deleted");
        assert!(
            !deleted.contains(&"key"),
            "key (used in a statement) not deleted"
        );
        assert!(!r.text.contains("lemma helper"), "helper's statement gone");
        assert!(r.text.contains("lemma main"), "user main's statement kept");
        assert!(!r.text.contains("using helper"), "user main's proof holed");
        assert_eq!(r.solution, src, "solution is the full original");
    }

    #[test]
    fn delete_count_truncate() {
        let syn = span::Syntax::hol();
        // aaa has 2 users (ua1, ua2); bbb has 3 (ub1..ub3); declare-before-use.
        let src = "theory T\nimports Main\nbegin\n\n\
            lemma aaa: \"(1::nat) = 1\" by simp\n\n\
            lemma bbb: \"(2::nat) = 2\" by simp\n\n\
            lemma ua1: \"(1::nat) = 1\" using aaa by simp\n\n\
            lemma ua2: \"(1::nat) = 1\" using aaa by simp\n\n\
            lemma ub1: \"(2::nat) = 2\" using bbb by simp\n\n\
            lemma ub2: \"(2::nat) = 2\" using bbb by simp\n\n\
            lemma ub3: \"(2::nat) = 2\" using bbb by simp\n\nend\n";
        let spans = syn.parse_spans(src);
        let z = |_: &str| 0i64;
        let run = |count: u64, truncate: bool, uniform: bool, seed: u64| {
            let spec = ablate::Spec {
                delete_lemmas: true,
                delete_uniform: uniform,
                count: Some(count),
                truncate,
                ..Default::default()
            };
            let mut rng = ablate::Rng::new(seed);
            ablate::ablate(&spans, &spec, &mut rng, &z)
        };
        // count is a target reached by a weighted draw; aaa(2) or bbb(3) alone
        // reaches >= 2, so a single deletion suffices and ablations are >= count
        let r2 = run(2, false, false, 1);
        assert!(
            r2.ablated >= 2 && r2.deleted.len() == 1,
            "count=2: >= 2 from one deletion"
        );
        let names = |r: &ablate::AblationResult| -> Vec<String> {
            r.deleted.iter().map(|d| d.name.clone()).collect()
        };
        assert_eq!(
            names(&r2),
            names(&run(2, false, false, 1)),
            "reproducible per seed"
        );
        // count = 4 needs both lemmas (2 + 3): always 5 ablations, both deleted
        let r4 = run(4, false, false, 0);
        assert_eq!(r4.ablated, 5, "count=4 -> needs both, 5 ablations");
        assert_eq!(r4.deleted.len(), 2, "both deleted");
        // --truncate caps ablations at exactly count and drops the rest
        assert_eq!(
            run(2, true, false, 3).ablated,
            2,
            "truncate+count=2 -> exactly 2"
        );
        assert_eq!(
            run(1, true, false, 7).ablated,
            1,
            "truncate+count=1 -> exactly 1"
        );
        // tail reachability + weighting across seeds: the sole deletion at count=2
        // is sometimes the tail (aaa), sometimes the popular one (bbb); weighting
        // favours bbb, uniform balances them.
        let first = |uniform: bool, seed: u64| -> String {
            run(2, false, uniform, seed)
                .deleted
                .first()
                .map(|d| d.name.clone())
                .unwrap_or_default()
        };
        let tally = |uniform: bool| -> (i32, i32) {
            let (mut a, mut b) = (0, 0);
            for s in 0..100u64 {
                match first(uniform, s).as_str() {
                    "aaa" => a += 1,
                    "bbb" => b += 1,
                    _ => {}
                }
            }
            (a, b)
        };
        let (wa, wb) = tally(false);
        let (ua, ub) = tally(true);
        assert!(
            wa > 0 && wb > 0,
            "weighted: both tail (aaa) and popular (bbb) reachable"
        );
        assert!(wb > wa, "weighting favours the more-used lemma (bbb)");
        assert!(ua > 0 && ub > 0, "uniform: both reachable");
    }

    #[test]
    fn delete_shrink_and_slice() {
        let syn = span::Syntax::hol();
        // base used by u1,u2,u3; un1/un2/un3 unrelated; declare-before-use.
        let src = "theory T\nimports Main\nbegin\n\n\
            lemma base: \"(1::nat) = 1\" by simp\n\n\
            lemma un1: \"(2::nat) = 2\" by simp\n\n\
            lemma u1: \"(1::nat) = 1\" using base by simp\n\n\
            lemma un2: \"(3::nat) = 3\" by simp\n\n\
            lemma u2: \"(1::nat) = 1\" using base by simp\n\n\
            lemma un3: \"(4::nat) = 4\" by simp\n\n\
            lemma u3: \"(1::nat) = 1\" using base by simp\n\nend\n";
        let spans = syn.parse_spans(src);
        let z = |_: &str| 0i64;
        let mk = |f: &dyn Fn(&mut ablate::Spec)| {
            let mut spec = ablate::Spec {
                delete_lemmas: true,
                count: Some(2),
                ..Default::default()
            };
            f(&mut spec);
            let mut rng = ablate::Rng::new(0);
            ablate::ablate(&spans, &spec, &mut rng, &z)
        };
        // prefix: keep through 2nd hole (u2), drop u3; base deleted; context + end kept
        let p = mk(&|s| s.shrink_challenge = true);
        assert!(
            p.text.contains("lemma u1") && p.text.contains("lemma u2"),
            "prefix keeps u1,u2"
        );
        assert!(!p.text.contains("lemma u3"), "prefix drops u3");
        assert!(!p.text.contains("lemma base"), "base deleted");
        assert!(
            p.text.contains("lemma un1") && p.text.contains("end"),
            "context + end kept"
        );
        // minimal slice: only the 2 holes + structural glue; unrelated/base/u3 dropped
        let m = mk(&|s| s.shrink_challenge_minimal = true);
        assert!(
            m.text.contains("lemma u1") && m.text.contains("lemma u2"),
            "slice keeps holes"
        );
        assert!(
            !m.text.contains("lemma un1") && !m.text.contains("lemma un2"),
            "slice drops unrelated"
        );
        assert!(
            !m.text.contains("lemma base") && !m.text.contains("lemma u3"),
            "slice drops base,u3"
        );
        assert!(
            m.text.contains("theory T") && m.text.contains("end"),
            "structural glue kept"
        );
        // solution slice: deleted base restored, with real proof
        let ms = mk(&|s| s.shrink_solution_minimal = true);
        assert!(
            ms.solution.contains("lemma base"),
            "solution slice restores base"
        );
        assert!(
            ms.solution.contains("using base"),
            "solution slice keeps real proofs"
        );
    }

    #[test]
    fn delete_slice_fairness() {
        // `helper` is cited only in u's PROOF (not its statement) and is [simp]-attributed
        // so it isn't itself deletable -> base is the sole deletion. The slice must KEEP
        // helper (the proof needs it), omit base, drop the unrelated lemma.
        let syn = span::Syntax::hol();
        let src = "theory T\nimports Main\nbegin\n\n\
            lemma helper [simp]: \"(1::nat) = 1\" by simp\n\n\
            lemma base: \"(1::nat) = 1\" by simp\n\n\
            lemma unrel: \"(9::nat) = 9\" by simp\n\n\
            lemma u: \"(1::nat) = 1\" using base helper by simp\n\nend\n";
        let spans = syn.parse_spans(src);
        let z = |_: &str| 0i64;
        let spec = ablate::Spec {
            delete_lemmas: true,
            count: Some(1),
            shrink_challenge_minimal: true,
            ..Default::default()
        };
        let mut rng = ablate::Rng::new(0);
        let r = ablate::ablate(&spans, &spec, &mut rng, &z);
        assert!(
            r.text.contains("lemma helper"),
            "helper (needed by the hole's proof) kept"
        );
        assert!(!r.text.contains("lemma base"), "deleted base omitted");
        assert!(!r.text.contains("lemma unrel"), "unrelated lemma dropped");
        assert!(
            r.text.contains("lemma u:") && r.text.contains("sorry"),
            "user holed"
        );
    }

    #[test]
    fn apply_script_cut() {
        // An apply-script with >=2 steps is ablated by keeping a prefix of `apply` steps
        // and admitting the rest with `sorry`; a single-step script -> whole-proof.
        let syn = span::Syntax::hol();
        let src = "theory T\nimports Main\nbegin\n\n\
            lemma multi: \"rev (rev xs) = xs\"\n  apply (induct xs)\n  apply simp\n  apply auto\n  done\n\n\
            lemma one: \"(1::nat) = 1\"\n  apply simp\n  done\n\nend\n";
        let spans = syn.parse_spans(src);
        let z = |_: &str| 0i64;
        let spec = ablate::Spec {
            prob: 1.0,
            ..Default::default()
        };
        let mut rng = ablate::Rng::new(0);
        let r = ablate::ablate(&spans, &spec, &mut rng, &z);
        assert!(
            r.text.contains("apply (induct xs)"),
            "leading apply step kept as a hint"
        );
        assert!(r.text.contains("sorry"), "the rest is admitted with sorry");
        assert!(
            !r.text.contains("apply auto"),
            "the final apply step is dropped"
        );
        assert!(!r.text.contains("done"), "the terminator is dropped");
        assert!(
            r.text.contains("lemma one: \"(1::nat) = 1\" sorry"),
            "a single-apply script falls back to whole-proof"
        );
        assert!(r.solution.contains("apply auto") && r.solution.contains("done"));
        // reproducible per seed, and challenge != solution (a real challenge)
        let mut rng2 = ablate::Rng::new(0);
        let r2 = ablate::ablate(&spans, &spec, &mut rng2, &z);
        assert_eq!(r.text, r2.text, "same seed -> same cut");
        assert_ne!(r.text, r.solution);

        // --ablate-scripts: drop the whole script instead of prefix-cutting
        let ws_spec = ablate::Spec {
            prob: 1.0,
            ablate_scripts: true,
            ..Default::default()
        };
        let mut rng3 = ablate::Rng::new(0);
        let ws = ablate::ablate(&spans, &ws_spec, &mut rng3, &z);
        assert!(
            ws.text.contains("lemma multi: \"rev (rev xs) = xs\" sorry"),
            "--ablate-scripts drops the whole script"
        );
        assert!(
            !ws.text.contains("apply (induct xs)"),
            "no prefix kept under --ablate-scripts"
        );
    }

    #[test]
    fn delete_leaves_apply_cut() {
        // A deleted lemma used mid apply-script -> cut at that step, keeping the prefix.
        let syn = span::Syntax::hol();
        let src = "theory T\nimports Main\nbegin\n\n\
            lemma base: \"(1::nat) = 1\" by simp\n\n\
            lemma u: \"(1::nat) = 1\"\n  apply (rule refl)\n  apply (simp add: base)\n  done\n\nend\n";
        let spans = syn.parse_spans(src);
        let z = |_: &str| 0i64;
        let spec = ablate::Spec {
            delete_lemmas: true,
            delete_leaves: true,
            count: Some(1),
            ..Default::default()
        };
        let mut rng = ablate::Rng::new(0);
        let r = ablate::ablate(&spans, &spec, &mut rng, &z);
        assert!(!r.text.contains("lemma base"), "deleted lemma omitted");
        assert!(
            r.text.contains("apply (rule refl)"),
            "prefix before the citing step kept"
        );
        assert!(
            !r.text.contains("simp add: base"),
            "citing step cut (no dangling ref)"
        );
        assert!(r.text.contains("sorry"), "the cut is admitted with sorry");
    }

    #[test]
    fn delete_lemmas_count_arg() {
        // --delete-lemmas N deletes exactly N lemmas regardless of ablation count
        let syn = span::Syntax::hol();
        let src = "theory T\nimports Main\nbegin\n\n\
            lemma aaa: \"(1::nat) = 1\" by simp\n\n\
            lemma bbb: \"(1::nat) = 1\" by simp\n\n\
            lemma ccc: \"(1::nat) = 1\" by simp\n\n\
            lemma ua: \"(1::nat) = 1\" using aaa by simp\n\n\
            lemma ub: \"(1::nat) = 1\" using bbb by simp\n\n\
            lemma uc: \"(1::nat) = 1\" using ccc by simp\n\nend\n";
        let spans = syn.parse_spans(src);
        let z = |_: &str| 0i64;
        let del = |n: u64| {
            let spec = ablate::Spec {
                delete_lemmas: true,
                delete_count: Some(n),
                ..Default::default()
            };
            let mut rng = ablate::Rng::new(0);
            ablate::ablate(&spans, &spec, &mut rng, &z).deleted.len()
        };
        assert_eq!(del(2), 2, "delete exactly 2");
        assert_eq!(del(1), 1, "delete exactly 1");
        assert_eq!(del(9), 3, "capped at the 3 candidates");
    }

    // A single dependency chain top -> mid -> base, plus an isolated `lonely`. The only
    // theorems with a non-empty *eligible* dependency closure are `top` ({mid,base}) and
    // `mid` ({base}); `base`/`lonely` have empty closures. So whichever corollary the
    // uniform draw lands on, every deletion must come from {mid, base} — never `top`
    // (nothing uses it -> not eligible) and never `lonely`.
    const COROLLARY_SRC: &str = "theory T\nimports Main\nbegin\n\n\
        lemma base: \"(1::nat) = 1\" by simp\n\n\
        lemma mid: \"(1::nat) = 1\" using base by simp\n\n\
        lemma top: \"(1::nat) = 1\" using mid by simp\n\n\
        lemma lonely: \"(5::nat) = 5\" by simp\n\nend\n";

    #[test]
    fn corollary_delete_restricts_to_closure() {
        let syn = span::Syntax::hol();
        let spans = syn.parse_spans(COROLLARY_SRC);
        let z = |_: &str| 0i64;
        let del1 = |seed: u64| -> Vec<String> {
            let spec = ablate::Spec {
                delete_lemmas: true,
                corollary: true,
                delete_count: Some(1),
                ..Default::default()
            };
            let mut rng = ablate::Rng::new(seed);
            let r = ablate::ablate(&spans, &spec, &mut rng, &z);
            r.deleted.into_iter().map(|d| d.name).collect()
        };
        let mut saw_base = false;
        let mut saw_mid = false;
        for s in 0..40u64 {
            let d = del1(s);
            assert_eq!(d.len(), 1, "=1 deletes exactly one");
            for n in &d {
                assert!(
                    n == "base" || n == "mid",
                    "deletion {n} outside any corollary closure"
                );
            }
            saw_base |= d.contains(&"base".to_string());
            saw_mid |= d.contains(&"mid".to_string());
        }
        assert!(
            saw_base && saw_mid,
            "both closure members reachable across seeds"
        );
        // reproducible per seed
        assert_eq!(del1(7), del1(7), "same seed -> same deletion");
        // never the user-less anchor or the isolated lemma
        for s in 0..40u64 {
            let d = del1(s);
            assert!(!d.contains(&"top".to_string()) && !d.contains(&"lonely".to_string()));
        }
    }

    #[test]
    fn corollary_delete_modes_and_shrink() {
        let syn = span::Syntax::hol();
        let spans = syn.parse_spans(COROLLARY_SRC);
        let z = |_: &str| 0i64;
        let run = |f: &dyn Fn(&mut ablate::Spec)| {
            let mut spec = ablate::Spec {
                delete_lemmas: true,
                corollary: true,
                delete_count: Some(1),
                ..Default::default()
            };
            f(&mut spec);
            let mut rng = ablate::Rng::new(2);
            ablate::ablate(&spans, &spec, &mut rng, &z)
        };
        // wholesale: a deleted lemma's user proof becomes `sorry`; full solution preserved
        let w = run(&|_| {});
        assert!(w.deleted.len() == 1, "one deletion");
        assert!(w.text.contains("sorry"), "user proof holed");
        assert_ne!(
            w.text, w.solution,
            "a real challenge (challenge != solution)"
        );
        assert_eq!(
            w.solution, COROLLARY_SRC,
            "wholesale solution is the full original"
        );
        // leaves variant runs and still holes
        let lv = run(&|s| s.delete_leaves = true);
        assert!(
            lv.text.contains("sorry"),
            "leaves mode holes the citing step"
        );
        // uniform variant runs and deletes from the closure
        let u = run(&|s| s.delete_uniform = true);
        assert!(u.deleted.len() == 1);
        // shrink still works under corollary mode (solution shrink implies challenge shrink)
        let sh = run(&|s| {
            s.shrink_solution = true;
            s.shrink_challenge = true;
        });
        assert!(
            sh.text.contains("end"),
            "shrunk challenge keeps structural end"
        );
        assert!(!sh.text.is_empty() && sh.text != sh.solution);
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
        let base = ablate::Spec {
            count: Some(1),
            by_centrality: true,
            ..Default::default()
        };

        let mut rng = ablate::Rng::new(0);
        let r0 = ablate::ablate(&spans, &base, &mut rng, &cent);
        assert!(
            r0.text.contains("lemma g2"),
            "g1 ablated, g2 statement kept in challenge"
        );
        assert_eq!(r0.solution, src, "no shrink: full solution");

        // shrink the SOLUTION: the later top-level goal g2 is dropped, g1 kept.
        let ss = ablate::Spec {
            shrink_solution: true,
            ..base.clone()
        };
        let mut rng = ablate::Rng::new(0);
        let r1 = ablate::ablate(&spans, &ss, &mut rng, &cent);
        assert!(
            !r1.solution.contains("lemma g2"),
            "shrink_solution drops the later goal"
        );
        assert!(
            !r1.solution.contains("definition d"),
            "shrink_solution drops the later definition"
        );
        assert!(
            r1.solution.contains("lemma g1"),
            "shrink_solution keeps the ablated goal"
        );
        assert!(
            r1.solution.contains("\nend"),
            "shrink_solution keeps the closing `end`"
        );
        assert!(
            r1.text.contains("lemma g2"),
            "challenge untouched by shrink_solution"
        );

        // shrink the CHALLENGE: g2 dropped from the challenge instead.
        let sc = ablate::Spec {
            shrink_challenge: true,
            ..base.clone()
        };
        let mut rng = ablate::Rng::new(0);
        let r2 = ablate::ablate(&spans, &sc, &mut rng, &cent);
        assert!(
            !r2.text.contains("lemma g2"),
            "shrink_challenge drops the later goal from the challenge"
        );
        assert_eq!(r2.solution, src, "solution untouched by shrink_challenge");
    }
}
