//! `ablate` CLI — Rust port of the Isabelle/Scala ablator's command line.
//! (Build validation `--check-build` stays in the Scala tool; this tool is the
//! portable/WASM ablation engine.)

use isabelle_ablator::ablate::{ablate, preset_of, Rng, Spec, INF, LADDER};
use isabelle_ablator::centrality;
use isabelle_ablator::record::record;
use isabelle_ablator::span::Syntax;
use isabelle_ablator::{count_theory_goals};
use std::collections::HashMap;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::exit;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

const USAGE: &str = "\
Usage: ablate [OPTIONS] PATH...

  Ablate proofs in Isabelle theories, replacing them with `sorry`.
  PATH... is any mix of .thy files and directories (walked for *.thy).
  Default: emit one indented JSON (challenge, solution) record per theory.

  Modes:
    --check          run the corpus self-test (round-trip + delimitation)

  Difficulty (raw knobs override any --difficulty preset):
    --difficulty L   preset ladder L0 (easy) .. L4 (code+spec only)
    --min-depth N    ablate goals at nesting depth >= N (default: 1)
    --max-depth N    ablate goals at nesting depth <= N; N may be `inf`
    --leaves-only    only ablate goals whose proof has no nested goal
    --min-size N / --max-size N   proof-command-count window (N may be `inf`)
    --min-centrality N / --max-centrality N   corpus fan-in window
    -p PROB          probability of ablating each selected proof (default: 0.5)
    --all            ablate every selected proof (-p 1.0)
    --count N        ablate exactly min(N, matching) proofs (excl. -p/--all)
    --by-centrality  with --count, pick the most-cited proofs

  Context shaping (challenge text only; ignored by --check):
    --truncate       drop everything after the last inserted `sorry`
    --shrink-context drop top-level lemmas/theorems after the last ablated one

  Other:
    -d DIR           strip DIR prefix from emitted file paths (repeatable)
    --keywords FILE  load a name->kind keyword table JSON (default: baked-in HOL)
    --repeat N       emit up to N deduplicated ablations per theory (default: 1)
    --seed N         RNG seed (default: time-based)
    --text           output the ablated theory text instead of JSONL
    --compact        strict one-object-per-line JSONL (no indentation)
    -v               verbose: progress/summary on stderr
";

fn parse_depth(s: &str) -> i64 {
    if s == "inf" || s == "infinity" {
        INF
    } else {
        s.parse().unwrap_or_else(|_| die(&format!("bad number: {s}")))
    }
}

fn die(msg: &str) -> ! {
    eprintln!("{msg}");
    exit(2)
}

