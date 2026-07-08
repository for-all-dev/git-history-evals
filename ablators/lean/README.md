# lean-ablator

**Syntactic ablation for Lean 4 theories, written in Lean itself.** Parse a
`.lean` file with a self-contained, dependency-free Lean tokenizer and replace
selected proof/definition bodies with `sorry`, preserving everything else
(signatures, comments, whitespace) byte-for-byte. The ablated files are the
*challenge* side of a proof-synthesis training/eval pair; the originals are the
ground truth.

It realises the [seL4-ablate-bench](../../docs/SoW.md) idea — "progressively delete
proofs and see how much an agent can reconstruct," with a slider on how much to
delete — for Lean, with the engine itself programmed in Lean. The design borrows
the proven module layout of the sibling [Isabelle ablator](../isabelle)
(a native Scala tool plus a Rust/WASM port), and records share the field schema
of the git-history datasets in [`../../artifacts`](../../artifacts) (with
`proof_assistant: "lean"`).

## Why a pure-Lean tool (and not `import Lean`)

The natural way to parse Lean is to call Lean's own frontend. We deliberately do
**not**: that needs the full compiler + a pre-built environment (`.olean`s,
often Mathlib), which is heavy and does not cross-compile. Instead the core is a
small, `import Lean`-free library (`Ablator/*`) that:

* tokenizes Lean surface syntax losslessly (every char in exactly one token, so
  concatenation round-trips byte-for-byte), with dedicated scanners for
  comments (`--`, nested `/- -/`, doc `/-- -/`), string/char literals, and
  identifiers — so a `:=` or keyword hiding in a string/comment is never
  mistaken for code;
* splits a file into top-level command spans by Lean's layout rule (a command
  begins at a column-0 command/modifier/`@` token — no `qed` to key off);
* walks declarations, finds the bracket-depth-0 `:=` separating signature from
  body, and replaces the body with `sorry`.

Keeping the core free of `import Lean` and of `IO` is also what makes it
**small and WASM-targetable** (see *Browser / WASM* below) — the whole point of
the parallel Rust effort.

## What gets ablated

The unit of ablation is a body that can be replaced by `sorry`. Four kinds, all
nesting by Lean's layout rule (a block runs until the next line indented no
deeper than its opener — there's no `qed` to key off):

```lean
theorem foo : P := by              -- depth 1   declaration body
  apply And.intro
  · have h : Q := by simp          -- depth 3   typed `have` (inside a bullet)
    exact h
  · sorry                          -- depth 2   focus bullet  (`·` or `.`)

def classify : Nat → Nat
  | 0     => 0                     -- depth 1+  match arm
  | n + 1 => n
```

* **Declaration bodies** — `theorem`/`lemma`/`example`/`def`/`abbrev`/`instance`.
  Because `sorry` inhabits *any* type, non-`Prop` `def`/`abbrev` bodies ablate
  too (the "ablate things that aren't necessarily `Prop`" point).
* **Typed binders** — `have x : T := e`, `let x : T := e`, `replace`.
* **Focus bullets** — `·` (and ASCII `.`), replaced as `· sorry`.
* **Match / `fun` arms** — `| pat => rhs`, replaced as `| pat => sorry`.
* **Anonymous-constructor `by`-components** — a tactic proof in a `⟨…⟩` slot,
  e.g. `⟨state, by tac⟩`, replaced as `⟨state, by sorry⟩`. Only `by`-blocks in
  *direct* component position qualify (a `by` nested inside `(…)` is left alone),
  since those are the unambiguous, type-safe proof slots.

**Type-ascription safety.** A binder or declaration is ablated *only if it
carries an explicit type* — `have h : T := e` and `def f : T := e` ablate, but
`have h := e`, `let ⟨a,b⟩ := e`, and `def f := e` are **kept**, because
`sorry` has no inferable type there and would not elaborate. Bullets and arms
get their type from context, so they always qualify. The engine never corrupts
source: it only deletes a cleanly-delimited body and splices ` sorry` (verified
to **type-check**, and round-tripped losslessly over the whole Lean 4 source
tree — 8,682 nested bullets/arms/binders/anon-components at depth ≥ 2, 0
failures).

