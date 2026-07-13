//! (challenge, solution) JSON record — same schema/field order as
//! `scala/src/Ablate.scala` `record`.

use crate::ablate::{AblationResult, Spec, INF};
use crate::sha1;
use serde_json::{json, Value};

fn depth_json(d: i64) -> Value {
    if d == INF {
        Value::String("inf".into())
    } else {
        json!(d)
    }
}

fn task_id(file_path: &str, variant: Option<u64>) -> String {
    let h = sha1::hex(file_path.as_bytes());
    let mut id = format!("ablate_{}", &h[..12]);
    if let Some(v) = variant {
        id.push_str(&format!("_{}", v));
    }
    id
}

fn theory_name(file_path: &str) -> String {
    let base = file_path.rsplit('/').next().unwrap_or(file_path);
    base.strip_suffix(".thy").unwrap_or(base).to_string()
}

/// Stable, unique per-challenge id (so labels join to features exactly). Derived from
/// the inputs that fully determine a challenge; unlike `task_id` it does not collide
/// across challenges mined from the same file. See docs/difficulty-features.md §1.
fn challenge_id(
    file_path: &str,
    seed: i64,
    variant: Option<u64>,
    result: &AblationResult,
) -> String {
    let mut deleted: Vec<&str> = result.deleted.iter().map(|d| d.name.as_str()).collect();
    deleted.sort();
    let mut holed: Vec<&str> = result.holes.iter().map(|h| h.theorem_name.as_str()).collect();
    holed.sort();
    let variant = variant.map(|v| v.to_string()).unwrap_or_default();
    let key = [
        file_path.to_string(),
        seed.to_string(),
        variant,
        deleted.join(","),
        holed.join(","),
    ]
    .join("|");
    sha1::hex(key.as_bytes())[..16].to_string()
}

#[allow(clippy::too_many_arguments)]
pub fn record(
    file_path: &str,
    session: &str,
    spec: &Spec,
    seed: i64,
    variant: Option<u64>,
    difficulty: Option<&str>,
    repo: Option<&str>,
    revision: Option<&str>,
    isabelle_version: Option<&str>,
    result: &AblationResult,
) -> Value {
    let holes: Vec<Value> = result
        .holes
        .iter()
        .map(|h| {
            json!({
                "theorem_name": h.theorem_name,
                "depth": h.depth,
                "n_commands": h.n_commands,
                "n_lines": h.n_lines,
                "is_leaf": h.is_leaf,
                "centrality": h.centrality,
                "method": h.method,
                "proof_text": h.proof_text,
                // proof-complexity metrics (spec §2); n_lines above matches metrics.n_lines
                "n_chars": h.metrics.n_chars,
                "n_subproofs": h.metrics.n_subproofs,
                "n_tactics": h.metrics.n_tactics,
                "cyclomatic": h.metrics.cyclomatic,
            })
        })
        .collect();
    let deleted_lemmas: Vec<Value> = result
        .deleted
        .iter()
        .map(|d| {
            json!({
                "name": d.name,
                "text": d.text,
                "fan_in": d.fan_in,
                "n_lines": d.metrics.n_lines,
                "n_chars": d.metrics.n_chars,
                "n_subproofs": d.metrics.n_subproofs,
                "n_tactics": d.metrics.n_tactics,
                "cyclomatic": d.metrics.cyclomatic,
            })
        })
        .collect();
    let corollaries: Vec<Value> = result
        .corollaries
        .iter()
        .map(|c| {
            json!({
                "name": c.name,
                "fan_in": c.fan_in,
                "n_lines": c.metrics.n_lines,
                "n_chars": c.metrics.n_chars,
                "n_subproofs": c.metrics.n_subproofs,
                "n_tactics": c.metrics.n_tactics,
                "cyclomatic": c.metrics.cyclomatic,
            })
        })
        .collect();

    json!({
        "task_id": task_id(file_path, variant),
        "challenge_id": challenge_id(file_path, seed, variant, result),
        "proof_assistant": "isabelle",
        "session": session,
        // provenance: git repo + commit the source was ablated from. For the packaged
        // AFP there is no local git checkout, so these come from --repo/--revision
        // (e.g. the isabelle-prover/mirror-afp-devel mirror) and `isabelle_version`
        // pins the Isabelle release the session was built against.
        "repo": repo.map(Value::from).unwrap_or(Value::Null),
        "revision": revision.map(Value::from).unwrap_or(Value::Null),
        "isabelle_version": isabelle_version.map(Value::from).unwrap_or(Value::Null),
        "file_path": file_path,
        "theory": theory_name(file_path),
        "variant": variant.map(Value::from).unwrap_or(Value::Null),
        "challenge_type": if result.deleted.is_empty() { "proof_ablate" } else { "lemma_delete" },
        "difficulty": difficulty.map(Value::from).unwrap_or(Value::Null),
        "count": spec.count.map(Value::from).unwrap_or(Value::Null),
        "by_centrality": spec.by_centrality,
        "ablation_prob": if spec.count.is_some() { Value::Null } else { json!(spec.prob) },
        "min_depth": spec.min_depth,
        "max_depth": depth_json(spec.max_depth),
        "leaves_only": spec.leaves_only,
        "min_size": spec.min_size,
        "max_size": depth_json(spec.max_size),
        "min_centrality": spec.min_centrality,
        "max_centrality": depth_json(spec.max_centrality),
        "seed": seed,
        "n_proofs": result.total,
        "n_ablated": result.ablated,
        "holes_filled": holes,
        "deleted_lemmas": deleted_lemmas,
        "corollaries": corollaries,
        "closure_size": result.closure_size,
        "challenge_file_content": result.text,
        // solution stored as a diff against the challenge (apply to recover it) —
        // full files are huge for big theories (issue #107)
        "solution_diff": crate::diff::unified(&result.text, &result.solution),
        "solution_file_content": result.solution,
    })
}
