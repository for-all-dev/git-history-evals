# Statement of Work

> **Status note (August 2026).** The method described in the original scope below —
> mining git histories for `(commit t, commit t+1)` challenge pairs — has been
> **superseded by syntactic proof ablation**. The original text is retained verbatim
> so the change is visible rather than hidden; see
> [Pivot: git-history mining → syntactic ablation](#pivot-august-2026-git-history-mining--syntactic-ablation)
> for the date, the rationale, the restated deliverable, and an honest account of the
> gap between what this document named as targets and what actually shipped.

## Mike Dodds (Twitter)

> Someone should build seL4-ablate-bench. Progressively delete proofs, lemmas, theorems and see how much a long-running AI agent can reconstruct. End state: just give the AI the seL4 code + top spec, and re-synthesise the whole 1m+ line Isabelle proof. The seL4 proofs are OSS so they’re in the model training set. But even so, reconstructing 1m+ lines of proof is an insanely hard coordination challenge, far beyond current agents. And there’s no equivalent closed dataset at this scale. Fun exercise: predict what year an agent with sufficient scaffolding could reconstruct the entire seL4 proof. Estimates at Galois ranged from “2040” to “this year” :)

## QD's first gdoc:


An obvious instrument in the secure program synthesis (SPS) arsenal is formal methods. While previously prohibitively expensive due to the labor of the proof engineers, we now expect it to sink in cost due to AI driven proof synthesis (and it already has). 

One kinda silly bottleneck to the evals and RL envs that could push this forward faster is cultural— proof engineers from real world codebases like CompCert, SeL4, fiat-crypto, Nova, etc. don’t necessarily know what an eval is and why it's valuable to register their naturally-occurring data to inspect. I have an unfinished e-book trying to solve this cultural gap. 

This codebase, which I prototyped but didn’t finish, targets a specific proof engineering repo, the specs and proofs of Dalek25519 (a cryptographic primitive library that Signal the messaging app uses), currently underway by BAIF: https://github.com/Beneficial-AI-Foundation/git-history-proof-engineering-eval 
In it, I “mine” the git history to extract challenge problems from commit at time t, which have a ground truth in that they’re solved in the commit at time t+1 in many cases. In doing this (as you’ll see in the code), the hardcoded .git directory scraper makes some assumptions about patterns in commit messages and more generally the conventions with which git is used for collaboration. 

The proper swing at proof engineering evals via git histories would be an agentic miner/scraper, which dynamically finds those assumptions and patterns on the fly, so you have one scaffold and you drop any proof engineering codebase you please into it. 

This effort should also involve conducting baselines. 

### Deliverable  *(superseded August 2026 — see the pivot section below)*

Evals for at least the Nova hypervisor specs and proofs, SeL4, Compcert, and Fiat-Crypto registered to inspect and listed on huggingface. The generalized scaffold dynamically synthesizing “miner” scripts that walk across the git histories. Reporting baselines of how current language models do, which includes demonstration of how to download the data from huggingface and make a solver. Stretch goal: demonstrate actual posttraining on these eval-as-envs with open weight models. 

## Discussion in grant application

There’s a whole lot about the ceiling of this project that won’t get done with SPAR resources, but the key idea (liberating human-provenance proof engineering data for huggingface) should be able to hit roughly its ceiling with the resources we’re asking for. Specifically, that would mean “ablation studies” of several major proof engineering repos exposed to huggingface. 

In the long run, proofs are cheap. Having a proof oracle even a few months sooner than the default path could increase our security posture by proving critical infrastructure (including advanced AI training and deployment stacks) correct at crunch time. 

## Pivot (August 2026): git-history mining → syntactic ablation

**Date of decision: August 2026.** As of this date the project no longer mines git
histories for challenges. Every dataset it produces comes from **syntactic proof
ablation**, and the git-history miner, its dashboard, and the per-commit replay
experiments have been removed from the tree. The previously published git-history cuts
(`for-all-dev/{fiat-crypto,CompCert,l4v}-eval`) are retired.

### Why

The mined datasets were inspected statically in June 2026 (`docs/dataset-issues.md`) and
found to be substantively broken as evals. Six issues were filed upstream as **#102–#107**:

- **#102** — `holes_filled` empty in 100% of sampled rows across all three datasets: the
  published schema predates per-hole metadata, so a consumer cannot even locate the hole
  without diffing challenge against solution.
- **#103** — most challenges contain no placeholder marker at all (~82% fiat-crypto, ~98%
  l4v, 100% of the CompCert sample), so they are not "fill the hole" tasks; the dataset
  card oversold them.
- **#104** — the *ground truth* still contains `Admitted`/`sorry` in ~11% of fiat-crypto
  and ~1.3% of l4v rows: the mined child commit did not actually close the goal, so the
  label is wrong.
- **#105** — degenerate new-file rows with an empty challenge: nothing to complete, only a
  commit message to author 3k–16k characters from.
- **#106** — no-op rows where challenge and solution are byte-identical.
- **#107** — whole-file challenges are intractable at scale: median l4v challenge ~139k
  characters (p90 ~322k), beyond any output-token budget; the whole-file baseline skipped
  100% of sampled l4v challenges as `TOO_LARGE`.

Common root cause: a commit diff is a *noisy, unvalidated* proxy for "here is a proof
problem and here is its answer." Nothing in the mining pipeline could check that the
challenge was well-posed or that the solution compiled, because doing so requires
building the repo at that commit — which is the expensive part the mining approach was
meant to avoid.

