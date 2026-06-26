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
    pub delete_uniform: bool,
    pub delete_leaves: bool,
    pub aggressive: bool,
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
            delete_uniform: false,
            delete_leaves: false,
            aggressive: false,
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
    let rest: &[&Token] = if after.first().map(|t| t.is_keyword_str("(")).unwrap_or(false) {
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
            method: if method.is_empty() { "?".to_string() } else { method },
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
                    let closer = self.spans[self.i].name() == "end"; // closes theory/locale/context
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
        AblationResult { text, solution, total: w.total, ablated: w.ablated, holes: w.holes, deleted: Vec::new() },
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
    let src_range = |lo: usize, hi: usize| -> String { spans[lo..hi].iter().map(|s| s.source()).collect() };
    let mut opener_of_name: std::collections::HashMap<&str, usize> = std::collections::HashMap::new();
    for l in by_lemma.values() {
        opener_of_name.entry(l.name.as_str()).or_insert(l.opener);
    }
    let deleted_names: HashSet<&str> =
        del.iter().filter_map(|o| by_lemma.get(o).map(|l| l.name.as_str())).collect();
    let must_hole = |o: usize| -> bool {
        by_lemma.get(&o).is_some_and(|l| l.body_names.iter().any(|nm| deleted_names.contains(nm.as_str())))
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
    while let Some(o) = q.pop_front() {
        let l = by_lemma[&o];
        for nm in l.stmt_names.iter().chain(l.body_names.iter()) {
            if let Some(&o2) = opener_of_name.get(nm.as_str()) {
                if !keep.contains(&o2) && by_lemma.contains_key(&o2) {
                    keep.insert(o2);
                    q.push_back(o2);
                }
            }
        }
    }
    let mut buf = String::new();
    let mut i = 0;
    while i < n {
        let is_goal = spans[i].keyword_kind().map(kw::is_theory_goal).unwrap_or(false);
        if is_goal {
            let e = by_lemma.get(&i).map(|l| l.block_end).unwrap_or(i + 1);
            if keep.contains(&i) {
                if !solution && del.contains(&i) {
                    // deleted lemma: omitted from the challenge
                } else if !solution && (must_hole(i) || users.contains(&i)) {
                    buf.push_str(&spans[i].source());
                    buf.push_str(" sorry");
                } else {
                    buf.push_str(&src_range(i, e));
                }
            }
            i = e;
        } else {
            buf.push_str(&spans[i].source());
            i += 1;
        }
    }
    collapse_blank_lines(&buf)
}

/// --delete-lemmas: delete eligible used lemmas + whole-proof-ablate their users.
fn ablate_delete(spans: &[Span], spec: &Spec, rng: &mut Rng) -> AblationResult {
    use std::collections::{HashMap, HashSet};
    let lemmas = crate::uses::analyze(spans, spec.aggressive);
    let total_eligible = lemmas.iter().filter(|l| l.eligible).count() as i64;
    let nusers = |l: &crate::uses::Lemma| l.users.len() as i64;
    let cands: Vec<&crate::uses::Lemma> = lemmas
        .iter()
        .filter(|l| l.eligible && nusers(l) >= spec.min_centrality && nusers(l) <= spec.max_centrality)
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
        if spec.delete_uniform { 1.0 } else { l.users.len() as f64 }
    };
    let selected: Vec<&crate::uses::Lemma> = match spec.count {
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
                let total: f64 = remaining.iter().map(|l| weight(l)).sum();
                let r = rng.next_f64() * total;
                // pick the lemma whose cumulative weight first exceeds r
                let mut acc = 0.0;
                let mut idx = remaining.len() - 1;
                for (j, l) in remaining.iter().enumerate() {
                    acc += weight(l);
                    if acc > r {
                        idx = j;
                        break;
                    }
                }
                let l = remaining.remove(idx);
                covered.extend(l.users.iter().copied());
                chosen.push(l);
            }
            chosen
        }
    };
    let del: HashSet<usize> = selected.iter().map(|l| l.opener).collect();
    // distinct forced ablations (users not themselves deleted), in file order.
    let mut users_sorted: Vec<usize> = {
        let mut s: Vec<usize> =
            selected.iter().flat_map(|l| l.users.iter().copied()).filter(|u| !del.contains(u)).collect();
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
    let by_lemma: HashMap<usize, &crate::uses::Lemma> = lemmas.iter().map(|l| (l.opener, l)).collect();
    let src_range = |lo: usize, hi: usize| -> String { spans[lo..hi].iter().map(|s| s.source()).collect() };
    let n = spans.len();

    // Emit the challenge while recording per-item challenge/solution segments (char
    // offsets, is_closer, had_hole) so --shrink-* can trim each side to the first N
    // holes. (--delete-lemmas-leaves: leaf-level holing for Isabelle's structured
    // proofs is deferred to the heavyweight semantic ablator; here it falls back to
    // whole-proof ablation, which is always correct.)
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
        let is_goal = spans[i].keyword_kind().map(kw::is_theory_goal).unwrap_or(false);
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
                out.push_str(&spans[i].source()); // statement
                out.push_str(" sorry");
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
            let closer = spans[i].name() == "end";
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
        collapse_blank_lines(&out.chars().take(last_sorry_end as usize).collect::<String>())
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
    AblationResult { text, solution, total: total_eligible, ablated, holes, deleted }
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
    match spec.count {
        Some(target) => {
            let mut none = |_idx: usize| false;
            let cands = walk_all(spans, spec, centrality, &mut none).1;
            let selected: std::collections::HashSet<usize> = if cands.len() as u64 <= target {
                cands.iter().map(|(i, _)| *i).collect()
            } else if spec.by_centrality {
                let mut c = cands.clone();
                c.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
                c.into_iter().take(target as usize).map(|(i, _)| i).collect()
            } else {
                let mut idx: Vec<usize> = cands.iter().map(|(i, _)| *i).collect();
                rng.shuffle(&mut idx);
                idx.into_iter().take(target as usize).collect()
            };
            let mut decide = |idx: usize| selected.contains(&idx);
            let (mut r, _) = walk_all(spans, spec, centrality, &mut decide);
            r.total = cands.len() as i64;
            r
        }
        None => {
            let p = spec.prob;
            let mut decide = |_idx: usize| rng.next_f64() < p;
            walk_all(spans, spec, centrality, &mut decide).0
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
    Preset { prob: 0.3, min_depth: 1, max_depth: INF, leaves_only: true },
    Preset { prob: 1.0, min_depth: 1, max_depth: INF, leaves_only: true },
    Preset { prob: 1.0, min_depth: 2, max_depth: INF, leaves_only: false },
    Preset { prob: 0.5, min_depth: 1, max_depth: 1, leaves_only: false },
    Preset { prob: 1.0, min_depth: 1, max_depth: 1, leaves_only: false },
];

pub fn preset_of(s: &str) -> Option<&'static Preset> {
    let t = s.to_lowercase();
    let t = t.strip_prefix('l').unwrap_or(&t);
    t.parse::<usize>().ok().filter(|i| *i < LADDER.len()).map(|i| &LADDER[i])
}
