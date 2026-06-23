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

#[allow(clippy::too_many_arguments)]
pub fn record(
    file_path: &str,
    session: &str,
    spec: &Spec,
    seed: i64,
    variant: Option<u64>,
    difficulty: Option<&str>,
    original: &str,
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
            })
        })
        .collect();

    json!({
        "task_id": task_id(file_path, variant),
        "proof_assistant": "isabelle",
        "session": session,
        "file_path": file_path,
        "theory": theory_name(file_path),
        "variant": variant.map(Value::from).unwrap_or(Value::Null),
        "challenge_type": "proof_ablate",
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
        "challenge_file_content": result.text,
        "solution_file_content": original,
    })
}
