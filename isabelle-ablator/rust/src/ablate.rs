//! Proof ablation: the depth-walk + difficulty knobs, ported from
//! `scala/src/Ablate.scala`. Operates on parsed spans; replaces selected proofs
//! with `sorry`, preserving everything else byte-for-byte.

use crate::keyword as kw;
use crate::span::Span;
use crate::token::Token;

pub const INF: i64 = i64::MAX;

/// Which proofs to ablate. `count` overrides `prob`.
#[derive(Clone, Debug)]
pub struct Spec {
    pub prob: f64,
    pub count: Option<u64>,
    pub by_centrality: bool,
    pub min_depth: i64,
    pub max_depth: i64,
    pub leaves_only: bool,
    pub min_size: i64,
    pub max_size: i64,
    pub min_centrality: i64,
    pub max_centrality: i64,
    pub truncate: bool,
    pub shrink_challenge: bool,
    pub shrink_solution: bool,
    pub shrink_challenge_minimal: bool,
    pub shrink_solution_minimal: bool,
    pub delete_lemmas: bool,
    pub delete_count: Option<u64>,
    pub delete_uniform: bool,
    pub delete_leaves: bool,
    pub aggressive: bool,
    /// corollary mode: restrict deletion candidates to one random theorem's
    /// (the "corollary") transitive in-file dependency closure, re-picking a fresh
    /// corollary only when the current closure is exhausted before the target count.
    pub corollary: bool,
    /// Ablate apply-scripts whole (drop the entire script) instead of the default
    /// prefix-cut (keep some `apply` steps, `sorry` the rest).
    pub ablate_scripts: bool,
}

impl Default for Spec {
    fn default() -> Spec {
        Spec {
            prob: 0.5,
            count: None,
            by_centrality: false,
            min_depth: 1,
            max_depth: 1,
            leaves_only: false,
            min_size: 0,
            max_size: INF,
            min_centrality: 0,
            max_centrality: INF,
            truncate: false,
            shrink_challenge: false,
            shrink_solution: false,
            shrink_challenge_minimal: false,
            shrink_solution_minimal: false,
            delete_lemmas: false,
            delete_count: None,
            delete_uniform: false,
            delete_leaves: false,
            aggressive: false,
            corollary: false,
            ablate_scripts: false,
        }
    }
}

impl Spec {
    pub fn uses_centrality(&self) -> bool {
        self.min_centrality > 0 || self.max_centrality != INF || self.by_centrality
    }
}

#[derive(Clone, Debug)]
pub struct Hole {
    pub theorem_name: String,
    pub depth: i64,
    pub n_commands: i64,
    pub n_lines: i64,
    pub is_leaf: bool,
    pub centrality: i64,
    pub method: String,
    pub proof_text: String,
}

#[derive(Clone, Debug)]
pub struct AblationResult {
    pub text: String,
    pub solution: String,
    pub total: i64,
    pub ablated: i64,
    pub holes: Vec<Hole>,
    pub deleted: Vec<(String, String)>, // (name, original block) for --delete-lemmas
}

/* ---------- small seedable PRNG (SplitMix64) ---------- */

pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Rng {
        Rng(seed)
    }
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        z ^ (z >> 31)
    }
    fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }
    fn shuffle<T>(&mut self, v: &mut [T]) {
        // Fisher-Yates
        let mut i = v.len();
        while i > 1 {
            i -= 1;
            let j = (self.next_u64() % (i as u64 + 1)) as usize;
            v.swap(i, j);
        }
    }
}

/* ---------- helpers on spans/tokens ---------- */

fn kind_of(span: &Span) -> Option<&str> {
    span.keyword_kind()
}
fn is_goal(k: &str) -> bool {
    kw::is_theory_goal(k) || kw::is_proof_goal(k)
}
fn is_ablatable(k: &str) -> bool {
    kw::is_theory_goal(k) || k == kw::PRF_GOAL || k == kw::PRF_ASM_GOAL
}
fn is_prefatory(k: &str) -> bool {
    k == kw::PRF_CHAIN || k == kw::PRF_DECL || k == kw::PRF_ASM
}

fn n_lines(s: &str) -> i64 {
    if s.is_empty() {
        0
    } else {
        (s.matches('\n').count() + 1) as i64
    }
}

fn proper_after_command(content: &[Token]) -> Vec<&Token> {
    content
        .iter()
        .skip_while(|t| !t.is_command())
        .skip(1)
        .filter(|t| t.is_proper())
        .collect()
}

/// Best-effort theorem name (Ablate.scala goal_name).
pub fn goal_name(span: &Span) -> String {
    let after = proper_after_command(&span.content);
    let rest: &[&Token] = if after
        .first()
        .map(|t| t.is_keyword_str("("))
        .unwrap_or(false)
    {
        // drop until ")" then drop it
        match after.iter().position(|t| t.is_keyword_str(")")) {
            Some(p) => &after[p + 1..],
            None => &after[after.len()..],
        }
    } else {
        &after[..]
    };
    if rest.len() >= 2
        && rest[0].is_ident()
        && (rest[1].is_keyword_str(":") || rest[1].is_keyword_str("["))
    {
        rest[0].source.clone()
    } else {
        String::new()
    }
}

fn classify_method(span: &Span) -> String {
    match span.name() {
        "by" => proper_after_command(&span.content)
            .iter()
            .find(|t| t.is_ident())
            .map(|t| format!("by:{}", t.source))
            .unwrap_or_else(|| "by".to_string()),
        "apply" | "apply_end" => "apply".to_string(),
        "proof" => "structured".to_string(),
        "." | ".." => "trivial".to_string(),
        other => other.to_string(),
    }
}

