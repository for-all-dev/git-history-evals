# isabelle-ablator (Rust / WASM)

A Rust port of the Isabelle/Scala ablator in [`../scala`](../scala) — same
semantic ablation of Isabelle theories (replace selected proofs with `sorry`,
preserve everything else byte-for-byte), but with **no JVM and no Isabelle at
runtime**, so it compiles to WebAssembly and runs entirely in the browser.

It reimplements Isabelle's outer-syntax tokenizer (`Symbol` + `Scan` +
`Token.explode`), `parse_spans`, the depth-walk ablation, centrality fan-in, and
all the difficulty knobs. The HOL keyword table is **baked in**
(`data/hol-keywords.json`, exported once from a real Isabelle session), so the
tokenizer classifies commands exactly as Isabelle does without a prover.

## Validation — byte-equivalent to the Scala reference

Checked against `../scala` over the **entire Isabelle/HOL core library**
(`src/HOL`, 1467 theories):

| | Rust | Scala |
|---|---|---|
| theories | 1467 | 1467 |
| in-range goals (depth 1..1) | 69,679 | 69,679 |
| cleanly ablated | 100% | 100% |
| round-trip / delimitation / re-parse failures | 0 | 0 |
| ablation pass | ~0.5 s | ~20 s |

Byte-level differential of `--all --text` (and `--shrink-challenge`, `--truncate`,
`--by-centrality --count`, depth `2..inf` sub-proofs) is **identical** to the
Scala output across sampled theories. Random `-p`/`--count` selections use a
different RNG by design, so only deterministic configs are byte-compared.

## CLI

```bash
cargo build --release            # -> target/release/ablate
./target/release/ablate theory.thy                 # JSONL (challenge, solution)
./target/release/ablate --all --text theory.thy    # just the ablated theory
./target/release/ablate --difficulty L2 dir/        # preset ladder L0..L4
./target/release/ablate --check "$ISABELLE_HOME/src/HOL"   # corpus self-test
```

Same flags as the Scala tool — `--difficulty`, `--min-depth`/`--max-depth`,
`--leaves-only`, `--min-size`/`--max-size`, `--min-centrality`/`--max-centrality`,
`-p`/`--all`/`--count`/`--by-centrality`, `--truncate`/`--shrink-challenge`/`--shrink-solution`,
`--repeat`, `-d` (path strip), `--text`/`--compact`, `--seed`, `-v`. A custom
session table can be supplied with `--keywords table.json`. Build validation
(`--check-build`, needs `isabelle build`) stays in the Scala tool.

## Website (in-browser ablation)

```bash
nix develop -c ./build-wasm.sh                   # -> web/pkg/ (≈190 KB wasm)
python3 -m http.server -d web 8000               # open http://localhost:8000/
```

The `flake.nix` provides the dev shell (rustup + wasm-pack + the WASM tooling);
`cargo`/`rustc` and the `wasm32-unknown-unknown` target come from `rustup`. Run
`nix develop` for an interactive shell (or `nix develop -c <cmd>` for one-offs).

`web/index.html` is a single-page app: paste a theory, pick the difficulty
preset / knobs, and the ablated challenge updates live. Everything runs
client-side — nothing is uploaded.

## Fuzzing

The parser is fuzzed with [`cargo-fuzz`](https://github.com/rust-fuzz/cargo-fuzz)
(libFuzzer). The target `fuzz/fuzz_targets/parse_roundtrip.rs` feeds arbitrary
UTF-8 to `parse_spans` and asserts the **round-trip invariant** (concatenating
every span's source reproduces the input) — the property the whole tool relies
on — then runs ablation over the result (full depth range + truncate +
shrink-challenge + shrink-solution) to catch panics.

`cargo-fuzz` needs nightly (for the sanitizer); get it from nix:

```bash
cd rust
nix shell nixpkgs#cargo-fuzz -c \
  env RUSTUP_TOOLCHAIN=nightly cargo fuzz run parse_roundtrip -- -max_total_time=60
```

The corpus is seeded with real HOL theories (`fuzz/corpus/parse_roundtrip/`).
A crash artifact (if any) is replayable with `cargo fuzz run parse_roundtrip <artifact>`.
A 150 s run (seeded from HOL) did **70k+ executions**, grew the corpus to ~1000
inputs, and found **no crashes and no round-trip violations**.

## Layout

- `src/tokenize.rs` — Symbol matcher, Scan scanners, keyword lexicon, `explode`
- `src/span.rs` — `parse_spans` + `Syntax` (keywords + lexicons)
- `src/ablate.rs` — depth-walk ablation, knobs, presets, seedable RNG
- `src/centrality.rs` — corpus fan-in
- `src/record.rs`, `src/sha1.rs` — JSONL record (schema matches `../scala`)
- `src/bin/ablate.rs` — CLI; `src/wasm.rs` — `wasm-bindgen` entry
- `data/hol-keywords.json` — baked-in HOL keyword table

## Regenerating the keyword table

Exported from Isabelle via the Scala tool's `load_syntax`; to refresh (or add
another session), dump `syntax.keywords.kinds` as a `{name: kind}` JSON object
and drop it in `data/` (or pass it at runtime with `--keywords`).
