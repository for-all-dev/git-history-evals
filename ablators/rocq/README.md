# rocq-ablator (OCaml / WASM)

A syntactic proof ablator for **Coq/Rocq**, in the spirit of the sibling
[`../isabelle`](../isabelle) (Rust) and
[`../lean`](../lean) (Lean). It tokenizes a `.v` file, replaces
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

Run over the entire **fiat-crypto** source tree (`../../data/fiat-crypto`):

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
dune exec bin/main.exe -- --check ../../data/fiat-crypto   # corpus self-test
dune exec bin/main.exe -- --check-build src/Foo.v       # compile-test with coqc
```

**`--check-build`** compile-tests each ablation with `coqc` (both the challenge
and the solution). It builds only the file itself — `coqc F.v` never builds F's
dependents — so dropping decls via `--shrink-*` can't break it, and a dependent
theory that referenced a dropped name is simply never built. F's own
dependencies must already be compiled (the normal state after a project build),
and coqc flags are read from the nearest `_CoqProject`. Needs `coq` (in the
flake's dev shell).

Flags mirror the Isabelle/Lean tools: `--difficulty`,
`--min-depth`/`--max-depth`, `--leaves-only`, `--min-size`/`--max-size`,
`--min-centrality`/`--max-centrality`, `-p`/`--all`/`--count`/`--by-centrality`,
`--truncate`/`--shrink-challenge`/`--shrink-solution`, `--allow-defined`, `--repeat`, `-d` (path
strip), `-s` (session), `--text`/`--compact`, `--seed`, `-v`. Records use the
shared schema with `proof_assistant: "coq"`, so a dump lines up field-for-field
with the existing HuggingFace datasets.

## Deleting lemmas

`--delete-lemmas` deletes whole *used* lemmas and ablates every in-file proof
that cited them — so the agent must re-derive the goal without the lemma it
relied on (it never even sees the deleted statement). It is
**correct-by-construction** (no prover): a lemma is deleted only when every use
of its name lies inside an ablatable proof body and it is `Qed`-opaque with ≥1
in-file user, so the result always compiles. Targets are chosen with the usual
`-p`/`--count`/`--by-centrality`/`--min-centrality` selectors. The solution is
the full original (the deleted lemma is the post-training "stuff it back in"
artifact); records use `challenge_type: "lemma_delete"` + a `deleted_lemmas` list.

`--aggressively-delete-lemmas` (backend-only, needs `coq`) relaxes the syntactic
guards and validates each challenge with `coqc`, dropping any that don't compile.
It is not surfaced in the browser demo.

## Solution format (diffs)

Records store the solution as **`solution_diff`** — a unified diff that turns
`challenge_file_content` into the solution — not the whole file. Whole-file
solutions are huge for big theories (l4v files run 100k–369k chars; see
issue #107), so per-row storage is dominated by a near-duplicate of the
challenge. The diff is self-rolled (no dependency, so it also runs in the WASM
demo): Myers line diff, lines = `split('\n')`, hunks `@@ -a,b +c,d @@` with
`-`/`+`/` ` prefixes; an empty diff means challenge = solution.

Recover the full solution by applying it to the challenge:

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
in the mining pipeline (cf. `../../experiments/docker`), not in WASM. Staying in
OCaml here keeps that pivot cheap.