/// Indices of the top-level (depth `base`) `apply`/`apply_end` steps in a proof body
/// `[start, end)`. `base` is the proof body's own depth (the goal's depth). Steps inside
/// a nested `have`/`show`/`{` are excluded (depth tracked exactly as in `measure`). An
/// apply-script proof is one whose step list is non-empty; keeping a prefix of these
/// steps and admitting the rest with `sorry` is always well-formed.
fn apply_steps(spans: &[Span], start: usize, end: usize, base: i64) -> Vec<usize> {
    let mut steps = Vec::new();
    let mut dep = base;
    let mut j = start;
    while j < end {
        if let Some(k) = kind_of(&spans[j]) {
            if dep == base && k == kw::PRF_SCRIPT {
                steps.push(j);
            }
            if kw::is_proof_goal(k) || k == kw::PRF_OPEN {
                dep += 1;
            } else if kw::is_proof_close(k) {
                dep -= 1;
            } else if kw::is_qed_global(k) {
                dep = base - 1;
            }
        }
        j += 1;
    }
    steps
}

/// Seeded hash of a statement (XOR the per-run `cut_salt`) → a reproducible pseudo-random
/// value used to pick an apply-script cut point. Deterministic per `(seed, lemma)`.
fn cut_hash(s: &str, salt: u64) -> u64 {
    let mut h = 0xcbf29ce484222325u64 ^ salt;
    for b in s.bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

/* ---------- the walk ---------- */

struct Body {
    end: usize,
    lead: String,
    text: String,
    n_commands: i64,
    is_leaf: bool,
    method: String,
    clean: bool,
}

struct Walker<'a> {
    spans: &'a [Span],
    spec: &'a Spec,
    centrality: &'a dyn Fn(&str) -> i64,
    decide: &'a mut dyn FnMut(usize) -> bool,
    out: String,
    holes: Vec<Hole>,
    matches: Vec<(usize, i64)>,
    // (end offset in `out`, end offset in the original, is_goal, had_sorry)
    top_segs: Vec<(usize, usize, bool, bool)>,
    orig: usize, // chars of the original consumed so far
    last_sorry_end: i64,
    total: i64,
    ablated: i64,
    i: usize,
    depth: i64,
    cut_salt: u64, // seeds the apply-script cut point (reproducible per run)
}

impl<'a> Walker<'a> {
    fn src(&self, j: usize) -> String {
        self.spans[j].source()
    }

    // pure lookahead; does not mutate walker state
    fn measure(&self, start: usize, d: i64) -> Body {
        let n = self.spans.len();
        let mut lead = String::new();
        let mut body = String::new();
        let mut j = start;
        let mut dep = d + 1;
        let mut seen = false;
        let mut clean = true;
        let mut n_cmds = 0i64;
        let mut is_leaf = true;
        let mut method = String::new();
        while j < n && dep > d && clean {
            match kind_of(&self.spans[j]) {
                Some(k) if kw::is_theory(k) => clean = false,
                Some(k) => {
                    seen = true;
                    body.push_str(&self.src(j));
                    n_cmds += 1;
                    if is_ablatable(k) {
                        is_leaf = false;
                    }
                    if method.is_empty() && !is_prefatory(k) {
                        method = classify_method(&self.spans[j]);
                    }
                    if kw::is_proof_goal(k) || k == kw::PRF_OPEN {
                        dep += 1;
                    } else if kw::is_proof_close(k) {
                        dep -= 1;
                    } else if kw::is_qed_global(k) {
                        dep = d;
                    }
                    j += 1;
                }
                None => {
                    if seen {
                        body.push_str(&self.src(j));
                    } else {
                        lead.push_str(&self.src(j));
                    }
                    j += 1;
                }
            }
        }
        Body {
            end: j,
            lead,
            text: body,
            n_commands: n_cmds,
            is_leaf,
            method: if method.is_empty() {
                "?".to_string()
            } else {
                method
            },
            clean: clean && dep == d,
        }
    }

    // emit body verbatim, recursing into nested goals (the "keep" path)
    fn walk_body(&mut self, d: i64) {
        let n = self.spans.len();
        while self.i < n && self.depth > d {
            match kind_of(&self.spans[self.i]) {
                Some(k) if kw::is_theory(k) => self.depth = d,
                Some(k) if is_goal(k) => self.handle_goal(),
                Some(k) => {
                    let s = self.src(self.i);
                    self.out.push_str(&s);
                    if k == kw::PRF_OPEN {
                        self.depth += 1;
                    } else if kw::is_proof_close(k) {
                        self.depth -= 1;
                    } else if kw::is_qed_global(k) {
                        self.depth = d;
                    }
                    self.i += 1;
                }
                None => {
                    let s = self.src(self.i);
                    self.out.push_str(&s);
                    self.i += 1;
                }
            }
        }
    }

