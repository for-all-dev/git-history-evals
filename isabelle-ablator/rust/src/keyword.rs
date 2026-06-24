//! Isar keyword classification + the per-session keyword table.
//!
//! Kind strings and category sets mirror `Pure/Isar/keyword.scala`. The
//! name->kind table is baked in (exported from a real Isabelle session) so the
//! tokenizer needs no prover at runtime — see `data/hol-keywords.json`.

use std::collections::HashMap;

// kind strings (Pure/Isar/keyword.scala)
pub const THY_BEGIN: &str = "thy_begin";
pub const THY_END: &str = "thy_end";
pub const THY_DECL: &str = "thy_decl";
pub const THY_DECL_BLOCK: &str = "thy_decl_block";
pub const THY_DEFN: &str = "thy_defn";
pub const THY_STMT: &str = "thy_stmt";
pub const THY_LOAD: &str = "thy_load";
pub const THY_GOAL: &str = "thy_goal";
pub const THY_GOAL_DEFN: &str = "thy_goal_defn";
pub const THY_GOAL_STMT: &str = "thy_goal_stmt";
pub const QED: &str = "qed";
pub const QED_SCRIPT: &str = "qed_script";
pub const QED_BLOCK: &str = "qed_block";
pub const QED_GLOBAL: &str = "qed_global";
pub const PRF_GOAL: &str = "prf_goal";
pub const PRF_BLOCK: &str = "prf_block";
pub const NEXT_BLOCK: &str = "next_block";
pub const PRF_OPEN: &str = "prf_open";
pub const PRF_CLOSE: &str = "prf_close";
pub const PRF_CHAIN: &str = "prf_chain";
pub const PRF_DECL: &str = "prf_decl";
pub const PRF_ASM: &str = "prf_asm";
pub const PRF_ASM_GOAL: &str = "prf_asm_goal";
pub const PRF_SCRIPT: &str = "prf_script";
pub const PRF_SCRIPT_GOAL: &str = "prf_script_goal";
pub const PRF_SCRIPT_ASM_GOAL: &str = "prf_script_asm_goal";
pub const BEFORE_COMMAND: &str = "before_command";
pub const QUASI_COMMAND: &str = "quasi_command";

/// thy_goal{,_defn,_stmt} — opens a proof at theory level.
pub fn is_theory_goal(k: &str) -> bool {
    matches!(k, THY_GOAL | THY_GOAL_DEFN | THY_GOAL_STMT)
}
/// prf_goal / prf_asm_goal / prf_script_goal / prf_script_asm_goal — nested goals.
pub fn is_proof_goal(k: &str) -> bool {
    matches!(k, PRF_GOAL | PRF_ASM_GOAL | PRF_SCRIPT_GOAL | PRF_SCRIPT_ASM_GOAL)
}
/// proof_close = qed ∪ {prf_close}.
pub fn is_proof_close(k: &str) -> bool {
    matches!(k, QED | QED_SCRIPT | QED_BLOCK | PRF_CLOSE)
}
pub fn is_qed_global(k: &str) -> bool {
    k == QED_GLOBAL
}
/// theory commands (begin/end/load/decl/defn/stmt/goal*).
pub fn is_theory(k: &str) -> bool {
    matches!(
        k,
        THY_BEGIN
            | THY_END
            | THY_LOAD
            | THY_DECL
            | THY_DECL_BLOCK
            | THY_DEFN
            | THY_STMT
            | THY_GOAL
            | THY_GOAL_DEFN
            | THY_GOAL_STMT
    )
}

/// Per-session keyword table: name -> kind ("" for minor keywords).
pub struct Keywords {
    pub kinds: HashMap<String, String>,
}

impl Keywords {
    /// Parse a `{ "name": "kind", ... }` JSON object (as exported from Isabelle).
    pub fn from_json(json: &str) -> Self {
        let kinds: HashMap<String, String> =
            serde_json::from_str(json).expect("keyword table JSON");
        Keywords { kinds }
    }

    /// The baked-in HOL keyword table.
    pub fn hol() -> Self {
        Self::from_json(include_str!("../data/hol-keywords.json"))
    }

    pub fn kind(&self, name: &str) -> Option<&str> {
        self.kinds.get(name).map(|s| s.as_str())
    }

    /// A keyword is "minor" iff its kind is "", before_command, or quasi_command;
    /// otherwise "major" (a command). (Pure/Isar/keyword.scala make_lexicon.)
    fn is_minor_kind(kind: &str) -> bool {
        kind.is_empty() || kind == BEFORE_COMMAND || kind == QUASI_COMMAND
    }

    pub fn major_names(&self) -> Vec<&str> {
        self.kinds
            .iter()
            .filter(|(_, k)| !Self::is_minor_kind(k))
            .map(|(n, _)| n.as_str())
            .collect()
    }

    pub fn minor_names(&self) -> Vec<&str> {
        self.kinds
            .iter()
            .filter(|(_, k)| Self::is_minor_kind(k))
            .map(|(n, _)| n.as_str())
            .collect()
    }
}
