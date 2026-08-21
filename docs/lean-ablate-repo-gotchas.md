# Lean ablation — per-repo gotchas & harness notes

Reference for reproducing / extending the validated Lean corollary-ablation datasets
(`artifacts/lean-ablate/<repo>/`) — **read this before the Leanstral 1.5 run.** It records
every non-obvious thing that bit us: repo build quirks, harness workarounds, and ablator
bugs found + fixed. Companion to `docs/lean-ablate-datasets.md` (method) and
`scratchpad_pilot/` (pipeline scripts).

Datasets were mined with the Lean ablator:
`ablate --corollary-delete-lemmas-leaves-all --shrink-solution-minimal --compact --seed 42 -d <repo> <repo>`
(one ablation per eligible corollary per file, leaf-level, shrunk solution) and validated
with `baselines/ ablate-baseline --dry-run` (pre-flight compile of challenge + solution).
Every record carries `repo` + `revision` provenance; `manifest.json` pins `lean_toolchain`.

---

## Ablator bugs found & fixed (affect the DATA — re-mine with fixed ablator)

1. **`@[simp]` (attribute) lemmas dropped by minimal-shrink.** `--shrink-solution-minimal`'s
   dependency closure is syntactic and can't see simp-set membership, so it dropped
   `@[simp]`/`@[grind]`/instance lemmas that a kept `simp` needs → "unsolved goals" in a
   *non-holed* proof. Fixed by keeping every attribute-tagged decl in the slice
   (`Ablator/Ablate.lean` `spanHasAttr`). VExpr.lean: 0/53 → 52/53.