    fn handle_goal(&mut self) {
        let d = self.depth;
        let stmt_idx = self.i;
        let k = kind_of(&self.spans[self.i]).unwrap().to_string();
        let name = goal_name(&self.spans[self.i]);
        let stmt_src = self.src(self.i);
        self.out.push_str(&stmt_src);
        self.i += 1;
        self.depth = d + 1;
        let goal_depth = d + 1;
        let m = self.measure(self.i, d);
        let cent = (self.centrality)(&name);

        let candidate = is_ablatable(&k)
            && m.clean
            && goal_depth >= self.spec.min_depth
            && goal_depth <= self.spec.max_depth
            && (!self.spec.leaves_only || m.is_leaf)
            && m.n_commands >= self.spec.min_size
            && m.n_commands <= self.spec.max_size
            && cent >= self.spec.min_centrality
            && cent <= self.spec.max_centrality;

        if candidate {
            self.total += 1;
            self.matches.push((stmt_idx, cent));
            if (self.decide)(stmt_idx) {
                // Apply-script proofs: keep a seeded-random prefix of `apply` steps and
                // admit the rest with `sorry` (a head start for the agent) instead of
                // dropping the whole proof. Other proofs (structured, `by`, single
                // `apply`) keep the whole-proof ` sorry`.
                let steps = apply_steps(self.spans, self.i, m.end, goal_depth);
                let keep_k = if !self.spec.ablate_scripts && steps.len() >= 2 {
                    1 + (cut_hash(&stmt_src, self.cut_salt) % (steps.len() as u64 - 1)) as usize
                } else {
                    0 // --ablate-scripts (or a single step): drop the whole script
                };
                if keep_k > 0 {
                    let prefix: String = (self.i..steps[keep_k - 1] + 1)
                        .map(|j| self.src(j))
                        .collect();
                    self.out.push_str(&prefix);
                }
                self.out.push_str(" sorry");
                self.last_sorry_end = self.out.chars().count() as i64;
                self.ablated += 1;
                self.holes.push(Hole {
                    theorem_name: name,
                    depth: goal_depth,
                    n_commands: m.n_commands,
                    n_lines: n_lines(&m.text),
                    is_leaf: m.is_leaf,
                    centrality: cent,
                    method: m.method,
                    proof_text: m.text,
                });
                self.i = m.end;
                self.depth = d;
            } else {
                self.walk_body(d);
            }
        } else if goal_depth > self.spec.max_depth {
            self.out.push_str(&m.lead);
            self.out.push_str(&m.text);
            self.i = m.end;
            self.depth = d;
        } else {
            self.walk_body(d);
        }
    }

    fn run(&mut self) {
        let n = self.spans.len();
        while self.i < n {
            match kind_of(&self.spans[self.i]) {
                Some(k) if is_goal(k) => {
                    let ablated0 = self.ablated;
                    let start = self.i;
                    self.handle_goal();
                    for j in start..self.i {
                        self.orig += self.spans[j].source().chars().count();
                    }
                    // a goal is never a structural closer
                    self.top_segs.push((
                        self.out.chars().count(),
                        self.orig,
                        false,
                        self.ablated > ablated0,
                    ));
                }
                _ => {
                    // keep `end` closers AND block openers (`context`/`locale`/… , kind
                    // thy_decl_block, which absorb their `begin`) so shrink can't drop an
                    // opener while keeping its `end` -> unbalanced theory.
                    let closer = self.spans[self.i].name() == "end"
                        || self.spans[self.i].keyword_kind() == Some(kw::THY_DECL_BLOCK);
                    let s = self.src(self.i);
                    self.orig += s.chars().count();
                    self.out.push_str(&s);
                    self.i += 1;
                    self.top_segs
                        .push((self.out.chars().count(), self.orig, closer, false));
                }
            }
        }
    }
}

fn walk_all(
    spans: &[Span],
    spec: &Spec,
    centrality: &dyn Fn(&str) -> i64,
    decide: &mut dyn FnMut(usize) -> bool,
    cut_salt: u64,
) -> (AblationResult, Vec<(usize, i64)>) {
    let mut w = Walker {
        spans,
        spec,
        centrality,
        decide,
        out: String::new(),
        holes: Vec::new(),
        matches: Vec::new(),
        top_segs: Vec::new(),
        orig: 0,
        last_sorry_end: -1,
        total: 0,
        ablated: 0,
        i: 0,
        depth: 0,
        cut_salt,
    };
    w.run();

    let full = w.out;
    let original: String = spans.iter().map(|s| s.source()).collect();
    // each top segment carries both its challenge-offset and its original-offset,
    // so the challenge and the solution can be shrunk independently.
    let chal_segs: Vec<(usize, bool, bool)> =
        w.top_segs.iter().map(|(c, _, g, a)| (*c, *g, *a)).collect();
    let sol_segs: Vec<(usize, bool, bool)> =
        w.top_segs.iter().map(|(_, o, g, a)| (*o, *g, *a)).collect();
    let text = if spec.truncate && w.last_sorry_end >= 0 {
        full.chars().take(w.last_sorry_end as usize).collect()
    } else if spec.shrink_challenge {
        shrink(&full, &chal_segs, spec.count.map(|c| c as usize))
    } else {
        full
    };
    let solution = if spec.shrink_solution {
        shrink(&original, &sol_segs, spec.count.map(|c| c as usize))
    } else {
        original
    };

    (
        AblationResult {
            text,
            solution,
            total: w.total,
            ablated: w.ablated,
            holes: w.holes,
            deleted: Vec::new(),
        },
        w.matches,
    )
}

