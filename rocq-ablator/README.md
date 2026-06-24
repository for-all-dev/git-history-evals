# rocq-ablator (OCaml / WASM)

A syntactic proof ablator for **Coq/Rocq**, in the spirit of the sibling
[`../isabelle-ablator`](../isabelle-ablator) (Rust) and
[`../lean-ablator`](../lean-ablator) (Lean). It tokenizes a `.v` file, replaces
selected proofs with an *admit-style hole*, and preserves everything else
(statements, definitions, comments, whitespace) **byte-for-byte**. There is **no
Coq / prover at runtime**, so the whole core compiles to a small WebAssembly
module and runs entirely in the browser.

It is written in **OCaml** (not Rust): the official Coq parser is far too heavy
for WASM (jsCoq-scale), and for *syntactic* ablation we only need sentence
boundaries and proof delimiters — a small hand-rolled lexer. OCaml keeps a clean
pivot to a future **semantic** ablator that would wrap the official toolchain
(`coq-lsp`/petanque or SerAPI) to inspect proof *state* between tactics; that
work runs offline, not in WASM.

## What gets ablated

The unit of ablation is a proof.

- **Top-level (depth 1)** — a whole proof script. `Lemma foo : T. Proof. … Qed.`
  becomes `Lemma foo : T. Proof. Admitted.` (valid with or without an original
  `Proof.`). `Defined`-terminated proofs are skipped by default (`--allow-defined`
  to include them; their opacity matters for downstream `Compute`).
- **Nested (depth ≥ 2)** — a focused sub-proof inside a `Proof … Qed`:
  - a **brace block** `{ … }` → `{ admit. }`
  - a **bullet segment** (`-` / `+` / `*`, `--` / `++` / `**`, …) → `- admit.`
  - an inline **`… by tac`** clause (e.g. `assert (H : T) by tac.`) → `… by admit.`

  A proof with an admitted goal cannot be closed by `Qed`, so the enclosing
  terminator `Qed`/`Defined`/`Save` is automatically rewritten to `Admitted`.

Everything is selectable by the same difficulty knobs as the other ablators
(probability / exact count, depth window, leaves-only, body-size window, corpus
centrality, context shaping) and the **L0–L4 preset ladder**.

## Validation — lossless over real Coq

Run over the entire **fiat-crypto** source tree (`../data/fiat-crypto`):

| | rocq-ablator |
|---|---|
| files checked | 652 |
| in-range proofs (depth 1) | 4,926 |
| cleanly ablated | 100% |
| round-trip / delimitation / re-parse failures | 0 |
| nested pass (depth 1..inf) candidates | 5,023, 0 failures |
| ablation time | < 1 s |

`--check` validates three invariants per file: round-trip (prob 0 is the
identity), delimitation (prob 1 ablates every in-range proof), and re-parse (the
number of goal statements is unchanged by ablation).

## CLI

```bash
dune build                                   # -> _build/default/bin/main.exe
dune exec bin/main.exe -- theory.v           # JSONL (challenge, solution)
dune exec bin/main.exe -- --all --text t.v   # just the ablated theory
dune exec bin/main.exe -- --difficulty L2 src/   # preset ladder L0..L4 (nested)
dune exec bin/main.exe -- --check ../data/fiat-crypto   # corpus self-test
```

Flags mirror the Isabelle/Lean tools: `--difficulty`,
`--min-depth`/`--max-depth`, `--leaves-only`, `--min-size`/`--max-size`,
`--min-centrality`/`--max-centrality`, `-p`/`--all`/`--count`/`--by-centrality`,
`--truncate`/`--shrink-challenge`/`--shrink-solution`, `--allow-defined`, `--repeat`, `-d` (path
strip), `-s` (session), `--text`/`--compact`, `--seed`, `-v`. Records use the
shared schema with `proof_assistant: "coq"`, so a dump lines up field-for-field
with the existing HuggingFace datasets.

## Website (in-browser ablation)

```bash
nix develop -c ./build-wasm.sh                   # -> web/ablator.{js,wasm}
python3 -m http.server -d web 8000               # open http://localhost:8000/
```

`web/index.html` is a single-page app: paste a theory, pick the difficulty
preset / knobs, and the ablated challenge updates live. Everything runs
client-side — nothing is uploaded.

## Layout

- `lib/tokenize.ml` — lossless lexer (the fullstop rule, nested `(* *)`, strings,
  qualified idents, numbers)
- `lib/keyword.ml` — static Coq vernacular classification
- `lib/span.ml` — fullstop-delimited spans; focus braces/bullets resolved at
  tactic position
- `lib/ablate.ml` — depth-walk, knobs, presets, seedable RNG, the `Qed→Admitted`
  rewrite
- `lib/centrality.ml` — corpus fan-in
- `lib/record.ml`, `lib/sha1.ml` — JSONL record (schema matches the siblings)
- `bin/main.ml` — CLI; `wasm/ablator_wasm.ml` — the `wasm_of_ocaml` entry
- `test/test_ablate.ml` — unit tests + edge cases (`dune test`)

## Design notes

- **Fullstop rule.** A `.` ends a sentence iff followed by whitespace or EOF.
  Otherwise it is a qualified-name dot (`Nat.add`), a projection (`.(`), a
  recursive-notation ellipsis (`..`), or part of a number (`3.14`).
- **Tactic position.** Focus braces `{`/`}` and bullets `-`/`+`/`*` are treated
  as structural only right after a `.` / `{` / `}` / bullet, so term-level braces
  (`{ x | P x }`, records `{| … |}`) and operator uses of `- + *` are never
  mistaken for focusing.
- **Holes.** `Admitted.` at top level; `admit.` for nested goals (with the
  enclosing terminator rewritten). Both keep the challenge well-formed.

## Future: semantic ablation

Walking a proof and inspecting goal state between tactics (to ablate mid-tactic)
requires *executing* Coq — a job for `coq-lsp`/petanque or SerAPI, run offline
in the mining pipeline (cf. `../experiments/docker`), not in WASM. Staying in
OCaml here keeps that pivot cheap.