Everything outside a selected body is emitted verbatim, so `-p 0` is the
identity. A `method` field on each hole records the kind (`bullet`, `arm`,
`anon`, `by:<tac>`, `calc`, `trivial`, `term`) for post-hoc stratification.

## Build & run

```bash
lake build                 # -> .lake/build/bin/ablate
```

The toolchain is pinned in `lean-toolchain` to **v4.15.0**; `elan` fetches it.
(v4.15.0 is the last Lean release that ships a prebuilt `wasm32` runtime — see
*Browser / WASM* — so the project targets it for a single native+wasm build. A
syntactic tokenizer needs nothing newer.)

`ablate` takes any mix of `.lean` files and directories (walked recursively for
`*.lean`) and emits one **(challenge, solution) JSON record per file** to stdout:

```bash
./.lake/build/bin/ablate theory.lean                       # 50% of top-level bodies -> sorry
./.lake/build/bin/ablate --difficulty L1 src/             # easy: only leaf steps -> sorry
./.lake/build/bin/ablate --difficulty L4 dir/             # hard: every body gone (code+spec)
./.lake/build/bin/ablate --min-depth 2 --max-depth inf t.lean   # keep skeletons, sorry sub-proofs
./.lake/build/bin/ablate --text --all theory.lean         # just the ablated source (no JSON)
./.lake/build/bin/ablate --compact dir/ > out.jsonl       # strict one-line-per-record JSONL
```

Output is indented JSONL by default (nice in `less`/`bat`, still `jq`-readable);
`--compact` gives strict one-object-per-line JSONL for HuggingFace upload;
`--text` writes the ablated source itself (byte-exact at `-p 0`). Each record
carries `challenge_file_content`, `solution_diff` (see *Solution format* below),
the difficulty knobs,
and `holes_filled` — each removed body with `theorem_name`, `depth`,
`n_commands`, `n_lines`, `is_leaf`, `centrality` (corpus fan-in), `method`
(e.g. `by:simp`, `term`, `calc`, `trivial`) and `proof_text` — so a single dump
can be **stratified by difficulty post-hoc**.

### Difficulty

A preset **ladder** composes the raw knobs (raw knobs override any preset field):

| `--difficulty` | composition | character |
|---|---|---|
| `L0` | `--leaves-only --max-depth inf -p 0.3` | a few terminal steps |
| `L1` | `--leaves-only --max-depth inf --all` | all leaf steps; skeletons fully kept |
| `L2` | `--min-depth 2 --max-depth inf --all` | all sub-proofs; only top skeletons kept |
| `L3` | `--min-depth 1 --max-depth 1 -p 0.5` | half the whole bodies gone |
| `L4` | `--min-depth 1 --max-depth 1 --all` | every body gone (code + spec only) |

Raw knobs: `--leaves-only`, `--min-size N`/`--max-size N` (body line-count
window), `--min-depth`/`--max-depth` (`inf` allowed), and the rate — either
`-p PROB` (per-body coin) or `--count N` (exactly `min(N, matching)` bodies, a
seeded random spread; mutually exclusive with `-p`).