/// Minimal dependency-closed slice for `--shrink-*-minimal`. Mirrors the rocq
/// `slice_delete`: keep the (first `count`) holes plus the transitive closure of the
/// goal-decls their statements reference; all non-goal items are kept as glue. The
/// challenge excludes the deleted lemma(s) and re-holes any kept goal still citing a
/// deleted name; the solution keeps everything real (pulling the deleted lemma + deps
/// back in). Purely syntactic.
fn slice_delete(
    spans: &[Span],
    spec: &Spec,
    by_lemma: &std::collections::HashMap<usize, &crate::uses::Lemma>,
    del: &std::collections::HashSet<usize>,
    users: &std::collections::HashSet<usize>,
    solution: bool,
) -> String {
    use std::collections::{HashSet, VecDeque};
    let n = spans.len();
    let src_range =
        |lo: usize, hi: usize| -> String { spans[lo..hi].iter().map(|s| s.source()).collect() };
    // name -> ALL openers that define it. A name may be declared more than once (e.g.
    // the same lemma name inside different locales/contexts); keeping only one — worse,
    // an arbitrary map entry — can drop the definition a kept reference resolves to,
    // leaving a dangling reference in the slice. (Mirrors rocq `openers_of_name`.)
    let mut openers_of_name: std::collections::HashMap<&str, Vec<usize>> =
        std::collections::HashMap::new();
    for l in by_lemma.values() {
        openers_of_name.entry(l.name.as_str()).or_default().push(l.opener);
    }
    let deleted_names: HashSet<String> = del
        .iter()
        .filter_map(|o| by_lemma.get(o).map(|l| l.name.clone()))
        .collect();
    let must_hole = |o: usize| -> bool {
        by_lemma.get(&o).is_some_and(|l| {
            l.body_names
                .iter()
                .any(|nm| deleted_names.contains(nm.as_str()))
        })
    };
    let mut seed: Vec<usize> = users.iter().copied().collect();
    seed.sort_unstable();
    if let Some(k) = spec.count {
        seed.truncate(k as usize);
    }
    let mut keep: HashSet<usize> = HashSet::new();
    let mut q: VecDeque<usize> = VecDeque::new();
    // Keep-set computed BEFORE ablating, shared by challenge & solution: the full
    // statement+body closure of the target holes over the original. Keeps every
    // lemma the real proofs need (so we never throw away more than the deleted
    // lemma) and gives both sides the same context window. The deleted lemma stays
    // in the set (restored in the solution, omitted from the challenge).
    for o in seed {
        if !keep.contains(&o) && by_lemma.contains_key(&o) {
            keep.insert(o);
            q.push_back(o);
        }
    }
    // Structural (non-goal) items are always kept, so any in-file lemma they cite must be
    // kept too — else it dangles (e.g. `lemmas foo = bar [OF refl]` needs `bar`). Seed the
    // closure with those references. (Mirrors the rocq ablator's non-goal seeding loop.)
    for sp in spans.iter() {
        let is_goal = sp.keyword_kind().map(kw::is_theory_goal).unwrap_or(false);
        if is_goal {
            continue;
        }
        for nm in crate::uses::names_in(&sp.content) {
            if let Some(os) = openers_of_name.get(nm.as_str()) {
                for &o2 in os {
                    if !keep.contains(&o2) && by_lemma.contains_key(&o2) {
                        keep.insert(o2);
                        q.push_back(o2);
                    }
                }
            }
        }
    }
    while let Some(o) = q.pop_front() {
        let l = by_lemma[&o];
        for nm in l.stmt_names.iter().chain(l.body_names.iter()) {
            if let Some(os) = openers_of_name.get(nm.as_str()) {
                for &o2 in os {
                    if !keep.contains(&o2) && by_lemma.contains_key(&o2) {
                        keep.insert(o2);
                        q.push_back(o2);
                    }
                }
            }
        }
    }
    let mut buf = String::new();
    let mut i = 0;
    while i < n {
        let is_goal = spans[i]
            .keyword_kind()
            .map(kw::is_theory_goal)
            .unwrap_or(false);
        if is_goal {
            let e = by_lemma.get(&i).map(|l| l.block_end).unwrap_or(i + 1);
            if keep.contains(&i) {
                if !solution && del.contains(&i) {
                    // deleted lemma: omitted from the challenge
                } else if !solution && (must_hole(i) || users.contains(&i)) {
                    buf.push_str(&render_user(
                        spans,
                        i,
                        e,
                        &deleted_names,
                        spec.delete_leaves,
                        spec.ablate_scripts,
                    ));
                } else {
                    buf.push_str(&src_range(i, e));
                }
            }
            i = e;
        } else {
            // Structural item (e.g. a `lemmas foo = bar` bundle, `declare`, notation).
            // In the challenge the deleted lemma is gone, so a structural item that cites
            // it would dangle (`Undefined fact "…"`); drop it. The solution restores the
            // lemma, so keep it there. Mirrors the rocq ablator's `struct_ok`.
            let cites_deleted = !solution
                && crate::uses::names_in(&spans[i].content)
                    .iter()
                    .any(|nm| deleted_names.contains(nm.as_str()));
            if !cites_deleted {
                buf.push_str(&spans[i].source());
            }
            i += 1;
        }
    }
    collapse_blank_lines(&buf)
}

// --- leaf-level user ablation (for --delete-lemmas-leaves) ----------------------
// Hole the *smallest enclosing* structured step (have/show/…) that cites a deleted
// name at its own level, keeping the surrounding skeleton; fall back to whole-proof
// when a deleted name is cited at the user's top proof level. Boundaries mirror the
// tested `measure` depth logic (qed vs nested proof_close), and the result is
// verified by `--check-build`.

/// End index (exclusive) of the proof region opened at `start`, whose enclosing unit
/// was measured at depth `d` — same logic as `WalkState::measure`.
fn measure_end(spans: &[Span], start: usize, d: i64) -> usize {
    let n = spans.len();
    let mut j = start;
    let mut dep = d + 1;
    while j < n && dep > d {
        match kind_of(&spans[j]) {
            Some(k) if kw::is_theory(k) => break,
            Some(k) => {
                if kw::is_proof_goal(k) || k == kw::PRF_OPEN {
                    dep += 1;
                } else if kw::is_proof_close(k) {
                    dep -= 1;
                } else if kw::is_qed_global(k) {
                    dep = d;
                }
                j += 1;
            }
            None => j += 1,
        }
    }
    j
}

fn span_cites(span: &Span, deleted: &std::collections::HashSet<String>) -> bool {
    crate::uses::names_in(&span.content)
        .iter()
        .any(|nm| deleted.contains(nm))
}

