# Leanstral 1.5 baseline — 100-problem leaf sample

**38/91 scorable = 41%** (`mistral:labs-leanstral-1-5`, max-turns 50).

Sample: 2 problems from each of the 48 leaf-ablation repos with >=2 valid challenges;
dolev-yao has only 1; wadray_verification has 0. The 3-problem shortfall went to the repos
with the fewest remaining problems (SizzLean, verified-compiler, veil). 49 repos, 100 problems.

| outcome | n |
|---|--:|
| PASS | 38 |
| turn-limit | 44 |
| fail | 4 |
| tampered | 4 |
| gave-up | 1 |
| oversized | 9 |
| **scorable** | **91** |

Solves >=1 problem in **24/49 repos**.

## 42% is a lower bound, not a ceiling

**turn-limit (44) is the most common outcome — more common than passing.** The model is
usually still working when its request budget runs out, not producing confidently wrong
proofs. Raising the budget 30 -> 50 turns moved the rate 36% -> 42% and the curve has not
flattened; a 100-turn run would very likely score higher.

## Two scoring bugs found by this run

1. **Oversized challenges were scored as failures.** 9 problems (9%) exceed leanstral's
   262,144-token context *even after* `--shrink-solution-minimal`, so the provider rejects the
   prompt (HTTP 400) and the model never sees the problem. The harness recorded that as an
   ordinary miss and counted it in the PASS denominator. Now flagged `context_exceeded` and
   excluded, like `malformed`/`trivial`. Without the fix this run would report 38/100 = 38%,
   blaming the model for challenges it was never shown.
2. **Tampering rises with budget** (4 here, 2 at 30 turns): more turns means more chances to
   "solve" a problem by deleting or weakening the theorem it was asked to re-prove. The
   harness's tamper check keeps those out of the PASS column — an eval like this needs it.

## Per-repo

| repo | pass / scorable |
|---|--:|
| ArkLib | 2/2 |
| Clear | 2/2 |
| SizzLean | 2/3 |
| btc-verified | 2/2 |
| clean | 2/2 |
| formal-snarks-project | 2/2 |
| hax-proof-libs-lean | 2/2 |
| jolt-tla | 2/2 |
| lampe | 2/2 |
| nickelean | 2/2 |
| proven-zk | 2/2 |
| shortest-decimal | 2/2 |
| sparkle | 2/2 |
| veil | 2/2 |
| CvxLean | 1/2 |
| LNSym | 1/2 |
| LeroyCompilerVerificationCourse | 1/1 |
| TorchLean | 1/2 |
| aeneas | 1/2 |
| curve25519-dalek-lean-verify | 1/2 |
| evm-asm | 1/2 |
| lean-formal-reasoning-program | 1/2 |
| verity | 1/2 |
| yul-semantics | 1/2 |
| CompPoly | 0/2 |
| FLoPS | 0/1 |
| FVIntmax | 0/1 |
| Foundation | 0/2 |
| Lentil | 0/2 |
| SampCert | 0/2 |
| TTBFL | 0/2 |
| TensorLib | 0/2 |
| VCV-io | 0/2 |
| capless-lean | 0/2 |
| cedar-spec | 0/2 |
| cslib | 0/1 |
| dolev-yao | 0/1 |
| hex-dev | 0/2 |
| iris-lean | 0/2 |
| juvix-lean | 0/2 |
| lean-mlir | 0/2 |
| lean-yjs | 0/2 |
| lean-zip | 0/2 |
| lean4lean | 0/2 |
| loom | 0/2 |
| ryu-lean4 | 0/0 |
| splean | 0/1 |
| starkware-formal-proofs | 0/1 |
| verified-compiler | 0/3 |

Reproduce: `pipeline/eval_sample.sh <eval-dir> mistral:labs-leanstral-1-5 50 8`
