//! WASM bindings for in-browser ablation. Build with:
//!   wasm-pack build --target web --out-dir web/pkg --features wasm --no-default-features

use crate::ablate::{ablate, preset_of, Rng, Spec, INF};
use crate::centrality;
use crate::span::Syntax;
use serde_json::{json, Value};
use wasm_bindgen::prelude::*;

fn as_i64_inf(v: &Value, default: i64) -> i64 {
    match v {
        Value::Number(n) => n.as_i64().unwrap_or(default),
        Value::String(s) if s == "inf" || s == "infinity" => INF,
        Value::String(s) => s.parse().unwrap_or(default),
        _ => default,
    }
}
fn as_bool(v: &Value, default: bool) -> bool {
    v.as_bool().unwrap_or(default)
}

fn spec_from(opts: &Value) -> (Spec, Option<String>) {
    let g = |k: &str| &opts[k];
    let difficulty = opts.get("difficulty").and_then(|v| v.as_str()).map(|s| s.to_string());
    let preset = difficulty.as_deref().and_then(preset_of);

    let count = match g("count") {
        Value::Number(n) => n.as_u64(),
        _ => None,
    };
    let prob = match g("prob") {
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
    .or(preset.map(|p| p.prob))
    .unwrap_or(0.5);

    let spec = Spec {
        prob,
        count,
        by_centrality: as_bool(g("by_centrality"), false),
        min_depth: {
            let v = g("min_depth");
            if v.is_null() { preset.map(|p| p.min_depth).unwrap_or(1) } else { as_i64_inf(v, 1) }
        },
        max_depth: {
            let v = g("max_depth");
            if v.is_null() { preset.map(|p| p.max_depth).unwrap_or(1) } else { as_i64_inf(v, 1) }
        },
        leaves_only: {
            let v = g("leaves_only");
            if v.is_null() { preset.map(|p| p.leaves_only).unwrap_or(false) } else { as_bool(v, false) }
        },
        min_size: as_i64_inf(g("min_size"), 0),
        max_size: as_i64_inf(g("max_size"), INF),
        min_centrality: as_i64_inf(g("min_centrality"), 0),
        max_centrality: as_i64_inf(g("max_centrality"), INF),
        truncate: as_bool(g("truncate"), false),
        shrink_challenge: as_bool(g("shrink_challenge"), false),
        shrink_solution: as_bool(g("shrink_solution"), false),
        shrink_challenge_minimal: as_bool(g("shrink_challenge_minimal"), false),
        shrink_solution_minimal: as_bool(g("shrink_solution_minimal"), false),
        delete_lemmas: as_bool(g("delete_lemmas"), false),
        delete_count: {
            let v = g("delete_count");
            if v.is_null() { None } else { v.as_u64() }
        },
        delete_uniform: as_bool(g("delete_uniform"), false),
        delete_leaves: as_bool(g("delete_leaves"), false),
        aggressive: false, // the prover-backed path is never exposed to the browser
        ablate_scripts: as_bool(g("ablate_scripts"), false),
    };
    (spec, difficulty)
}

/// Ablate one theory. `opts_json` is a JSON object of the difficulty knobs
/// (see the website). Returns a JSON string: `{text, total, ablated, holes}`.
/// Centrality fan-in is computed within the given text (corpus-of-one).
#[wasm_bindgen]
pub fn ablate_theory(text: &str, opts_json: &str, seed: f64) -> String {
    let opts: Value = serde_json::from_str(opts_json).unwrap_or(Value::Null);
    let (spec, _difficulty) = spec_from(&opts);

    let syntax = Syntax::hol();
    let spans = syntax.parse_spans(text);

    let fan = if spec.uses_centrality() {
        centrality::fan_in(&syntax, std::iter::once(text))
    } else {
        std::collections::HashMap::new()
    };
    let centrality_fn = |name: &str| *fan.get(name).unwrap_or(&0);

    let mut rng = Rng::new(seed.to_bits());
    let r = ablate(&spans, &spec, &mut rng, &centrality_fn);

    let holes: Vec<Value> = r
        .holes
        .iter()
        .map(|h| {
            json!({
                "theorem_name": h.theorem_name, "depth": h.depth, "n_commands": h.n_commands,
                "n_lines": h.n_lines, "is_leaf": h.is_leaf, "centrality": h.centrality,
                "method": h.method, "proof_text": h.proof_text,
            })
        })
        .collect();
    let deleted: Vec<Value> = r.deleted.iter().map(|(nm, txt)| json!({ "name": nm, "text": txt })).collect();
    json!({ "text": r.text, "solution_diff": crate::diff::unified(&r.text, &r.solution), "total": r.total, "ablated": r.ablated, "holes": holes, "deleted_lemmas": deleted }).to_string()
}

/// Number of HOL keywords baked in (sanity check for the JS side).
#[wasm_bindgen]
pub fn keyword_count() -> usize {
    Syntax::hol().keywords.kinds.len()
}