/// Does `[lo,hi)` cite a deleted name at its OWN level (outside nested goal-units)?
fn own_cites(
    spans: &[Span],
    lo: usize,
    hi: usize,
    d: i64,
    deleted: &std::collections::HashSet<String>,
) -> bool {
    let mut j = lo;
    while j < hi {
        match kind_of(&spans[j]) {
            Some(k) if kw::is_proof_goal(k) => j = measure_end(spans, j + 1, d + 1),
            _ => {
                if span_cites(&spans[j], deleted) {
                    return true;
                }
                j += 1;
            }
        }
    }
    false
}

/// Does `[lo,hi)` cite a deleted name anywhere (including nested units)?
fn subtree_cites(
    spans: &[Span],
    lo: usize,
    hi: usize,
    deleted: &std::collections::HashSet<String>,
) -> bool {
    (lo..hi).any(|j| span_cites(&spans[j], deleted))
}

/// Index of the first OWN-level (depth `d`) span in `[lo,hi)` that cites a deleted name,
/// skipping over nested goal-units (mirrors `own_cites`). Used to cut an apply-script at
/// the step that uses a deleted lemma.
fn first_citing_toplevel(
    spans: &[Span],
    lo: usize,
    hi: usize,
    d: i64,
    deleted: &std::collections::HashSet<String>,
) -> Option<usize> {
    let mut j = lo;
    while j < hi {
        match kind_of(&spans[j]) {
            Some(k) if kw::is_proof_goal(k) => j = measure_end(spans, j + 1, d + 1),
            _ => {
                if span_cites(&spans[j], deleted) {
                    return Some(j);
                }
                j += 1;
            }
        }
    }
    None
}

/// Render proof body `[lo,hi)` (whose enclosing unit measured at depth `d`), holing
/// the innermost units that own-level-cite a deleted name. Returns (text, all_covered):
/// `all_covered=false` means a deleted name survives at this level (caller whole-proofs).
fn leaf_render(
    spans: &[Span],
    lo: usize,
    hi: usize,
    d: i64,
    deleted: &std::collections::HashSet<String>,
) -> (String, bool) {
    let mut out = String::new();
    let mut ok = true;
    let mut j = lo;
    while j < hi {
        match kind_of(&spans[j]) {
            Some(k) if kw::is_proof_goal(k) => {
                let ue = measure_end(spans, j + 1, d + 1);
                if subtree_cites(spans, j, ue, deleted) {
                    if own_cites(spans, j + 1, ue, d + 1, deleted) {
                        out.push_str(&spans[j].source()); // the have/show statement
                        out.push_str(" sorry");
                    } else {
                        out.push_str(&spans[j].source());
                        let (sub, sub_ok) = leaf_render(spans, j + 1, ue, d + 1, deleted);
                        out.push_str(&sub);
                        ok &= sub_ok;
                    }
                } else {
                    out.push_str(&spans[j..ue].iter().map(|s| s.source()).collect::<String>());
                }
                j = ue;
            }
            _ => {
                if span_cites(&spans[j], deleted) {
                    ok = false; // a top-level citation here can't be leaf-isolated
                }
                out.push_str(&spans[j].source());
                j += 1;
            }
        }
    }
    (out, ok)
}

/// Render one holed user. `--delete-lemmas-leaves` holes the smallest enclosing steps
/// citing a deleted name (whole-proof fallback); otherwise whole-proof (`stmt + sorry`).
fn render_user(
    spans: &[Span],
    opener: usize,
    end: usize,
    deleted: &std::collections::HashSet<String>,
    leaves: bool,
    ablate_scripts: bool,
) -> String {
    if leaves {
        let (body, ok) = leaf_render(spans, opener + 1, end, 0, deleted);
        if ok {
            return format!("{}{}", spans[opener].source(), body);
        }
        // Apply-script: a top-level step (not a nested have/show) cites the deleted name.
        // Cut at the first such step, keeping the citation-free prefix + `sorry`
        // (unless --ablate-scripts, which drops the whole script below).
        if !ablate_scripts {
            if let Some(cut) = first_citing_toplevel(spans, opener + 1, end, 0, deleted) {
                if !subtree_cites(spans, opener + 1, cut, deleted) {
                    let prefix: String =
                        spans[opener + 1..cut].iter().map(|s| s.source()).collect();
                    return format!("{}{} sorry", spans[opener].source(), prefix);
                }
            }
        }
    }
    format!("{} sorry", spans[opener].source())
}

/// --delete-lemmas: delete eligible used lemmas + whole-proof-ablate their users.
/// Position in `lemmas` of the lemma whose span opener is `opener`, or `usize::MAX`.
fn u_pos(lemmas: &[crate::uses::Lemma], opener: usize) -> usize {
    lemmas
        .iter()
        .position(|l| l.opener == opener)
        .unwrap_or(usize::MAX)
}