fn collect_theories(paths: &[String]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    fn walk(p: &Path, out: &mut Vec<PathBuf>) {
        if p.is_dir() {
            let mut kids: Vec<_> = std::fs::read_dir(p)
                .map(|rd| rd.filter_map(|e| e.ok().map(|e| e.path())).collect())
                .unwrap_or_else(|_| Vec::new());
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
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut session = "HOL".to_string();
    let mut keywords_file: Option<String> = None;
    let mut seed: Option<i64> = None;
    let mut verbose = false;
    let mut check = false;
    let mut compact = false;
    let mut text_mode = false;
    let mut difficulty: Option<String> = None;
    let mut prob_opt: Option<f64> = None;
    let mut count_opt: Option<u64> = None;
    let mut min_depth_opt: Option<i64> = None;
    let mut max_depth_opt: Option<i64> = None;
    let mut leaves_opt: Option<bool> = None;
    let mut min_size_opt: Option<i64> = None;
    let mut max_size_opt: Option<i64> = None;
    let mut min_cent_opt: Option<i64> = None;
    let mut max_cent_opt: Option<i64> = None;
    let mut by_centrality = false;
    let mut truncate = false;
    let mut shrink_context = false;
    let mut repeat: u64 = 1;
    let mut paths: Vec<String> = Vec::new();
    let mut strip_dirs: Vec<String> = Vec::new();

    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut next = || it.next().cloned().unwrap_or_else(|| die(&format!("missing arg for {a}")));
        match a.as_str() {
            "--check" => check = true,
            "--compact" => compact = true,
            "--text" => text_mode = true,
            "-v" => verbose = true,
            "--all" => prob_opt = Some(1.0),
            "--by-centrality" => by_centrality = true,
            "--truncate" => truncate = true,
            "--shrink-context" => shrink_context = true,
            "--leaves-only" => leaves_opt = Some(true),
            "--difficulty" => difficulty = Some(next()),
            "--min-depth" => min_depth_opt = Some(parse_depth(&next())),
            "--max-depth" => max_depth_opt = Some(parse_depth(&next())),
            "--min-size" => min_size_opt = Some(parse_depth(&next())),
            "--max-size" => max_size_opt = Some(parse_depth(&next())),
            "--min-centrality" => min_cent_opt = Some(parse_depth(&next())),
            "--max-centrality" => max_cent_opt = Some(parse_depth(&next())),
            "-p" => prob_opt = Some(next().parse().unwrap_or_else(|_| die("bad -p"))),
            "--count" => count_opt = Some(next().parse().unwrap_or_else(|_| die("bad --count"))),
            "--repeat" => repeat = next().parse().unwrap_or_else(|_| die("bad --repeat")),
            "--seed" => seed = Some(next().parse().unwrap_or_else(|_| die("bad --seed"))),
            "-s" => session = next(),
            "-d" => strip_dirs.push(next()),
            "--keywords" => keywords_file = Some(next()),
            "-h" | "--help" => {
                print!("{USAGE}");
                return;
            }
            s if s.starts_with('-') && s != "-" => die(&format!("Unknown option: {s}\n{USAGE}")),
            s => paths.push(s.to_string()),
        }
    }

    let preset = difficulty.as_deref().map(|d| {
        preset_of(d).unwrap_or_else(|| die(&format!("Unknown --difficulty {d} (expected L0..L{})", LADDER.len() - 1)))
    });
    if count_opt.is_some() && prob_opt.is_some() {
        die("--count cannot be combined with -p / --all");
    }
    if by_centrality && count_opt.is_none() {
        die("--by-centrality only applies with --count");
    }

    let spec = Spec {
        prob: prob_opt.or(preset.map(|p| p.prob)).unwrap_or(0.5),
        count: count_opt,
        by_centrality,
        min_depth: min_depth_opt.or(preset.map(|p| p.min_depth)).unwrap_or(1),
        max_depth: max_depth_opt.or(preset.map(|p| p.max_depth)).unwrap_or(1),
        leaves_only: leaves_opt.or(preset.map(|p| p.leaves_only)).unwrap_or(false),
        min_size: min_size_opt.unwrap_or(0),
        max_size: max_size_opt.unwrap_or(INF),
        min_centrality: min_cent_opt.unwrap_or(0),
        max_centrality: max_cent_opt.unwrap_or(INF),
        truncate,
        shrink_context,
    };
    if spec.min_depth < 1 {
        die("--min-depth must be >= 1");
    }
    if paths.is_empty() {
        die(USAGE);
    }

    let syntax = match keywords_file {
        Some(f) => {
            let json = std::fs::read_to_string(&f).unwrap_or_else(|e| die(&format!("read {f}: {e}")));
            Syntax::new(isabelle_ablator::keyword::Keywords::from_json(&json))
        }
        None => Syntax::hol(),
    };
    let base: i64 = seed.unwrap_or_else(|| {
        SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos() as i64
    });

    let theories = collect_theories(&paths);
    // read all up front (centrality + emit both need contents)
    let docs: Vec<(PathBuf, String)> = theories
        .iter()
        .filter_map(|p| std::fs::read_to_string(p).ok().map(|t| (p.clone(), t)))
        .collect();
    if verbose {
        eprintln!("[session {session}: {} keywords; {} theories]", syntax.keywords.kinds.len(), docs.len());
    }

    // corpus fan-in (always for JSONL emit; otherwise only when filtered)
    let need_cent = spec.uses_centrality() || (!check && !text_mode);
    let fan: HashMap<String, i64> = if need_cent {
        centrality::fan_in(&syntax, docs.iter().map(|(_, t)| t.as_str()))
    } else {
        HashMap::new()
    };
    let centrality_fn = |name: &str| *fan.get(name).unwrap_or(&0);

    if check {
        let ok = run_check(&syntax, &docs, &spec, &centrality_fn);
        if !ok {
            exit(1);
        }
        return;
    }

    // path display: strip longest matching -d prefix
    let mut strip: Vec<String> = strip_dirs
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

    let n_repeat = repeat.max(1);
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
            if seen.insert(result.text.clone()) {
                if text_mode {
                    print!("{}", result.text);
                } else {
                    let variant = if n_repeat > 1 { Some(produced) } else { None };
                    let obj = record(&display, &session, &spec, base, variant, difficulty.as_deref(), original, &result);
                    if compact {
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
    if verbose {
        eprintln!("[emitted {emitted} {}]", if text_mode { "theories" } else { "records" });
    }
}

fn run_check(
    syntax: &Syntax,
    docs: &[(PathBuf, String)],
    spec: &Spec,
    centrality_fn: &dyn Fn(&str) -> i64,
) -> bool {
    // disable count / context shaping (validate the proof ablation itself)
    let base_spec = Spec { count: None, truncate: false, shrink_context: false, ..spec.clone() };
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
        println!("ablation time        : {ablate_s:.2} s ({:.0} theories/s, {:.0} goals/s)",
            n_files as f64 / ablate_s, n_goals as f64 / ablate_s);
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
