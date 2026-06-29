//! `ablate` CLI — Rust port of the Isabelle/Scala ablator's command line.
//! (Build validation `--check-build` stays in the Scala tool; this tool is the
//! portable/WASM ablation engine.)

use clap::Parser;
use isabelle_ablator::ablate::{ablate, preset_of, Rng, Spec, INF, LADDER};
use isabelle_ablator::centrality;
use isabelle_ablator::count_theory_goals;
use isabelle_ablator::record::record;
use isabelle_ablator::span::Syntax;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::exit;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

/// Ablate proofs in Isabelle theories, replacing them with `sorry`.
///
/// PATH... is any mix of .thy files and directories (walked for *.thy).
/// Default: emit one indented JSON (challenge, solution) record per theory.
#[derive(Parser, Debug)]
#[command(name = "ablate", version, about, long_about = None)]
struct Cli {
    /// .thy files and/or directories to ablate
    #[arg(value_name = "PATH", required = true)]
    paths: Vec<String>,

    /// run the corpus self-test (round-trip + delimitation) instead of emitting
    #[arg(long)]
    check: bool,

    /// compile-test each ablation with `isabelle build` (challenge + solution);
    /// builds a throwaway session of just the theory, so only its upward closure
    /// is built and --shrink can't break it
    #[arg(long)]
    check_build: bool,

    /// difficulty preset ladder L0 (easy) .. L4 (code+spec only)
    #[arg(long, value_name = "L")]
    difficulty: Option<String>,

    /// ablate goals at nesting depth >= N (default 1)
    #[arg(long, value_name = "N")]
    min_depth: Option<String>,
    /// ablate goals at nesting depth <= N; N may be `inf` (default 1)
    #[arg(long, value_name = "N")]
    max_depth: Option<String>,
    /// only ablate goals whose proof has no nested goal
    #[arg(long)]
    leaves_only: bool,
    /// only ablate proofs with >= N proof commands
    #[arg(long, value_name = "N", default_value = "0")]
    min_size: String,
    /// only ablate proofs with <= N proof commands; N may be `inf`
    #[arg(long, value_name = "N", default_value = "inf")]
    max_size: String,
    /// only ablate lemmas with corpus fan-in >= N
    #[arg(long, value_name = "N", default_value = "0")]
    min_centrality: String,
    /// only ablate lemmas with corpus fan-in <= N; N may be `inf`
    #[arg(long, value_name = "N", default_value = "inf")]
    max_centrality: String,

    /// probability of ablating each selected proof (default 0.5)
    #[arg(short = 'p', long, value_name = "PROB", conflicts_with_all = ["all", "count"])]
    prob: Option<f64>,
    /// ablate every selected proof (-p 1.0)
    #[arg(long, conflicts_with = "count")]
    all: bool,
    /// ablate exactly min(N, matching) selected proofs per theory
    #[arg(long, value_name = "N")]
    count: Option<u64>,
    /// with --count, pick the most-cited proofs (not random)
    #[arg(long, requires = "count")]
    by_centrality: bool,

    /// drop everything after the last inserted `sorry` (challenge only)
    #[arg(long)]
    truncate: bool,
    /// drop challenge top-level lemmas/theorems after the N-th hole (--count); keeps prefix + closers
    #[arg(long)]
    shrink_challenge: bool,
    /// same, for the solution
    #[arg(long)]
    shrink_solution: bool,
    /// challenge: keep only the N holes + their dependency closure (drop unrelated decls)
    #[arg(long)]
    shrink_challenge_minimal: bool,
    /// same, for the solution (restores the deleted lemma + its deps)
    #[arg(long)]
    shrink_solution_minimal: bool,

    /// delete eligible used lemmas + ablate their users (correct-by-construction).
    /// Optional `=N` deletes exactly N lemmas (weighted draw); else --count/-p decide.
    #[arg(long, num_args = 0..=1, require_equals = true, value_name = "N")]
    delete_lemmas: Option<Option<u64>>,
    /// like --delete-lemmas[=N] but draw deletions uniformly (unweighted by user count)
    #[arg(long, num_args = 0..=1, require_equals = true, value_name = "N")]
    delete_lemmas_uniform: Option<Option<u64>>,
    /// like --delete-lemmas[=N] but hole only leaf steps citing L
    #[arg(long, num_args = 0..=1, require_equals = true, value_name = "N")]
    delete_lemmas_leaves: Option<Option<u64>>,
    /// delete-lemmas[=N] with relaxed guards, validated by `isabelle build` (needs isabelle)
    #[arg(long, num_args = 0..=1, require_equals = true, value_name = "N")]
    aggressively_delete_lemmas: Option<Option<u64>>,