/// Corollary selection: pick a random theorem, take its transitive in-file dependency
/// closure, and draw deletions from the *eligible* members of that closure (fan-in
/// weighted, or uniform). One corollary is exhausted before a fresh one is drawn, so
/// deletions stay concentrated in a single proof's subtree unless the target forces
/// spilling. Returns positions (in `lemmas`) of the lemmas to delete.
fn corollary_select(
    lemmas: &[crate::uses::Lemma],
    cand_pos: &std::collections::HashSet<usize>,
    spec: &Spec,
    rng: &mut Rng,
) -> Vec<usize> {
    use std::collections::{HashMap, HashSet};
    if cand_pos.is_empty() {
        return Vec::new();
    }
    // name -> first lemma position, for resolving citation edges to in-file lemmas.
    let mut idx_of: HashMap<&str, usize> = HashMap::new();
    for (p, l) in lemmas.iter().enumerate() {
        idx_of.entry(l.name.as_str()).or_insert(p);
    }
    let deps_of = |p: usize| -> Vec<usize> {
        let l = &lemmas[p];
        let mut s: Vec<usize> = l
            .stmt_names
            .iter()
            .chain(l.body_names.iter())
            .filter_map(|nm| idx_of.get(nm.as_str()).copied())
            .filter(|&q| q != p)
            .collect();
        s.sort_unstable();
        s.dedup();
        s
    };
    // eligible candidates in `corollary`'s transitive dependency closure (excl. itself).
    let closure_cands = |start: usize| -> Vec<usize> {
        let mut seen: HashSet<usize> = HashSet::new();
        let mut stack = deps_of(start);
        while let Some(p) = stack.pop() {
            if seen.insert(p) {
                for q in deps_of(p) {
                    if !seen.contains(&q) {
                        stack.push(q);
                    }
                }
            }
        }
        seen.remove(&start);
        let mut v: Vec<usize> = seen.into_iter().filter(|p| cand_pos.contains(p)).collect();
        v.sort_unstable();
        v
    };
    let weight = |p: usize| -> f64 {
        if spec.delete_uniform {
            1.0
        } else {
            (lemmas[p].users.len() as f64).max(1.0)
        }
    };
    let pick = |pool: &[usize], rng: &mut Rng| -> usize {
        let total: f64 = pool.iter().map(|&p| weight(p)).sum();
        let r = rng.next_f64() * total;
        let mut acc = 0.0;
        for (j, &p) in pool.iter().enumerate() {
            acc += weight(p);
            if acc > r {
                return j;
            }
        }
        pool.len() - 1
    };

    let mut order: Vec<usize> = (0..lemmas.len()).collect();
    rng.shuffle(&mut order);

    let mut del: HashSet<usize> = HashSet::new();
    let mut chosen: Vec<usize> = Vec::new();
    let target_deletions: Option<usize> = spec.delete_count.map(|n| n as usize);
    let target_ablations: Option<usize> = if spec.delete_count.is_none() {
        spec.count.map(|c| c as usize)
    } else {
        None
    };
    let use_prob = spec.delete_count.is_none() && spec.count.is_none();

    let covered = |del: &HashSet<usize>, chosen: &[usize]| -> usize {
        let mut s: HashSet<usize> = HashSet::new();
        for &cp in chosen {
            for &u in &lemmas[cp].users {
                let up = u_pos(lemmas, u);
                if up == usize::MAX || !del.contains(&up) {
                    s.insert(u);
                }
            }
        }
        s.len()
    };
    let reached = |del: &HashSet<usize>, chosen: &[usize]| -> bool {
        match (target_deletions, target_ablations) {
            (Some(nd), _) => chosen.len() >= nd,
            (_, Some(k)) => covered(del, chosen) >= k,
            _ => false,
        }
    };

    for &cor in &order {
        if !use_prob && reached(&del, &chosen) {
            break;
        }
        let mut pool: Vec<usize> = closure_cands(cor)
            .into_iter()
            .filter(|p| !del.contains(p))
            .collect();
        if pool.is_empty() {
            continue;
        }
        if use_prob {
            // no count/=N target: a per-candidate coin over one corollary's closure.
            for &pp in &pool {
                if rng.next_f64() < spec.prob {
                    del.insert(pp);
                    chosen.push(pp);
                }
            }
            break;
        }
        while !pool.is_empty() && !reached(&del, &chosen) {
            let j = pick(&pool, rng);
            let cp = pool.remove(j);
            del.insert(cp);
            chosen.push(cp);
        }
    }
    chosen
}

