# isabelle-ablator

**Semantic ablation for Isabelle theories.** Parse a `.thy` file with the
*bundled Isabelle/Scala outer-syntax parser* and replace whole proofs with
`sorry`, preserving everything else (statements, definitions, comments,
whitespace) byte-for-byte. The ablated theories are the *challenge* side of a
proof-synthesis training/eval pair; the original theories are the ground truth.

This is the "ablate" complement to the git-history miner in this repo: instead
of harvesting `(commit, file)` holes from history, it manufactures holes
*synthetically* from any theory at a tunable rate — the
[seL4-ablate-bench](../docs/SoW.md) idea of "progressively delete proofs and see
how much an agent can reconstruct," with a slider on how much to delete.

## Why a Scala tool

Isabelle ships its own outer-syntax tokenizer/parser in Scala
(`isabelle.Token`, `isabelle.Outer_Syntax`, `isabelle.Keyword`,
`isabelle.Command_Span`). Reusing it means we classify commands with the *exact*
keyword kinds Isabelle uses, so proof boundaries are found correctly rather than
by brittle regex. The keyword table itself is loaded from a real session
(`Sessions.deps(...).overall_syntax`), so `function`/`instance`/`lift_definition`
goals and any session-specific commands are recognised.

## How it works

1. Load a session's `Outer_Syntax` (keyword table) — default `HOL`, sub-second.
2. `syntax.parse_spans(text)` → command spans, each carrying its `keyword_kind`.
3. Walk the spans tracking Isar goal-stack **depth**, recursing into kept proofs.
   Goals nest: a `thy_goal*` command opens a depth-1 proof; `prf_goal`/`prf_asm_goal`
   (`have`/`show`/`obtain`/…) and `prf_open` (`{`) push; `qed`/`prf_close` (`}`)
   pop; `qed_global` (`oops`) closes.
4. Replace a goal's proof with `sorry` iff its depth is in `[--min-depth, --max-depth]`
   (and a per-proof coin `-p` fires), keeping the statement and the proof's leading
   indentation. Shallower goals keep their structure and we recurse in; deeper goals
   are left verbatim. Everything outside proofs is emitted verbatim — `-p 0` is the
   identity.

**Depth** = open goal/block-stack level when the goal opens (so `{ }` blocks add a
level). Because `sorry` erases a whole proof body, ablating a goal stops the descent
there — you either replace a `proof…qed` wholesale or keep its skeleton and ablate
inside, never both.

**Script-style goals** (`subgoal` and friends: `prf_script_goal`/`_asm_goal`) are
never replaced — `sorry` isn't reliably valid in apply-script position — though they
still nest. A theory command appearing while a goal is open (a desync) makes the tool
keep that proof verbatim — it never corrupts the source.

## Build & run

```bash
nix develop            # provides isabelle (2025) + JDK
bash build.sh          # -> build/ablator.jar
```

`ablate` takes any mix of `.thy` files and directories (directories are walked
recursively for `*.thy`) and emits one **(challenge, solution) JSON record per
theory** to stdout:

```bash
./bin/ablate theory.thy                            # 50% of top-level proofs -> sorry
./bin/ablate --difficulty L1 src/HOL/Library       # easy: only leaf steps -> sorry
./bin/ablate --difficulty L4 dir/                  # hard: every proof gone (code+spec)
./bin/ablate --min-depth 2 --max-depth inf t.thy   # keep skeletons, sorry sub-proofs
./bin/ablate --leaves-only --min-size 5 t.thy      # only big terminal proofs
./bin/ablate --compact dir/ > out.jsonl            # strict one-line-per-record JSONL
```

Records are **indented by default** (a stream of pretty JSON objects, nice in
`less`/`bat`); still `jq`-readable (`jq -c . out`). Use `--compact` for strict
one-object-per-line JSONL (e.g. HuggingFace upload). Each record carries
`challenge_file_content` (proofs → `sorry`), `solution_file_content` (ground
truth), the difficulty knobs used, and `holes_filled` — the removed proofs, each
with `theorem_name`, `depth`, `n_commands`, `n_lines`, `is_leaf`, `centrality`
(corpus fan-in), and `method` (e.g. `by:simp`, `apply`, `structured`), so a
single dump can be **stratified by difficulty post-hoc**. Field names mirror the
git-history datasets in `../artifacts`.

Or via the flake directly: `nix run .# -- --all theory.thy`.

### Difficulty

Reconstruction difficulty has a few roughly-orthogonal axes, exposed as raw
knobs and composed into a preset **ladder** (raw knobs override any preset field):

| `--difficulty` | composition | character |
|---|---|---|
| `L0` | `--leaves-only --max-depth inf -p 0.3` | a few terminal steps |
| `L1` | `--leaves-only --max-depth inf --all` | all leaf steps; skeletons fully kept |
| `L2` | `--min-depth 2 --max-depth inf --all` | all sub-proofs; only top skeletons kept |
| `L3` | `--min-depth 1 --max-depth 1 -p 0.5` | half the whole proofs gone |
| `L4` | `--min-depth 1 --max-depth 1 --all` | every proof gone (code + spec only) |

Easier levels keep more structure as scaffolding; `L4` is the seL4-ablate-bench
extreme. The discrete rungs are meant for plotting agent success vs. difficulty.

Raw knobs: `--leaves-only` (only goals whose proof has no nested goal — the
true bottom of the proof tree), `--min-size N`/`--max-size N` (filter by proof
*command count*, so you can target trivial `by simp`s or long manual proofs),
`--min-depth`/`--max-depth`, and the rate — either `-p PROB` (per-proof coin) or
`--count N` (exactly `min(N, matching)` proofs per theory, a uniformly-random
seeded spread; mutually exclusive with `-p`). `--count` is exact when matches
don't nest (a single depth, or `--leaves-only`); with overlapping ranges an
ablated ancestor can shadow selected descendants, so it's best-effort up to N.