    /// session name (for the record's `session` field)
    #[arg(short = 's', long, default_value = "HOL")]
    session: String,
    /// strip DIR prefix from emitted file paths (repeatable)
    #[arg(short = 'd', long = "strip-dir", value_name = "DIR")]
    strip_dir: Vec<String>,
    /// load a name->kind keyword table JSON (default: baked-in HOL)
    #[arg(long, value_name = "FILE")]
    keywords: Option<String>,
    /// emit up to N deduplicated ablations per theory
    #[arg(long, value_name = "N", default_value_t = 1)]
    repeat: u64,
    /// RNG seed (default: time-based)
    #[arg(long, value_name = "N")]
    seed: Option<i64>,
    /// output the ablated theory text instead of JSONL records
    #[arg(long)]
    text: bool,
    /// strict one-object-per-line JSONL (no indentation)
    #[arg(long)]
    compact: bool,
    /// verbose: progress/summary on stderr
    #[arg(short = 'v', long)]
    verbose: bool,
}

fn die(msg: &str) -> ! {
    eprintln!("error: {msg}");
    exit(2)
}

fn parse_depth(s: &str) -> i64 {
    if s == "inf" || s == "infinity" {
        INF
    } else {
        s.parse().unwrap_or_else(|_| die(&format!("bad number: {s}")))
    }
}

fn collect_theories(paths: &[String]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    fn walk(p: &Path, out: &mut Vec<PathBuf>) {
        if p.is_dir() {
            let mut kids: Vec<_> = std::fs::read_dir(p)
                .map(|rd| rd.filter_map(|e| e.ok().map(|e| e.path())).collect())
                .unwrap_or_default();
            kids.sort();
            for k in kids {
                walk(&k, out);
            }
        } else if p.extension().map(|e| e == "thy").unwrap_or(false) {
            out.push(p.to_path_buf());
        }
    }
    for p in paths {
        walk(Path::new(p), &mut out);
    }
    out.dedup();
    out
}

fn fnv1a(s: &str) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in s.bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

