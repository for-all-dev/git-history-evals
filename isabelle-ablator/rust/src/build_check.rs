//! Native-only compile-test for the CLI's `--check-build`. Gated to the `cli`
//! feature so it is NEVER compiled into the WASM core (it shells out to
//! `isabelle build`).
//!
//! Strategy — build only the *upward closure*. We synthesize a throwaway
//! session that lists ONLY the ablated theory; `isabelle build` resolves that
//! theory's imports automatically and never builds dependents (they are not in
//! the session). So dropping trailing decls via `--shrink` can't break the
//! check, and a dependent theory that referenced a dropped name is simply never
//! built. `quick_and_dirty` lets the challenge's `sorry`s elaborate.

use std::fs;
use std::path::Path;
use std::process::Command;

/// The theory name from the `theory <Name>` header.
fn theory_name(content: &str) -> Option<String> {
    let mut it = content.split_whitespace();
    while let Some(tok) = it.next() {
        if tok == "theory" {
            return it.next().map(|s| s.to_string());
        }
    }
    None
}

/// Put `content` at `file`, build a throwaway session containing only that
/// theory (so only its upward closure is built), then restore the original.
/// Returns whether `isabelle build` succeeded.
pub fn check_compiles(file: &Path, content: &str) -> bool {
    let name = match theory_name(content) {
        Some(n) => n,
        None => return false,
    };
    let thy_dir = match file.parent() {
        Some(d) => d.to_path_buf(),
        None => return false,
    };
    let orig = match fs::read_to_string(file) {
        Ok(s) => s,
        Err(_) => return false,
    };

    let tmp = std::env::temp_dir().join(format!("ablate-check-{}-{}", std::process::id(), name));
    let _ = fs::create_dir_all(&tmp);
    let dir = thy_dir.canonicalize().unwrap_or(thy_dir.clone());
    let root = format!(
        "session \"AblateCheck\" = \"HOL\" +\n  directories \"{}\"\n  theories\n    \"{}\"\n",
        dir.display(),
        name
    );
    let _ = fs::write(tmp.join("ROOT"), root);

    let _ = fs::write(file, content);
    let result = Command::new("isabelle")
        .args(["build", "-o", "quick_and_dirty=true", "-d"])
        .arg(&tmp)
        .arg("AblateCheck")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    let _ = fs::write(file, &orig); // restore original
    let _ = fs::remove_dir_all(&tmp);
    result
}
