# CompCert Mining Strategy Notes

## Repo Overview

- **3,301 commits** over **20 years** (2006-2026)
- **253 Coq `.v` files**, estimated **250,000+ lines of proof**
- **Zero admitted proofs** across all samples — exceptional verification rigor
- Xavier Leroy wrote ~90% of the proofs; style is remarkably consistent
- 974 commits (29.5%) touch `.v` files; 459 touch `*proof*.v` files specifically

## Directory Structure (proof-relevant)

| Directory | `.v` files | Commits touching dir | Role |
|-----------|-----------|---------------------|------|
| `backend/` | 54 | 2,709 | Compiler middle-end: optimizations, RTL, register allocation. **Highest proof volume.** |
| `cfrontend/` | 21 | 1,322 | C front-end: Clight semantics, type checking |
| `lib/` | 20 | 552 | Core libraries: Integers, Maps, Floats |
| `common/` | 18 | 659 | Shared framework: Memory model, Events, Smallstep |
| `flocq/` | 31 | — | IEEE 754 floating-point (highest theorem density) |
| `powerpc/`, `arm/`, `aarch64/`, `riscV/` | 18 each | ~900-1,230 | Architecture backends |
| `x86/` | 17 | ~599 | Unified x86 backend |
| `driver/` | 3 | 569 | End-to-end correctness (top-level theorems) |

## Most-Modified Proof Files

| File | Commits | Area |
|------|---------|------|
| `backend/Selectionproof.v` | 70 | Instruction selection correctness |
| `cfrontend/Cminorgenproof.v` | 69 | Clight-to-Cminor translation |
| `powerpc/Asmgenproof.v` | 63 | PowerPC assembly generation |
| `backend/Stackingproof.v` | 63 | Stack frame layout |
| `backend/Allocproof.v` | 63 | Register allocation |
| `cfrontend/Cshmgenproof.v` | 59 | Clight-to-Csharpminor |
| `backend/CSEproof.v` | 57 | Common subexpression elimination |
| `backend/RTLgenproof.v` | 52 | RTL generation |
| `cfrontend/Initializersproof.v` | 48 | Global variable initializers |
| `backend/Constpropproof.v` | 47 | Constant propagation |

## Mining Priority Tiers

### Tier 1: `backend/` proof files
Largest, most active, clearest pass-proof pairing. Each compiler pass (`Selection.v`) has a companion proof file (`Selectionproof.v`). Covers optimizations (constant propagation, CSE, inlining, dead code elimination).

### Tier 2: `cfrontend/` proof files
Second most active. Proves correctness of translating C source into intermediate languages. Key files: `Cminorgenproof.v`, `Cshmgenproof.v`, `SimplLocalsproof.v`, `SimplExprproof.v`.

### Tier 3: Architecture backends
Each architecture (powerpc, arm, aarch64, riscV, x86) has ~18 `.v` files with identical structure. `Asmgenproof.v` is the big one per arch (54-63 commits). Good for cross-architecture "port this proof" challenges.

### Tier 4: `lib/` + `common/` — foundational
`Integers.v` (79 commits), `Events.v` (79 commits) are heavily modified. Changes here cascade through the entire project.

## Key Patterns for the Miner

1. **Naming convention is gold**: `*proof.v` always contains proofs for the corresponding pass. Strongest signal for pattern detection.

2. **Coq version migration commits are ideal eval candidates**: "Replace `omega` with `lia`", "Make proof script compatible with Coq 8.17" — changes proof scripts without changing theorem statements. ~222 Coq-related commits, ~306 mentioning specific tactic names.

3. **Proof work is often implicit in commit messages**: Many commits touching proof files describe the *feature* change, not the proof adaptation. Miner should detect proof file changes by file path, not commit message keywords.

4. **Commit messages are natural language, not formulaic**: No rigid prefix convention (no `feat:`, `fix:`, etc.). Sometimes in French (early history).

5. **Simulation diagrams are the core proof technique**: Forward simulations per pass, composed for end-to-end semantic preservation.

## Diff Analysis

Sampled ~60 commits across backend, cfrontend, architecture, and lib/common proof files. Each commit was categorized by change type.

### Change Category Definitions

| Category | Description |
|----------|-------------|
| **A) Tactic migration** | Replacing one tactic with another (omega->lia, Hint->Global Hint) without changing theorem statements |
| **B) Theorem statement change** | The Lemma/Theorem signature itself changed (new params, stronger/weaker conclusion) |
| **C) New lemma/theorem added** | Brand new proof obligation introduced |
| **D) Proof body rewrite** | Same theorem statement, but proof script substantially rewritten |
| **E) Definition change cascade** | A definition/type in the pass or upstream lib changed, requiring proof adaptation |
| **F) Structural refactor** | Reorganizing proof structure (splitting/merging lemmas, moving files, extracting helpers) |
| **G) Bug fix in proof** | Fixing an incorrect or broken proof |
| **H) Other** | Copyright headers, file moves, syntactic-only changes |

### Aggregate Tallies (across all ~60 sampled commits)

| Category | backend | cfrontend | arch | lib/common | Total |
|----------|---------|-----------|------|------------|-------|
| A) Tactic migration | 4 | 2 | 1 | 2 | **9** |
| B) Theorem stmt change | 4 | 2 | 2 | 2 | **10** |
| C) New lemma/theorem | 9 | 2 | 3 | 6 | **20** |
| D) Proof body rewrite | 17 | 2 | 2 | 2 | **23** |
| E) Def change cascade | 12 | 4 | 3 | 1 | **20** |
| F) Structural refactor | 6 | 1 | 3 | 0 | **10** |
| G) Bug fix in proof | 0 | 1 | 1 | 0 | **2** |
| H) Other | 2 | 0 | 0 | 4 | **6** |