fn ablate_delete(spans: &[Span], spec: &Spec, rng: &mut Rng) -> AblationResult {
    use std::collections::{HashMap, HashSet};
    let lemmas = crate::uses::analyze(spans, spec.aggressive);
    let total_eligible = lemmas.iter().filter(|l| l.eligible).count() as i64;
    let nusers = |l: &crate::uses::Lemma| l.users.len() as i64;
    let cands: Vec<&crate::uses::Lemma> = lemmas
        .iter()
        .filter(|l| {
            l.eligible && nusers(l) >= spec.min_centrality && nusers(l) <= spec.max_centrality
        })
        .collect();
    // `--count k` is a target number of *ablations* (holed users), not deletions:
    // deleting a lemma forces every one of its users to be ablated, so ablations
    // arrive in chunks. We draw deletions at random *without replacement*, each with
    // probability proportional to its weight (its user count — or 1 under
    // `delete_uniform`), accumulating their forced ablations until the distinct total
    // reaches >= k, then stop. Seed-driven (diverse evals), favours popular lemmas,
    // yet keeps a non-zero chance on the tail. Without --count the per-lemma `prob`
    // coin decides. (With --truncate we later keep only the first k.)
    let weight = |l: &crate::uses::Lemma| -> f64 {
        if spec.delete_uniform {
            1.0
        } else {
            l.users.len() as f64
        }
    };
    // index of one weighted pick (proportional to `weight`) from a non-empty list
    let pick_idx = |remaining: &[&crate::uses::Lemma], rng: &mut Rng| -> usize {
        let total: f64 = remaining.iter().map(|l| weight(l)).sum();
        let r = rng.next_f64() * total;
        let mut acc = 0.0;
        let mut idx = remaining.len() - 1;
        for (j, l) in remaining.iter().enumerate() {
            acc += weight(l);
            if acc > r {
                idx = j;
                break;
            }
        }
        idx
    };
    // Corollary mode restricts the candidate pool to one random theorem's dependency
    // closure (see corollary_select); everything downstream (user ablation, shrink,
    // --count/--truncate) is shared with plain --delete-lemmas.
    let selected: Vec<&crate::uses::Lemma> = if spec.corollary {
        let cand_pos: std::collections::HashSet<usize> = {
            let opener_pos: HashMap<usize, usize> = lemmas
                .iter()
                .enumerate()
                .map(|(p, l)| (l.opener, p))
                .collect();
            cands
                .iter()
                .filter_map(|c| opener_pos.get(&c.opener).copied())
                .collect()
        };
        corollary_select(&lemmas, &cand_pos, spec, rng)
            .into_iter()
            .map(|p| &lemmas[p])
            .collect()
    } else {
        match spec.delete_count {
            // --delete-lemmas N: delete exactly N lemmas (weighted draw), any ablation count
            Some(kd) => {
                let target = (kd as usize).min(cands.len());
                let mut chosen: Vec<&crate::uses::Lemma> = Vec::new();
                let mut remaining: Vec<&crate::uses::Lemma> = cands.clone();
                while chosen.len() < target && !remaining.is_empty() {
                    let idx = pick_idx(&remaining, rng);
                    chosen.push(remaining.remove(idx));
                }
                chosen
            }
            None => match spec.count {
                None => {
                    let p = spec.prob;
                    cands.into_iter().filter(|_| rng.next_f64() < p).collect()
                }
                Some(0) => Vec::new(),
                Some(k) => {
                    let k = k as usize;
                    let mut covered: HashSet<usize> = HashSet::new();
                    let mut chosen: Vec<&crate::uses::Lemma> = Vec::new();
                    let mut remaining: Vec<&crate::uses::Lemma> = cands.clone();
                    while covered.len() < k && !remaining.is_empty() {
                        let idx = pick_idx(&remaining, rng);
                        let l = remaining.remove(idx);
                        covered.extend(l.users.iter().copied());
                        chosen.push(l);
                    }
                    chosen
                }
            },
        }
    };
    let del: HashSet<usize> = selected.iter().map(|l| l.opener).collect();
    let deleted_names: HashSet<String> = selected.iter().map(|l| l.name.clone()).collect();
    // distinct forced ablations (users not themselves deleted), in file order.
    let mut users_sorted: Vec<usize> = {
        let mut s: Vec<usize> = selected
            .iter()
            .flat_map(|l| l.users.iter().copied())
            .filter(|u| !del.contains(u))
            .collect();
        s.sort_unstable();
        s.dedup();
        s
    };
    // with --truncate + --count, ablate EXACTLY the first k and cut the rest;
    // otherwise every user must be ablated (a dangling reference would not compile).
    if spec.truncate {
        if let Some(k) = spec.count {
            users_sorted.truncate(k as usize);
        }
    }
    let users: HashSet<usize> = users_sorted.into_iter().collect();
    let by_lemma: HashMap<usize, &crate::uses::Lemma> =
        lemmas.iter().map(|l| (l.opener, l)).collect();
    let src_range =
        |lo: usize, hi: usize| -> String { spans[lo..hi].iter().map(|s| s.source()).collect() };
    let n = spans.len();

    // Emit the challenge while recording per-item challenge/solution segments (char
    // offsets, is_closer, had_hole) so --shrink-* can trim each side to the first N
    // holes. (--delete-lemmas-leaves: render_user holes the smallest enclosing step
    // citing a deleted name, with whole-proof fallback when a top-level citation can't
    // be leaf-isolated.)
    let mut out = String::new();
    let mut holes = Vec::new();
    let mut deleted = Vec::new();
    let mut ablated = 0i64;
    let mut last_sorry_end: i64 = -1;
    let mut chal_segs: Vec<(usize, bool, bool)> = Vec::new();
    let mut sol_segs: Vec<(usize, bool, bool)> = Vec::new();
    let mut orig_chars = 0usize;
    let mut i = 0;
    while i < n {
        let is_goal = spans[i]
            .keyword_kind()
            .map(kw::is_theory_goal)
            .unwrap_or(false);
        if is_goal {
            let l = by_lemma.get(&i).copied();
            let e = l.map(|l| l.block_end).unwrap_or(i + 1);
            let name = l.map(|l| l.name.clone()).unwrap_or_default();
            let item_src = src_range(i, e);
            let item_chars = item_src.chars().count();
            if del.contains(&i) {
                deleted.push((name, item_src));
                orig_chars += item_chars;
                sol_segs.push((orig_chars, false, false)); // solution only
            } else if users.contains(&i) {
                out.push_str(&render_user(
                    spans,
                    i,
                    e,
                    &deleted_names,
                    spec.delete_leaves,
                    spec.ablate_scripts,
                ));
                last_sorry_end = out.chars().count() as i64;
                let proof_text = src_range(i + 1, e);
                holes.push(Hole {
                    theorem_name: name,
                    depth: 1,
                    n_commands: 0,
                    n_lines: n_lines(&proof_text),
                    is_leaf: true,
                    centrality: 0,
                    method: "deleted-dep".to_string(),
                    proof_text,
                });
                ablated += 1;
                chal_segs.push((out.chars().count(), false, true));
                orig_chars += item_chars;
                sol_segs.push((orig_chars, false, true));
            } else {
                out.push_str(&item_src);
                chal_segs.push((out.chars().count(), false, false));
                orig_chars += item_chars;
                sol_segs.push((orig_chars, false, false));
            }
            i = e;
        } else {
            let src = spans[i].source();
            // keep `end` closers AND block openers (kind thy_decl_block) so shrink keeps
            // a balanced theory/locale/context skeleton.
            let closer = spans[i].name() == "end"
                || spans[i].keyword_kind() == Some(kw::THY_DECL_BLOCK);
            out.push_str(&src);
            chal_segs.push((out.chars().count(), closer, false));
            orig_chars += src.chars().count();
            sol_segs.push((orig_chars, closer, false));
            i += 1;
        }
    }
    let original: String = spans.iter().map(|s| s.source()).collect();
    let cnt = spec.count.map(|c| c as usize);
    let text = if spec.truncate && last_sorry_end >= 0 {
        collapse_blank_lines(
            &out.chars()
                .take(last_sorry_end as usize)
                .collect::<String>(),
        )
    } else if spec.shrink_challenge_minimal {
        slice_delete(spans, spec, &by_lemma, &del, &users, false)
    } else if spec.shrink_challenge {
        shrink(&out, &chal_segs, cnt)
    } else {
        collapse_blank_lines(&out)
    };
    let solution = if spec.shrink_solution_minimal {
        slice_delete(spans, spec, &by_lemma, &del, &users, true)
    } else if spec.shrink_solution {
        shrink(&original, &sol_segs, cnt)
    } else {
        original
    };
    AblationResult {
        text,
        solution,
        total: total_eligible,
        ablated,
        holes,
        deleted,
    }
}