Ablation inverts this. The challenge is *constructed* by deleting a lemma from a
compiling file, so the hole location is known exactly, the ground truth is by
construction a proof that compiles, no-op and empty challenges are impossible (the
ablators refuse to emit a record when nothing was deleted), and the corollary-closure
slice keeps challenges small instead of whole-file. Both sides are then really compiled
before publication; anything that fails is marked `malformed` and excluded. Ablation is
also more contamination-resistant: the deletion is drawn from a seeded random choice over
a dependency closure, so the exact (challenge, solution) pair is not text that exists
anywhere upstream.

### Restated deliverable

Compile-validated **ablation** evals over real proof-engineering repos, published to
HuggingFace as `for-all-dev/ablation-eval` (easy/hard splits), together with:

- three ablators (Lean, Rocq/Coq, Isabelle) sharing one record schema, each also compiled
  to WASM and exposed through a browser playground, so any proof engineer can drop their
  own repo in — including a private one, since the ablator runs locally;
- a reproducible nix pipeline (mine → build → validate → index → publish → eval) with a
  pinned corpus (`pipeline/repos.tsv`) so every published row traces to an exact tree;
- a prover-agnostic agentic baseline harness (`ablate-baseline`) scored by real
  compilation, with reported baselines for current language models;
- an ablation *slider* (which lemma, how many, leaves vs. whole proof body) so the
  difficulty of the generated dataset is a knob rather than an accident of history.

Stretch goal (unchanged): post-training on these evals-as-envs with open-weight models.

### Target vs. delivered — the honest gap

This document named **Nova, seL4, CompCert, and fiat-crypto**. That is not what shipped.

| named target | status |
|---|---|
| Nova hypervisor | **not attempted.** No Nova eval exists. |
| seL4 (l4v, Isabelle) | **partial.** l4v is mined (19,018 challenges) but only partially *validated*: the `ExecSpec`/`ASpec` sessions require `spec/design/*.thy`, which are generated from seL4's Haskell model and absent from a plain checkout. Until that generation runs, only sessions below the design spec (Lib, Word_Lib, Monads, …) validate — and **14,594 of the 19,018 challenges sit under `Refine`**, which needs it. The flagship refinement proof is therefore not yet in a published, compile-validated cut. |
| CompCert (Coq) | **not mined under ablation.** The checkout is present; the Rocq ablator exists and passes its own tests; the corpus has not been mined and validated. |
| fiat-crypto (Coq) | **not mined under ablation.** Same as CompCert. The retired git-history cut is withdrawn, so there is currently no fiat-crypto dataset. |
| *(not named in the SoW)* | **57 Lean repos**, mined in both modes, compile-validated, and published as `for-all-dev/ablation-eval`. This is the bulk of what shipped. |

So the delivered corpus is broader than the SoW in repo count and narrower in prover
coverage: strong on Lean, partial on Isabelle, empty on Rocq/Coq under the new method.
The Lean corpus was where the pivot could be validated end-to-end fastest (a uniform
toolchain, elan/lake, and repos that build in minutes rather than hours), and building it
out was prioritised over reproducing the named Coq targets. Closing the Rocq gap and
unblocking l4v's `Refine` sessions are the outstanding items against the original scope.

## Milestones

### [x] June 18th ish

Preliminary huggingface MVPs for a couple of the repos. Scaffold repo is possibly not agentic yet, but written in a way that can turn agentic easily. 

### [ ] July 18th ish

Scaffold repo is agentic, repo and proofstack agnostic (done). Expanding to Isabelle's AFP, with nonmath repos as priority. Some way of attaching a slider to the generator of the dataset to ablate more or ablate less and export dataset on the fly — with symbolic ablations and perturbations as a more contamination-proof alternative to git-history deletion. Reproducible pipeline where all tools are installed automatically (i.e. docker or nix). Rudimentary/preliminary baselines demonstration. Huggingface posts continually updated.

*(The "scaffold repo is agentic" criterion refers to the git-history miner and is
superseded — the agentic calibration miner has been removed. Its successor criterion is
"the ablators are repo- and proofstack-agnostic and need no per-repo calibration", which
holds: ablation is purely syntactic, so a new repo needs no profile, only a build.
The "slider" and "reproducible nix pipeline" criteria carried over intact and are met.)*

### [ ] August 18th ish

Approximately conference tier writeup (not necessarily submitted or worried about specific peer review), with accompanying websites consisting of baseline information and lightweight “scaling laws” information. MIT licensed scaffold repo with method clear to reproduce for repos we did not ship to huggingface (including, in principle, sensitive/confidential repos). 

Since the goal is to get stolen by frontier companies, ideally we’d catch wind of some internal pilots happening on posttraining teams by now, but this shouldn’t be a hard KPI because if it happens it might not be right away and we might not hear about it. 

# Changelog

**August 2026 — method pivot:**
- Retired git-history mining in favour of syntactic proof ablation (rationale and
  target-vs-delivered gap in the pivot section above; underlying defects filed as #102–#107)
- Removed the miner, dashboard, and per-commit replay experiments from the tree
- Withdrew `for-all-dev/{fiat-crypto,CompCert,l4v}-eval`; `for-all-dev/ablation-eval` is the
  single published dataset
- 57 Lean repos mined and compile-validated in both ablation modes

**June 22 Checkin notes:**
- Posted three benches on HuggingFace
- Scaffold is already language and repo agnostic (achieved milestone 2 criterion early)
- Limited baselines information gathered (open question: how important is it to get signal on this going forward?)
- Galois is considering using the scaffold on private repos with kestrel and kry10 to streamline their work
- Updated July 18th milestone to incorporate: AFP expansion, symbolic ablations/perturbations as contamination-proof alternative