Note: many commits fall into multiple categories.

### Key Finding: Proof body rewrites (D) and definition change cascades (E) dominate

The two most common proof change patterns are:
- **D) Proof body rewrite** (23 occurrences): Same theorem statement, different proof. This is the cleanest eval formulation: "given this theorem statement + context, produce a proof."
- **E) Definition change cascade** (20 occurrences): An upstream definition changed, forcing proof adaptation. Best for agent-style evals with a compilation oracle.

Bug fixes in proofs (G) are almost nonexistent (2 out of ~60), confirming CompCert's exceptional rigor -- proofs work correctly the first time.

### Eval Suitability Ranking

#### Tier 1 -- Best for evals (cleanest problem formulation, widest difficulty range)

**D) Proof body rewrite with unchanged statement.** The problem is: "Given this theorem statement and the surrounding context (definitions, imports, prior lemmas), produce a proof." Ground truth exists. Difficulty ranges from 1 line to 100+ lines. Examples:
- `02d3c953`: Cast optimization idempotence -- same statements, completely rewritten proofs
- `b6a2f5a9`: Helper return type changes from single step to star-of-steps, requiring `star_trans` throughout
- `65cc3738`: Boolean function replaces propositional one, requiring new destruction patterns
- `1abecb7b`: Maps implementation rewritten from `append`-based to `prev_append`-based

**C) New lemma/theorem added.** Especially self-contained additions to lib/common. Examples:
- `e8dc5a72`: 170 lines of new `eventually` closure theory in Smallstep.v
- `a0aaa355`: Two new eval theorems (`eval_singleoflong`, `eval_singleoflongu`)
- `91381b65`: New `of_intu_of_int_3` theorem in Floats.v (~40 lines)

#### Tier 2 -- Good for agent evals (require broader context)

**E) Definition change cascade.** Challenge: "An upstream definition changed; here is the old proof that no longer compiles; fix it." Requires understanding the transitive dependency chain. Best scoped as "fix one proof file given the definition change," not "fix everything."
- `ce495154`: Program representation changed, `match_globalenvs` must be restructured
- `17f51965`: 64-bit switch support cascades through 4 cfrontend proof files
- `7a6bb900`: External function names change from `ident` to `string`, ripples everywhere

**B+D combined.** Advanced challenges where both statement and proof need updating.
- `56579f8a`: Major refactor of Allocproof and Stackingproof (461 lines changed)
- `93b89122`: `exec_program`-style theorems replaced with `forward_simulation`-style

#### Tier 3 -- Limited eval value

**A) Tactic migration** splits into two sub-tiers:
- *Pure find-replace* (omega->lia, Hint->Global Hint): too mechanical, a regex could handle it
- *Requires understanding* (7 of 15 tactic commits): choosing the right auto database for each `intuition` call, deciding `Qed` vs `Defined`, adapting to changed tactic output. These are interesting but niche.

**F) Structural refactor** is hard to specify as a challenge because the target is ambiguous.

### Architecture-Specific Findings

- **Cross-architecture identical changes** (touching 4-5 archs with the same edit): too easy once you have one solution. Could be used as "given arch A solution, adapt to arch B" tasks.
- **Single-architecture substantive changes**: genuinely different per arch, good eval material. E.g., PowerPC float select (`e1055531`), ARM CombineOpproof (`132e36fa`).
- **Architecture ports** (e.g., `1f004665` ARM Int->Ptrofs): very large, systematic, better suited as agent tasks than single-shot challenges.

### lib/common Cascade Patterns

- 8 of 15 sampled lib/common commits are **standalone** (no downstream impact in same commit)
- 5 of 15 **cascade in the same commit** (lib change + downstream fixes bundled together)
- Changes to *definitions* cascade immediately; *new lemma additions* are standalone
- Best standalone eval material: `common/Smallstep.v`, `common/Values.v`, `lib/Floats.v`, `lib/Maps.v`

### The "Add a New Case to the Simulation" Pattern

A recurring and highly formulaic pattern: a new instruction/optimization is added to the compiler, and every pass's simulation proof needs a new case. Examples:
- `74487f07`: Jump tables added -- 9 proof files each get an `Ijumptable` case
- `93d2fc9e`: Trivial Icond elimination -- new case in deadcode simulation
- `078933ce`: `Mbool` memory chunk -- one new case in CSEproof and Inliningproof

This pattern is excellent eval template material because:
1. The challenge is well-defined: "add the missing case"
2. Difficulty varies by pass (some cases are 2 lines, others 20+)
3. Context is naturally bounded (the surrounding cases serve as examples)

### Tactic Migration Deep Dive

Of 15 tactic migration commits sampled:
- 3 pure find-replace (not useful for evals)
- 3 near-mechanical pattern-based (marginally useful)
- 7 require genuine understanding of Coq internals (moderately useful)
- 1 new tactic feature, 1 tactic bug fix (different eval type -- Ltac programming)

The "requires understanding" tier includes:
- `974fbd83`: Each `intuition` call needs a different auto database depending on what's in scope
- `808d0b03`: `Qed`->`Defined` requires knowing which proofs are used computationally
- `e9c13650`: `field_simplify` output changed, proof must be made robust to new output
