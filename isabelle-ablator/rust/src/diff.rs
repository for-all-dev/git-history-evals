//! A tiny self-rolled line differ (no dependency, so it compiles into the WASM
//! core). We store `solution_diff` — a unified diff turning the challenge into
//! the solution — instead of the whole solution file, which is huge for big
//! theories (l4v: 100k+ chars; see issue #107).
//!
//! Convention: lines are `split('\n')` (a trailing newline yields a final ""
//! element); `apply` joins them back with '\n', so the round-trip is byte-exact.
//! Hunks are `@@ -oldStart,oldLen +newStart,newLen @@` with ` `/`-`/`+`
//! prefixes; an empty diff means challenge = solution.
//!
//! Algorithm: Myers O(ND) greedy diff capped at `D_MAX` edits; beyond the cap we
//! fall back to a single hunk replacing the differing middle.

const D_MAX: usize = 2000;
const CONTEXT: usize = 3;

enum Op<'a> {
    Eq(&'a str),
    Del(&'a str),
    Ins(&'a str),
}

fn myers<'a>(a: &[&'a str], b: &[&'a str]) -> Option<Vec<Op<'a>>> {
    let n = a.len() as i64;
    let m = b.len() as i64;
    let max_d = (n + m) as usize;
    if max_d == 0 {
        return Some(Vec::new());
    }
    let off = max_d as i64;
    let mut v = vec![0i64; 2 * max_d + 1];
    let mut trace: Vec<Vec<i64>> = Vec::new();
    let idx = |k: i64| (off + k) as usize;
    let mut found_d: Option<i64> = None;
    let mut d = 0i64;
    'outer: while d <= max_d as i64 && d <= D_MAX as i64 {
        trace.push(v.clone());
        let mut k = -d;
        while k <= d {
            let mut x = if k == -d || (k != d && v[idx(k - 1)] < v[idx(k + 1)]) {
                v[idx(k + 1)]
            } else {
                v[idx(k - 1)] + 1
            };
            let mut y = x - k;
            while x < n && y < m && a[x as usize] == b[y as usize] {
                x += 1;
                y += 1;
            }
            v[idx(k)] = x;
            if x >= n && y >= m {
                found_d = Some(d);
                break 'outer;
            }
            k += 2;
        }
        d += 1;
    }
    let mut dd = found_d?;
    let mut ops: Vec<Op<'a>> = Vec::new();
    let mut x = n;
    let mut y = m;
    while dd >= 0 {
        let vprev = &trace[dd as usize];
        let k = x - y;
        let prev_k = if k == -dd || (k != dd && vprev[idx(k - 1)] < vprev[idx(k + 1)]) {
            k + 1
        } else {
            k - 1
        };
        let prev_x = vprev[idx(prev_k)];
        let prev_y = prev_x - prev_k;
        while x > prev_x && y > prev_y {
            ops.push(Op::Eq(a[(x - 1) as usize]));
            x -= 1;
            y -= 1;
        }
        if dd > 0 {
            if x == prev_x {
                ops.push(Op::Ins(b[(y - 1) as usize]));
                y -= 1;
            } else {
                ops.push(Op::Del(a[(x - 1) as usize]));
                x -= 1;
            }
        }
        dd -= 1;
    }
    ops.reverse();
    Some(ops)
}

fn is_change(o: &Op) -> bool {
    !matches!(o, Op::Eq(_))
}

fn format_hunks(ops: &[Op]) -> String {
    let len = ops.len();
    // old/new line number (1-based) of ops[i]'s first consumed line
    let mut old_no = vec![1i64; len + 1];
    let mut new_no = vec![1i64; len + 1];
    for i in 0..len {
        let (o, nw) = match ops[i] {
            Op::Eq(_) => (1, 1),
            Op::Del(_) => (1, 0),
            Op::Ins(_) => (0, 1),
        };
        old_no[i + 1] = old_no[i] + o;
        new_no[i + 1] = new_no[i] + nw;
    }
    let mut out = String::new();
    let mut i = 0usize;
    while i < len {
        if is_change(&ops[i]) {
            let mut s = i;
            let mut back = CONTEXT;
            while s > 0 && !is_change(&ops[s - 1]) && back > 0 {
                s -= 1;
                back -= 1;
            }
            let mut e = i;
            let mut last_change = i;
            loop {
                if e + 1 >= len {
                    break;
                } else if is_change(&ops[e + 1]) {
                    e += 1;
                    last_change = e;
                } else {
                    let mut run = 0usize;
                    while e + 1 + run < len && !is_change(&ops[e + 1 + run]) {
                        run += 1;
                    }
                    let next_change = e + 1 + run;
                    if next_change < len && run <= 2 * CONTEXT {
                        e = next_change;
                        last_change = next_change;
                    } else {
                        break;
                    }
                }
            }
            let mut stop = last_change;
            let mut fwd = CONTEXT;
            while stop + 1 < len && !is_change(&ops[stop + 1]) && fwd > 0 {
                stop += 1;
                fwd -= 1;
            }
            let ol = old_no[stop + 1] - old_no[s];
            let nl = new_no[stop + 1] - new_no[s];
            out.push_str(&format!(
                "@@ -{},{} +{},{} @@\n",
                old_no[s], ol, new_no[s], nl
            ));
            for op in &ops[s..=stop] {
                match op {
                    Op::Eq(l) => {
                        out.push(' ');
                        out.push_str(l);
                    }
                    Op::Del(l) => {
                        out.push('-');
                        out.push_str(l);
                    }
                    Op::Ins(l) => {
                        out.push('+');
                        out.push_str(l);
                    }
                }
                out.push('\n');
            }
            i = stop + 1;
        } else {
            i += 1;
        }
    }
    out
}

fn fallback(a: &[&str], b: &[&str]) -> String {
    let n = a.len();
    let m = b.len();
    let mut p = 0;
    while p < n && p < m && a[p] == b[p] {
        p += 1;
    }
    let mut s = 0;
    while s < n - p && s < m - p && a[n - 1 - s] == b[m - 1 - s] {
        s += 1;
    }
    let ol = n - p - s;
    let nl = m - p - s;
    if ol == 0 && nl == 0 {
        return String::new();
    }
    let mut out = String::new();
    out.push_str(&format!("@@ -{},{} +{},{} @@\n", p + 1, ol, p + 1, nl));
    for line in &a[p..n - s] {
        out.push('-');
        out.push_str(line);
        out.push('\n');
    }
    for line in &b[p..m - s] {
        out.push('+');
        out.push_str(line);
        out.push('\n');
    }
    out
}

/// Unified diff turning `challenge` into `solution`; "" if identical.
pub fn unified(challenge: &str, solution: &str) -> String {
    if challenge == solution {
        return String::new();
    }
    let a: Vec<&str> = challenge.split('\n').collect();
    let b: Vec<&str> = solution.split('\n').collect();
    match myers(&a, &b) {
        Some(ops) => format_hunks(&ops),
        None => fallback(&a, &b),
    }
}

/// Apply a `unified` diff to `challenge`, recovering the solution.
pub fn apply(challenge: &str, diff: &str) -> String {
    if diff.is_empty() {
        return challenge.to_string();
    }
    let a: Vec<&str> = challenge.split('\n').collect();
    let mut out: Vec<String> = Vec::new();
    let mut oi = 0usize;
    for line in diff.split('\n') {
        if line.starts_with("@@") {
            // "@@ -os,ol +ns,nl @@" — copy unchanged lines up to os
            let after_dash = &line[line.find('-').unwrap() + 1..];
            let os: usize = after_dash[..after_dash.find(',').unwrap()].parse().unwrap();
            while oi < os - 1 {
                out.push(a[oi].to_string());
                oi += 1;
            }
        } else if line.is_empty() {
            // trailing split artifact
        } else {
            match line.as_bytes()[0] {
                b' ' => {
                    out.push(line[1..].to_string());
                    oi += 1;
                }
                b'-' => oi += 1,
                b'+' => out.push(line[1..].to_string()),
                _ => {}
            }
        }
    }
    while oi < a.len() {
        out.push(a[oi].to_string());
        oi += 1;
    }
    out.join("\n")
}