**Centrality.** A corpus-wide **fan-in** is computed by textual name-citation (a
fact `B` is cited by `A` if `A`'s body mentions `B`) — no prover needed.
`--min-centrality N`/`--max-centrality N` filter by it, and `--count N
--by-centrality` ablates the N *most-cited* bodies (e.g. "blank the load-bearing
lemmas"). `centrality` is recorded on every hole regardless.

### Focusing the challenge, and augmentation

* `--truncate` drops everything after the last inserted `sorry` — the file ends
  at the body to complete.
* `--shrink-challenge` drops top-level decls that come *after* the last ablated
  one from the **challenge** (keeping earlier context and `end`s);
  `--shrink-solution` does the same to the **solution**, so the answer file
  isn't padded with later theorems the model was never asked about.
* `--repeat N` emits up to N **deduplicated** ablations per file — augmentation
  that's only fruitful with a stochastic selector (`-p`, `--count`).
* `-d DIR` strips a path prefix from emitted `file_path`s (and the path-derived
  `task_id`), so datasets don't carry machine-specific absolute paths.

### Options

| flag | meaning |
|------|---------|
| `--check` | run the corpus self-test instead of emitting records |
| `--check-build` | compile-test each ablation with `lake env lean` (challenge + solution); builds only the file itself (never its dependents), so `--shrink-*` can't break it. Deps must already be built (`lake build`). |
| `--delete-lemmas` | delete eligible *used* lemmas + ablate their in-file users (correct-by-construction: only when every use is in a proof body, no `@[…]` attribute, ≥1 user). Records use `lemma_delete` + `deleted_lemmas`. |
| `--aggressively-delete-lemmas` | as above, relaxed guards, validated with `lake env lean` (drops non-compiling); backend-only, not in the demo. |
| `--difficulty L` | preset ladder `L0` (easy) … `L4` (code+spec only) |
| `--min-depth N` / `--max-depth N` | nesting-depth window (`N` may be `inf`) |
| `--leaves-only` | only bodies with no nested binding |
| `--min-size N` / `--max-size N` | body line-count window (`N` may be `inf`) |
| `--min-centrality N` / `--max-centrality N` | corpus fan-in window |
| `-p PROB` | per-body ablation probability (default `0.5`) |
| `--all` | ablate every selected body (`-p 1.0`) |
| `--count N` | ablate exactly `min(N, matching)` bodies (excl. `-p`/`--all`) |
| `--by-centrality` | with `--count`, pick the most-cited |
| `--truncate` / `--shrink-challenge` / `--shrink-solution` | challenge/solution shaping |
| `--repeat N` | up to N deduplicated ablations per file |
| `-s SESSION` | library label recorded in output (default `lean`) |
| `-d DIR` | strip prefix from emitted paths (repeatable) |
| `--seed N` | RNG seed for reproducibility |
| `--text` | output ablated source instead of JSONL |
| `--compact` | strict one-object-per-line JSONL |
| `-v` | verbose summary on stderr |

## Self-test (`--check`)

`ablate --check PATH...` verifies, for every theory at the configured depth
range: (1) lossless round-trip (`-p 0` == source byte-for-byte), (2) every
in-range body is cleanly delimited (prob-1 ablates all of them), and (3) the
ablated text preserves all top-level declaration statements. Exits non-zero on
any failure — a CI gate.

Last run over the **entire Lean 4 source tree** shipped with the toolchain:

```
$ ./.lake/build/bin/ablate --check ~/.elan/toolchains/.../src/lean
theories checked     : 2421
in-range goals       : 74619
cleanly ablated      : 74619 (100.00%)
round-trip failures  : 0
delimitation misses  : 0
re-parse mismatches  : 0
RESULT: OK
```

and at depth `2..inf` (nested sub-proofs): **2815** nested goals over `Init/`,
100% cleanly ablated, 0 failures. Hand-checked samples additionally
**type-check** in real Lean (only `declaration uses 'sorry'` warnings) — the
analogue of the Isabelle tool's `--check-build`.

```bash
lake exe ablate-test        # unit tests (round-trip, top-level, nested, centrality)
```

## Solution format (diffs)

Records store the solution as **`solution_diff`** — a unified diff that turns
`challenge_file_content` into the solution — not the whole file. Whole-file
solutions are huge for big theories (l4v files run 100k–369k chars; see
issue #107), so per-row storage is otherwise dominated by a near-duplicate of
the challenge. The diff is self-rolled (`Ablator/Diff.lean`, no dependency, so
it also runs in the WASM demo): Myers line diff, lines = `split '\n'`, hunks
`@@ -a,b +c,d @@` with `-`/`+`/` ` prefixes; an empty diff means challenge =
solution. The format is identical across all four ablators, so one `apply`
recovers the solution everywhere:

```python
def apply(challenge: str, diff: str) -> str:
    if not diff: return challenge
    a, out, oi = challenge.split('\n'), [], 0
    for line in diff.split('\n'):
        if line[:2] == '@@':
            start = int(line.split('-', 1)[1].split(',', 1)[0])
            while oi < start - 1: out.append(a[oi]); oi += 1
        elif line == '': pass
        elif line[0] == ' ': out.append(line[1:]); oi += 1
        elif line[0] == '-': oi += 1
        elif line[0] == '+': out.append(line[1:])
    out += a[oi:]
    return '\n'.join(out)
```

## Browser / WASM

A browser playground where you move a slider and watch proofs dissolve into
`sorry` — **the real Lean ablator, compiled to WebAssembly, running entirely
client-side** (no server, nothing uploaded). Keeping the core `import Lean`- and
`IO`-free is what makes this possible.

```bash
nix develop -c ./build-wasm.sh             # -> web/ablator.{js,wasm}
python3 -m http.server -d web 8000         # open http://localhost:8000/
```

How it works:

* `Ablator/Wasm.lean` exposes `@[export lean_ablate_theory]` — a C-callable
  entry taking the source text + difficulty knobs and returning the
  `{text, total, ablated, holes_filled}` JSON the playground renders.
* `lake build` emits portable C (`.lake/build/ir/**/*.c`). `build-wasm.sh`
  recompiles that C + a small marshalling shim (`wasm/shim.c`) with
  **emscripten** and links it against Lean's *prebuilt* `wasm32` runtime — the
  `lean-4.15.0-linux_wasm32` release asset (`libleanrt`/`libleancpp`/`libInit`/
  `libStd`). This is why the project is pinned to v4.15.0: it's the last Lean
  release that ships a wasm runtime, so no runtime cross-build is needed.
* The runtime is built with `-fwasm-exceptions -pthread`, so the page must be
  cross-origin isolated for `SharedArrayBuffer`; `web/coi-serviceworker.js`
  arranges that on any static host (so it also works on GitHub Pages, etc.).
* `wasm/uv_stubs.c` satisfies a few `libuv` temp-file symbols the runtime
  references but the pure ablation path never calls.

The `flake.nix` dev shell provides everything needed (`nix develop`): `elan`
(which fetches the pinned Lean toolchain via `lean-toolchain`) plus the
emscripten toolchain — nothing has to be pre-installed. Build outputs
(`web/ablator.{js,wasm}`) are git-ignored — regenerate with `build-wasm.sh`.

## Layout

- `Ablator/Token.lean` — token type + lossless `implode`
- `Ablator/Tokenize.lean` — the dependency-free Lean tokenizer
- `Ablator/Keyword.lean` — command / modifier / binder classification
- `Ablator/Span.lean` — group tokens into top-level command spans; `:=`/name finders
- `Ablator/Ablate.lean` — the depth-walk + difficulty knobs + RNG + presets
- `Ablator/Centrality.lean` — corpus fan-in (dependency centrality)
- `Ablator/Json.lean` — tiny dependency-free JSON encoder
- `Ablator/Hash.lean` — SHA-1 for stable `task_id`s
- `Ablator/Record.lean` — the (challenge, solution) record schema
- `Ablator/Wasm.lean` — `@[export]` C-callable entry for the browser playground
- `Main.lean` — `ablate` CLI + the `--check` self-test
- `Tests.lean` — `ablate-test` unit tests
- `build-wasm.sh` / `flake.nix` — emscripten WASM build + dev shell
- `wasm/shim.c`, `wasm/uv_stubs.c` — C marshalling shim + libuv link stubs
- `web/` — the in-browser playground (`index.html`, `coi-serviceworker.js`)