fn main() {
    let cli = Cli::parse();

    let preset = cli.difficulty.as_deref().map(|d| {
        preset_of(d).unwrap_or_else(|| {
            die(&format!("unknown --difficulty {d} (expected L0..L{})", LADDER.len() - 1))
        })
    });

    let spec = Spec {
        prob: cli
            .prob
            .or(cli.all.then_some(1.0))
            .or(preset.map(|p| p.prob))
            .unwrap_or(0.5),
        count: cli.count,
        by_centrality: cli.by_centrality,
        min_depth: cli
            .min_depth
            .as_deref()
            .map(parse_depth)
            .or(preset.map(|p| p.min_depth))
            .unwrap_or(1),
        max_depth: cli
            .max_depth
            .as_deref()
            .map(parse_depth)
            .or(preset.map(|p| p.max_depth))
            .unwrap_or(1),
        leaves_only: cli.leaves_only || preset.map(|p| p.leaves_only).unwrap_or(false),
        min_size: parse_depth(&cli.min_size),
        max_size: parse_depth(&cli.max_size),
        min_centrality: parse_depth(&cli.min_centrality),
        max_centrality: parse_depth(&cli.max_centrality),
        truncate: cli.truncate,
        // shrinking the solution implies shrinking the challenge (a shrunk solution
        // against a full challenge is meaningless)
        shrink_challenge: cli.shrink_challenge || cli.shrink_solution,
        shrink_solution: cli.shrink_solution,
        shrink_challenge_minimal: cli.shrink_challenge_minimal || cli.shrink_solution_minimal,
        shrink_solution_minimal: cli.shrink_solution_minimal,
        delete_lemmas: cli.delete_lemmas.is_some()
            || cli.delete_lemmas_uniform.is_some()
            || cli.delete_lemmas_leaves.is_some()
            || cli.aggressively_delete_lemmas.is_some(),
        delete_count: cli
            .delete_lemmas
            .flatten()
            .or(cli.delete_lemmas_uniform.flatten())
            .or(cli.delete_lemmas_leaves.flatten())
            .or(cli.aggressively_delete_lemmas.flatten()),
        delete_uniform: cli.delete_lemmas_uniform.is_some(),
        delete_leaves: cli.delete_lemmas_leaves.is_some(),
        aggressive: cli.aggressively_delete_lemmas.is_some(),
    };
    if spec.min_depth < 1 {
        die("--min-depth must be >= 1");
    }

    let syntax = match &cli.keywords {
        Some(f) => {
            let json = std::fs::read_to_string(f).unwrap_or_else(|e| die(&format!("read {f}: {e}")));
            Syntax::new(isabelle_ablator::keyword::Keywords::from_json(&json))
        }
        None => Syntax::hol(),
    };
    let base: i64 = cli
        .seed
        .unwrap_or_else(|| SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos() as i64);

    let theories = collect_theories(&cli.paths);
    let docs: Vec<(PathBuf, String)> = theories
        .iter()
        .filter_map(|p| std::fs::read_to_string(p).ok().map(|t| (p.clone(), t)))
        .collect();
    if cli.verbose {
        eprintln!(
            "[session {}: {} keywords; {} theories]",
            cli.session,
            syntax.keywords.kinds.len(),
            docs.len()
        );
    }

    // corpus fan-in (always for JSONL emit; otherwise only when filtered)
    let need_cent = spec.uses_centrality() || (!cli.check && !cli.text);
    let fan: HashMap<String, i64> = if need_cent {
        centrality::fan_in(&syntax, docs.iter().map(|(_, t)| t.as_str()))
    } else {
        HashMap::new()
    };
    let centrality_fn = |name: &str| *fan.get(name).unwrap_or(&0);

    if cli.check {
        let ok = run_check(&syntax, &docs, &spec, &centrality_fn);
        if !ok {
            exit(1);
        }
        return;
    }

    if cli.check_build {
        let mut ok = 0u64;
        let mut fail = 0u64;
        for (path, original) in &docs {
            let spans = syntax.parse_spans(original);
            let mut rng = Rng::new((base as u64) ^ fnv1a(&path.to_string_lossy()));
            let r = ablate(&spans, &spec, &mut rng, &centrality_fn);
            let chal = isabelle_ablator::build_check::check_compiles(path, &r.text);
            let sol = isabelle_ablator::build_check::check_compiles(path, &r.solution);
            if chal && sol {
                ok += 1;
            } else {
                fail += 1;
            }
            println!(
                "{:<50} challenge:{:<4} solution:{:<4}",
                path.display(),
                if chal { "ok" } else { "FAIL" },
                if sol { "ok" } else { "FAIL" }
            );
        }
        println!("\nbuild-check: {ok} ok, {fail} failed (of {} files)", docs.len());
        if fail > 0 {
            exit(1);
        }
        return;
    }

    // path display: strip longest matching -d prefix
    let mut strip: Vec<String> = cli
        .strip_dir
        .iter()
        .filter_map(|d| std::fs::canonicalize(d).ok().map(|p| p.to_string_lossy().into_owned()))
        .collect();
    strip.sort_by_key(|s| std::cmp::Reverse(s.len()));
    let display_path = |p: &Path| -> String {
        let abs = std::fs::canonicalize(p)
            .map(|c| c.to_string_lossy().into_owned())
            .unwrap_or_else(|_| p.to_string_lossy().into_owned());
        for base in &strip {
            if abs == *base {
                return p.file_name().unwrap().to_string_lossy().into_owned();
            }
            if let Some(rest) = abs.strip_prefix(&format!("{base}/")) {
                return rest.to_string();
            }
        }
        abs
    };

    let n_repeat = cli.repeat.max(1);
    let mut emitted = 0u64;
    for (path, original) in &docs {
        let display = display_path(path);
        let spans = syntax.parse_spans(original);
        let mut seen: HashSet<String> = HashSet::new();
        let mut produced = 0u64;
        for k in 0..n_repeat {
            let pf = fnv1a(&display);
            let mut rng = Rng::new((base as u64) ^ pf ^ k.wrapping_mul(0x9E3779B97F4A7C15));
            let result = ablate(&spans, &spec, &mut rng, &centrality_fn);
            // aggressive delete-lemmas: only keep challenges that actually compile
            if cli.aggressively_delete_lemmas.is_some()
                && !isabelle_ablator::build_check::check_compiles(path, &result.text)
            {
                continue;
            }
            // only emit *real* challenges: at least one hole was inserted AND the
            // challenge differs from the solution. A theory with no eligible lemmas (or
            // a no-op ablation) otherwise yields a trivial, already-complete challenge
            // that would inflate any downstream baseline
            if result.ablated == 0 || result.text == result.solution {
                continue;
            }
            if seen.insert(result.text.clone()) {
                if cli.text {
                    print!("{}", result.text);
                } else {
                    let variant = if n_repeat > 1 { Some(produced) } else { None };
                    let obj = record(
                        &display,
                        &cli.session,
                        &spec,
                        base,
                        variant,
                        cli.difficulty.as_deref(),
                        &result,
                    );
                    if cli.compact {
                        println!("{}", serde_json::to_string(&obj).unwrap());
                    } else {
                        println!("{}", serde_json::to_string_pretty(&obj).unwrap());
                    }
                }
                produced += 1;
                emitted += 1;
            }
        }
    }
    if cli.verbose {
        eprintln!("[emitted {emitted} {}]", if cli.text { "theories" } else { "records" });
    }
}