**Centrality.** Difficulty also has a dependency axis: removing a lemma that
many proofs lean on is harder and more consequential than removing a leaf. A
corpus-wide **fan-in** is computed by textual name-citation (a fact `B` is cited
by theorem `A` if `A`'s proof mentions `B`) — no prover needed. `--min-centrality
N`/`--max-centrality N` filter by it, and `--count N --by-centrality` ablates the
N *most-cited* matching lemmas (e.g. "blank the 5 load-bearing lemmas in each
theory"). `centrality` is recorded on every hole regardless. On `src/HOL/Algebra`
the top-cited come out as `one_closed`, `subringE`, `is_group`, `inv_closed`, …
— the genuinely foundational lemmas. It's approximate (matched by name; short
names that collide with variables are dropped); the accurate version via
Isabelle's `export_theory` theorem deps is a planned upgrade.

### Options

| flag | meaning |
|------|---------|
| `--check` | run the corpus self-test instead of emitting records |
| `--check-build D` | copy + ablate + `isabelle build` session dir D (repeatable) |
| `--difficulty L` | preset ladder `L0` (easy) … `L4` (code+spec only) |
| `--min-depth N` | ablate goals at nesting depth ≥ N (default `1`) |
| `--max-depth N` | ablate goals at nesting depth ≤ N; `N` may be `inf` (default `1`) |
| `--leaves-only` | only ablate goals whose proof has no nested goal |
| `--min-size N` | only ablate proofs with ≥ N proof commands (default `0`) |
| `--max-size N` | only ablate proofs with ≤ N proof commands; `N` may be `inf` |
| `--min-centrality N` | only ablate lemmas with corpus fan-in ≥ N |
| `--max-centrality N` | only ablate lemmas with corpus fan-in ≤ N; `N` may be `inf` |
| `-p PROB` | probability of ablating each selected proof (default `0.5`) |
| `--all` | ablate every selected proof (`-p 1.0`) |
| `--count N` | ablate exactly `min(N, matching)` proofs, a random spread (excl. `-p`/`--all`) |
| `--by-centrality` | with `--count`, pick the most-cited proofs instead of random |
| `-s SESSION` | session whose keyword table to parse with (default `HOL`) |
| `-d DIR` | extra session root directory (repeatable) |
| `--afp DIR` | AFP `thys` dir added (`-d`) for `--check-build` deps (repeatable) |
| `--keep` | keep `--check-build` working copies |
| `--seed N` | RNG seed for reproducibility (per-file seed derived from it) |
| `--compact` | strict one-object-per-line JSONL (no indentation) |
| `-q` | suppress incidental progress on stderr |

## Self-test (`--check`)

`ablate --check PATH...` loads the syntax once and, for every theory at the
configured `--min-depth`/`--max-depth`, verifies (1) lossless round-trip
(parse→implode == source), (2) every in-range proof is cleanly delimited (prob-1
ablates all of them), and (3) the ablated text preserves all theory-level goal
statements. It also times the ablation pass. Prints a report, exits non-zero on
any failure — a CI gate.

```bash
./bin/ablate --check "$(isabelle getenv -b ISABELLE_HOME)/src/HOL"
./bin/ablate --check --min-depth 2 --max-depth inf "$ISABELLE_HOME/src/HOL"
```

Last run over `src/HOL` (1467 theories), all checks clean:

- depth `1..1` (top-level): **69,679 proofs**, 100% ablated, 0 failures
- depth `2..inf` (sub-proofs): **64,364 proofs**, 100% ablated, 0 failures

## Build validation (`--check-build`)

`--check` proves the ablated text *parses* and round-trips; `--check-build`
proves it still *type-checks*. For each session directory (one containing a
`ROOT`) it copies the dir, renames the declared session(s) (a `__ablated`
suffix, so the copy never clashes with the original on the build path), ablates
every `.thy` in the copy, then runs `isabelle build -o quick_and_dirty=true`. A
clean build certifies the challenge is well-formed Isabelle — statements and
definitions elaborate, only the proof bodies are admitted (`quick_and_dirty`
turns `sorry` from a build error into an admitted goal). This is the gate that
matters most for sub-proof ablation, where nested `sorry` validity isn't
guaranteed by parsing alone.

```bash
# one or more targets; --afp adds AFP `thys` dirs (-d) so dependencies resolve
./bin/ablate --check-build path/to/Entry --afp /path/to/afp/thys --all
./bin/ablate --min-depth 2 --max-depth inf \
    --check-build EntryA --check-build EntryB --afp /path/to/afp/thys
```

Validated end-to-end: a `Main`-importing session, multiple targets at once, an
AFP-style cross-session dependency resolved via `--afp` (with the rename
avoiding a clash against the on-path original), and — crucially — a **depth
`2..inf` sub-proof ablation that builds cleanly** (nested `sorry` accepted).
Builds reuse the bundled HOL/Pure heaps; targets importing heavier sessions
(HOL-Analysis, …) build those first, so it is intensive — start with small
entries. `--keep` preserves the working copies for inspection.

## Layout

- `flake.nix` — Isabelle 2025 + JDK dev shell and `ablator` package
- `src/Ablate.scala` — ablation library + CLI + JSONL records (`proofablate.Ablate`)
- `src/Check.scala` — corpus self-test, run via `ablate --check` (`proofablate.Check`)
- `src/CheckBuild.scala` — build validation, run via `ablate --check-build`
- `src/Centrality.scala` — corpus fan-in (dependency centrality)
- `build.sh` — `isabelle scalac` → `build/ablator.jar`
- `bin/ablate` — classpath wrapper (checkout or nix-installed)