/// Public entry. `centrality` maps a theorem name to its corpus fan-in (0 if unused).
pub fn ablate(
    spans: &[Span],
    spec: &Spec,
    rng: &mut Rng,
    centrality: &dyn Fn(&str) -> i64,
) -> AblationResult {
    if spec.delete_lemmas {
        return ablate_delete(spans, spec, rng);
    }
    let cut_salt = rng.next_u64();
    match spec.count {
        Some(target) => {
            let mut none = |_idx: usize| false;
            let cands = walk_all(spans, spec, centrality, &mut none, cut_salt).1;
            let selected: std::collections::HashSet<usize> = if cands.len() as u64 <= target {
                cands.iter().map(|(i, _)| *i).collect()
            } else if spec.by_centrality {
                let mut c = cands.clone();
                c.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
                c.into_iter()
                    .take(target as usize)
                    .map(|(i, _)| i)
                    .collect()
            } else {
                let mut idx: Vec<usize> = cands.iter().map(|(i, _)| *i).collect();
                rng.shuffle(&mut idx);
                idx.into_iter().take(target as usize).collect()
            };
            let mut decide = |idx: usize| selected.contains(&idx);
            let (mut r, _) = walk_all(spans, spec, centrality, &mut decide, cut_salt);
            r.total = cands.len() as i64;
            r
        }
        None => {
            let p = spec.prob;
            let mut decide = |_idx: usize| rng.next_f64() < p;
            walk_all(spans, spec, centrality, &mut decide, cut_salt).0
        }
    }
}

// Drop all top-level segments after the last ablated one, keeping structural
// closers (`end`) so the theory/locale/context still closes, then collapse the
// blank-line runs left behind (Ablate.scala `shrink`). The same operation
// shrinks either the challenge or the solution; only the offsets in
// `segs` differ.
// Drop segments after the cut: the last hole, or with `count=Some(n)` the n-th hole
// (so the result keeps exactly the first n holes). Structural closers are kept.
fn shrink(full: &str, segs: &[(usize, bool, bool)], count: Option<usize>) -> String {
    let last = {
        let mut seen = 0usize;
        let mut last: Option<usize> = None;
        for (idx, (_, _closer, had)) in segs.iter().enumerate() {
            if *had {
                seen += 1;
                match count {
                    Some(n) if seen > n => {}
                    _ => last = Some(idx),
                }
            }
        }
        last
    };
    let last = match last {
        Some(l) => l,
        None => return full.to_string(),
    };
    let cs: Vec<char> = full.chars().collect();
    let mut out = String::new();
    let mut prev = 0usize;
    let mut gap = false; // dropped a segment since the last kept one
    for (idx, (end, is_closer, _)) in segs.iter().enumerate() {
        if idx <= last || *is_closer {
            // a kept closer after a gap (e.g. `end`) must not glue onto the
            // previous token — ensure a newline separates them.
            if gap && out.chars().last().is_some_and(|c| c != '\n') {
                out.push('\n');
            }
            out.extend(&cs[prev..*end]);
            gap = false;
        } else {
            gap = true;
        }
        prev = *end;
    }
    collapse_blank_lines(&out)
}

fn collapse_blank_lines(s: &str) -> String {
    let cs: Vec<char> = s.chars().collect();
    let n = cs.len();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < n {
        if cs[i] == '\n' {
            // count following [ \t]*\n groups
            let mut j = i + 1;
            let mut groups = 0;
            loop {
                let mut k = j;
                while k < n && (cs[k] == ' ' || cs[k] == '\t') {
                    k += 1;
                }
                if k < n && cs[k] == '\n' {
                    j = k + 1;
                    groups += 1;
                } else {
                    break;
                }
            }
            if groups >= 2 {
                out.push('\n');
                out.push('\n');
                i = j;
            } else {
                out.push('\n');
                i += 1;
            }
        } else {
            out.push(cs[i]);
            i += 1;
        }
    }
    out
}

/* ---------- difficulty preset ladder ---------- */

pub struct Preset {
    pub prob: f64,
    pub min_depth: i64,
    pub max_depth: i64,
    pub leaves_only: bool,
}

pub const LADDER: [Preset; 5] = [
    Preset {
        prob: 0.3,
        min_depth: 1,
        max_depth: INF,
        leaves_only: true,
    },
    Preset {
        prob: 1.0,
        min_depth: 1,
        max_depth: INF,
        leaves_only: true,
    },
    Preset {
        prob: 1.0,
        min_depth: 2,
        max_depth: INF,
        leaves_only: false,
    },
    Preset {
        prob: 0.5,
        min_depth: 1,
        max_depth: 1,
        leaves_only: false,
    },
    Preset {
        prob: 1.0,
        min_depth: 1,
        max_depth: 1,
        leaves_only: false,
    },
];

pub fn preset_of(s: &str) -> Option<&'static Preset> {
    let t = s.to_lowercase();
    let t = t.strip_prefix('l').unwrap_or(&t);
    t.parse::<usize>()
        .ok()
        .filter(|i| *i < LADDER.len())
        .map(|i| &LADDER[i])
}