fn run_check(
    syntax: &Syntax,
    docs: &[(PathBuf, String)],
    spec: &Spec,
    centrality_fn: &dyn Fn(&str) -> i64,
) -> bool {
    // disable count / context shaping (validate the proof ablation itself)
    let base_spec = Spec {
        count: None,
        truncate: false,
        shrink_challenge: false,
        shrink_solution: false,
        shrink_challenge_minimal: false,
        shrink_solution_minimal: false,
        ..spec.clone()
    };
    let mut n_files = 0;
    let mut n_goals = 0i64;
    let mut n_ablated = 0i64;
    let mut roundtrip_fail: Vec<String> = Vec::new();
    let mut delimit_fail: Vec<String> = Vec::new();
    let mut reparse_fail: Vec<String> = Vec::new();
    let mut ablate_ns = 0u128;

    let wall = Instant::now();
    for (path, text) in docs {
        let name = path.to_string_lossy().to_string();
        let spans = syntax.parse_spans(text);

        let mut rng = Rng::new(0);
        let id = ablate(&spans, &Spec { prob: 0.0, ..base_spec.clone() }, &mut rng, centrality_fn);
        if id.text != *text {
            roundtrip_fail.push(name.clone());
        }

        let t0 = Instant::now();
        let mut rng = Rng::new(0);
        let all = ablate(&spans, &Spec { prob: 1.0, ..base_spec.clone() }, &mut rng, centrality_fn);
        ablate_ns += t0.elapsed().as_nanos();
        n_files += 1;
        n_goals += all.total;
        n_ablated += all.ablated;
        if all.ablated != all.total {
            delimit_fail.push(name.clone());
        }
        if count_theory_goals(syntax, &all.text) != count_theory_goals(syntax, text) {
            reparse_fail.push(name.clone());
        }
    }
    let wall_s = wall.elapsed().as_secs_f64();
    let ablate_s = ablate_ns as f64 / 1e9;

    println!("\n================ ablation self-test ================");
    println!("theories checked     : {n_files}");
    println!("in-range goals       : {n_goals}");
    let pct = if n_goals > 0 { 100.0 * n_ablated as f64 / n_goals as f64 } else { 0.0 };
    println!("cleanly ablated      : {n_ablated} ({pct:.2}%)");
    if ablate_s > 0.0 {
        println!(
            "ablation time        : {ablate_s:.2} s ({:.0} theories/s, {:.0} goals/s)",
            n_files as f64 / ablate_s,
            n_goals as f64 / ablate_s
        );
    }
    println!("self-test wall time  : {wall_s:.2} s");
    println!("round-trip failures  : {}", roundtrip_fail.len());
    println!("delimitation misses  : {}", delimit_fail.len());
    println!("re-parse mismatches  : {}", reparse_fail.len());
    for (label, xs) in [
        ("round-trip failures", &roundtrip_fail),
        ("delimitation misses", &delimit_fail),
        ("re-parse mismatches", &reparse_fail),
    ] {
        if !xs.is_empty() {
            println!("\n-- {label} ({}), first 10:", xs.len());
            for x in xs.iter().take(10) {
                println!("   {x}");
            }
        }
    }
    let ok = roundtrip_fail.is_empty() && reparse_fail.is_empty();
    println!("\nRESULT: {}", if ok { "OK" } else { "FAILURES PRESENT" });
    ok
}
