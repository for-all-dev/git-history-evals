# Thoughts about Jason Gross's dissertation

Source: `docs/priorlit/jgross.pdf` (258 pages). PhD on performance engineering of proof-based software systems in Coq, with fiat-crypto as the primary macrobenchmark.

## Why this matters for our eval

We're building a deletion-addition loop where an ablated proof from the fiat-crypto git history is presented to a language model as a challenge. We need metrics to score whether the model's new proof is "better" or "worse" than the human-written one. Gross's dissertation is fundamentally about **performance**, so it gives us a sharp, computable axis that pure "did it typecheck" misses. fiat-crypto is his recurring macrobenchmark, so the framework transfers directly.

## The Four Axes (§2.6, the load-bearing framework)

Gross's central claim: most pathological proofs blow up along one of four axes. These translate directly into "better/worse" scoring:

1. **Size of the type** — node count of the goal type
2. **Size of the term** — node count of the proof term (`Print` + parse, or .vo size)
3. **Number of binders** — λ/Π depth in the proof term
4. **Nested abstraction barriers** — how often the kernel must unfold definitions to typecheck

Demonstration: a categorical proof goes from ~2s → ~254s (>100×) by piercing one extra abstraction barrier (p. 51–52).

> "most interesting performance bottlenecks scale as a superlinear factor of one or more of these axes" (p. 37)

> "In just three doublings of input size, we might go from tens of seconds to thousands of years... Our primary nontoy test example used four machine words and took just under a minute; the biggest realistic example we were targeting was twice that size, at eight machine words, and took about 20 hours." (p. 19, on fiat-crypto scaling)

## Tiered metrics for the scorer

### Tier 1 — cheap, high signal, automate now

- **Qed wall-clock time** — `time coqc` or per-`Qed` timing. Gross's primary diagnostic.
- **Proof term size** — bytes of `.vo` or AST node count from `Print`. Direct proxy for axis 2.
- **Proof script line count** — crude but useful baseline.

### Tier 2 — moderate effort, high signal

- **Binder count** in the proof term (axis 3).
- **`Qed` vs `Defined` discipline** — `Defined` leaks the term downstream and can blow up dependent proofs; using `Qed` where possible is a positive style signal (§1.3.2).
- **Typecheck-time / term-size ratio** — distinguishes "big but cheap" proofs from "small but pathological conversion" proofs.
- **Universe variable count** — Gross flags careless universe polymorphism as a 10× footgun (§8.2.1).

### Tier 3 — gold standard, requires instrumentation

- **Abstraction-barrier depth** — count `unfold`/δ-reductions during typecheck (axis 4). Needs Coq's profiler (§8.1.3) or manual `Opaque`/`Transparent` variants.
- **Subterm sharing ratio** — unique vs. total subterm occurrences (§4.4.2 "Subterm Sharing Is Crucial").
- **Reflective vs. tactic balance** — reflective proofs are dramatically faster on large goals but harder to debug (Ch. 3).

## fiat-crypto–specific notes

Fiat-crypto is Gross's macrobenchmark throughout. The pathology pattern relevant to our ablation eval: proofs that look fine on small word counts but scale superlinearly in machine-word count. So for a fair human-vs-model comparison, **don't just measure compile time on the ablated commit — measure the slope** if a parameterized version can be synthesized, or at least flag any proof whose Tier 1 numbers are >2× the human's as a regression even when it typechecks.

Root causes Gross identified in fiat-crypto specifically:
- Repeated binders in `conj` certificates (§2.6.1)
- Quadratic substitution in context-building (§2.6.3)
- Leaky abstraction barriers in the category-theory setup used for modular reasoning (§2.6.4)

## Failure modes to bake into the scorer

- Wall-clock time is noisy — run N times, report median.
- Term size doesn't see Coq's internal hash-consing — two proofs with the same `Print` output may have wildly different memory footprints.
- A model can "win" all four axes by using `admit`/`Axiom` — **always gate metrics on `Print Assumptions` being clean**.
- A shorter script using `vm_compute`/heavy reflection can be 10× faster but unreviewable; weight Tier 1 against a style/maintainability judgment if maintainability matters.

## What's missing from Gross for our purposes

Gross is silent on **semantic** quality (is this "the right" proof mathematically? does it generalize?) and on **maintainability** (how brittle under refactoring?). Those are real axes for our eval but we'll have to source them elsewhere — likely LLM-judge rubrics rather than Gross's framework.

## Recommendation for v1 of the scorer

Tier 1 metrics + `Print Assumptions` gate + a binary "uses `admit`/`Admitted`/new axioms" check. ~50 lines of Python wrapping `coqc`, giving a defensible better/worse signal grounded directly in Gross's framework.

## Key citations

- §2.6 "The Four Axes of the Landscape" (pp. 37–55) — the framework
- §1.2 "Our Work" (pp. 19–23) — fiat-crypto scaling pathology
- §4.5 "Evaluation" (pp. 92–99) — how Gross measures, including the fiat-crypto macrobenchmark
- §8.1.3 "The Ltac Profiler" (p. 164) — instrumentation hook for Tier 3
- §4.4.2 "Subterm Sharing Is Crucial" (p. 88)