2. **`set_option … in` / `open … in` prefix orphaned.** The span parser merged `@[…]`/
   modifier prefixes into their decl but NOT `set_option … in` / `open … in`; dropping the
   decl left the modifier dangling (`set_option … in\nend` → "Unexpected name after `end`:
   current section is unnamed"). Fixed in `Ablator/Span.lean` `isInModifierPrefix`.
   lean-zip: 48/60 → 32/32 on the sample.
3. **Minimal-slice O(n³) blowup (fixed).** `sliceDelete`'s `openersOfName`/`bySpan` were
   O(lemmas) scans called per-name inside the closure BFS, per-slice by `ablateAll` — so
   `--shrink-*-minimal` went ~O(n³) on large corollary-rich files (a 1200-line VCV-io
   theory took 25 s and looked like a hang). Fixed by precomputing a last-component index
   (`byLastComp`) + `spanToLemma` map → O(1) lookups; 24,713 ms → 609 ms. Insight: a full-
   name match implies a last-component match, so ONE last-component index is complete.
4. **Leaf-holing metavariable cascades (NOT fixed — inherent).** `-leaves-all` holes one
   leaf tactic step; inside a rewrite-heavy dependent proof the resulting `sorry` can carry
   a metavariable type → "Invalid rewrite argument ?m.N" → the whole decl fails to
   elaborate → its name goes unknown downstream. These are correctly dropped by the
   dry-run. This is why dependent-proof-heavy repos (lean4lean 48%, nickelean 22%) validate
   lower than flat ones (cedar 96%, ryu 100%).

> Parity TODO: bugs 1 & 2 are Lean-only fixes. The Rocq/Isabelle slicers share the design
> and may have analogous orphaning (Coq `Local`/`#[…]`/`Section`…`End`); unaudited.

## Harness gotchas (affect VALIDATION, not the data)

- **Multi-project repos → mathlib-clone RUNAWAY (historical; #119 closed the mechanism).**
  Before #119, a repo with a second lake subproject whose `.lake` wasn't built ran
  `lake env lean` on those files, which `git clone`d mathlib4 into `/tmp` **per record** —
  filled disk fast. Seen in **starkware** (`Stwo/` subproject; mined file_paths `Stwo/…`),
  and structurally in **aeneas**, **rust-lean** (many sub-lakefiles), **lean-zip**
  (`bench/`). `check` no longer runs `lake` at all (bare `lean` only), so this specific
  clone-runaway can't happen anymore — an unbuilt subproject now just fails the compile
  with "unknown module" instead. Still build *every* sub-lakefile (`cd sub && lake exe
  cache get && lake build`) so those records aren't spuriously malformed; `finish_ab.sh`
  guards by dropping `Stwo/` records if Stwo's mathlib is absent.
- **Monorepo sibling deps.** **SizzLean** lives in the `etheorem/etheorem` monorepo and
  `require`s a sibling `../LeanHazmatSha256`. Validate with the **monorepo root** as the
  baseline `src` (`data/etheorem`, file_paths `packages/SizzLean/…`) so the overlay contains
  the sibling — not the package dir alone (→ "package directory not found").
- **`lakefile.lean` + C FFI used to break `lake env lean`.** **lean-zip** builds C FFI
  (zlib) + a test exe that won't link here (`collect2: ld returned 1`), so lake used to
  mark the config "invalid" and refuse `lake env lean`. The library oleans are fine.
  Moot since #119: `check` in `baselines/…/provers/lean.py` always runs bare `lean` with a
  reconstructed `LEAN_PATH` (own + every package's `build/lib[/lean]`) — it never asks
  lake to elaborate the config at all, so an unlinkable FFI target can't block it.
- **Native `:c.o` mathlib builds are wasteful.** Some repos' default `lake build` compiles
  mathlib to native (gcc, 30–90 s/module × thousands) — NOT needed for `lake env lean`
  (which only loads oleans). `cache get` already fetches the oleans; build just the repo's
  lean_lib target(s), or don't full-build.
- **Corrupt mathlib clone after an interrupted build.** Killing a build mid-`git clone`
  leaves `.lake/packages/mathlib` corrupt → "could not resolve 'HEAD' to a commit". Fix:
  `rm -rf <repo>/.lake/packages/mathlib` then re-`cache get`.
- **Background jobs die on parent timeout.** Launching `… &` inside a foreground shell that
  then times out (SIGTERM) kills the whole process group. Use `setsid nohup … &` for durable
  background runs (see `finish_ab.sh`).
- **Special chars in paths break the miner.** `mine_repo.sh` piped `find | xargs`
  without null-delimiting; a dir with a quote/space (verse-lab/loom's `NonDetT'`) makes
  xargs abort after 1 file → silent under-count (loom 46→1). Fixed with `-print0 | xargs -0`.
  loom was the only affected repo (only one with such a path).
- **Race: build must finish before validate.** A repo whose mathlib build is still running
  when validation starts → all-malformed "no Mathlib.olean in search path" (TTBFL 0/191 →
  143/191 after the build completed). Ensure the build is fully done before mining/validating.
- **Concurrent mathlib builds FILL THE DISK.** Each repo keeps its OWN `.lake/packages/mathlib`
  (~5-7 GB); building many in parallel exhausted a 1.8 TB disk to 100% → cascading
  "No space left on device" clone/build failures (cslib, sparkle, wadray, veil, pcf-lean, PoL
  all 0/mined in one run). Build mathlib repos **sequentially** and `rm -rf */.lake/packages/mathlib`
  between them (datasets are self-contained — challenge/solution text is IN the JSONL, so built
  `.lake` is disposable after finalize). Recovered cslib 0→322, sparkle 0→281.
- **Dotted lean_lib targets.** `lake_lib «IP.RV32»`/`«Examples.CDC»` (verse... Verilean/sparkle):
  the lib-name extractor must include `.` (`[A-Za-z0-9_.]+`) or `lake build IP …` fails "unknown
  target `IP`" → 0 oleans → all-malformed. sparkle recovered once fixed.
- **Old-toolchain repos build mathlib from source.** repos pinned to old Lean (formal-snarks
  v4.10, wadray v4.9) get an incomplete `cache get` → lake compiles mathlib from source (~30-60 min,
  and CPU-heavy). Budget for it or skip low-yield old-toolchain repos.
- **Validation is CPU/IO-heavy for mathlib repos** (~5–10 s/record: overlay + 2 compiles).
  Use the sharded `par_dryrun.sh` (24–32 shards on the 64-core box). Big repos take a while.
- **C++ FFI repos need `g++`** (not on PATH here). **SampCert** builds `ffi.cpp` via `g++`
  → "could not execute external process 'g++'" (rc=1). Fix: `ln -sf $(ls /nix/store/*gcc-14*/bin/g++)
  ~/.local/bin/g++` (on PATH), then build. Exports must reach the baseline shards too
  (`export PATH="$HOME/.local/bin:$PATH"`). SampCert then validates 220/240.
- **Subproject lib target name ≠ dir name.** starkware's `Stwo/` lean_lib is `«Verification»`,
  so `lake build Stwo` built 1 olean (its 484 records mostly failed); `lake build Verification`
  (in `Stwo/`) built the real lib → Stwo recovered **17 → 264 good** (starkware 211 → 458).
  Always read the subproject's lakefile for the actual `lean_lib` name.

## Per-repo notes & status

| repo | build | notes |
|---|---|---|
| cedar-spec | batteries, prebuilt | cleanest; `cedar-lean/` subdir (src=`data/cedar-spec`, strip=`data/cedar-spec`) |
| lean4lean | prebuilt | `Experimental/` files are WIP (pre-existing `sorry` → sol_BAD); dependent proofs → leaf-holing malformed |
| LNSym | prebuilt (nightly-2024-10-07) | |
| clean, ArkLib, CompPoly, verity | mathlib | verity has 4 lakefiles (multi-project); ArkLib/CompPoly/verity builds not finished |
| starkware-formal-proofs | mathlib | 3 roots: `Verification/` (root — ~191/254 good), `Stwo/` (subproject — `lake build Stwo` only built 1 olean, so its 484 records mostly malformed on missing own-deps; needs a **full** Stwo build to recover), `lean3/` (Lean 3 syntax → malformed). Net ~208/968 good, almost all `Verification/`. |
| curve25519-dalek-lean-verify | mathlib | single-project, safe |
| yul-semantics, dolev-yao | mathlib | small, clean |
| ryu-lean4, shortest-decimal, nickelean, FLoPS, btc-verified | mathlib (lexicone42/rutgers/ProofOfKeags) | small; `cache get` then lib-only build; some default-build native mathlib (skip) |
| hax (cryspen) | mathlib | Lean sparse (32 records / 5 files); main yield in `hax-lib/proof-libs/lean`; also 608 **Coq** files for the Rocq ablator |
| SizzLean | monorepo | see monorepo-sibling gotcha; `etheorem/etheorem` @ `packages/SizzLean` |
| lean-zip | FFI, no-deps | see FFI gotcha; 658 records; needs the lean.py LEAN_PATH fallback |
| TensorLib | batteries+aesop | tiny (2 records) |
| SampCert | mathlib | **own build fails rc=1** — unresolved |
| lean-mlir | mathlib (nightly-2025-12-01), 9449 files | built (libs SSA/AliveExamples/…/CIRCT/ISL = only 149 real modules; `lake exe cache get` gives an *incomplete* nightly mathlib cache so lake builds the rest from source, ~30 min). **Low yield: 40/294** — the 9,253 SSA files are ~98% generated Alive peephole cases (`by alive_auto` decision-procedure proofs, zero in-file lemma deps → no corollaries). Real ablatable content is Blase (bitvector) + some Projects. High file count ≠ ablation yield. |
| TorchLean (1162), evm-asm (3059), hex-dev (369) | mathlib | large — not built |
| aeneas, rust-lean | multi-project | many sub-lakefiles; not built |

## Validated dataset rates (good / mined), seed 42 — 27 datasets, 13,917 / 19,935 total

evm-asm 7368/7466 · cedar-spec 1116/1167 · CompPoly 1100/1490 · TorchLean 676/728 ·
lean-zip 657/658 · hex-dev 459/3821 · starkware 458/968 · ArkLib 336/656 · clean 276/386 ·
curve25519 253/288 · SampCert 220/240 · verity 183/956 · FLoPS 162/184 · lean4lean 127/263 ·
shortest-decimal 121/135 · LNSym 119/140 · aeneas 110/147 · ryu-lean4 57/57 · yul-semantics 27/27 ·
btc-verified 25/27 · hax-proof-libs 22/27 · Leroy 17/20 · nickelean 15/67 · verified-compiler 6/6 ·
SizzLean 4/8 · TensorLib 2/2 · dolev-yao 1/1.  **Only lean-mlir untackled** (9449 files, 6 subprojects).

Clean flat proof libs validate ~90-99% (evm-asm 99%, TorchLean 93%, cedar 96%); dependent-proof-
heavy repos far lower (hex-dev 12%, verity 19%, lean4lean 48%) due to inherent leaf-holing metavar
cascades (filtered by dry-run, not a bug). `Experimental/`/WIP, Lean-3 dirs, and pre-existing
`sorry` are correctly excluded.
