# Statement of Work

## Mike Dodds (Twitter)

> Someone should build seL4-ablate-bench. Progressively delete proofs, lemmas, theorems and see how much a long-running AI agent can reconstruct. End state: just give the AI the seL4 code + top spec, and re-synthesise the whole 1m+ line Isabelle proof. The seL4 proofs are OSS so they’re in the model training set. But even so, reconstructing 1m+ lines of proof is an insanely hard coordination challenge, far beyond current agents. And there’s no equivalent closed dataset at this scale. Fun exercise: predict what year an agent with sufficient scaffolding could reconstruct the entire seL4 proof. Estimates at Galois ranged from “2040” to “this year” :)

## QD's first gdoc:


An obvious instrument in the secure program synthesis (SPS) arsenal is formal methods. While previously prohibitively expensive due to the labor of the proof engineers, we now expect it to sink in cost due to AI driven proof synthesis (and it already has). 

One kinda silly bottleneck to the evals and RL envs that could push this forward faster is cultural— proof engineers from real world codebases like CompCert, SeL4, fiat-crypto, Nova, etc. don’t necessarily know what an eval is and why it's valuable to register their naturally-occurring data to inspect. I have an unfinished e-book trying to solve this cultural gap. 

This codebase, which I prototyped but didn’t finish, targets a specific proof engineering repo, the specs and proofs of Dalek25519 (a cryptographic primitive library that Signal the messaging app uses), currently underway by BAIF: https://github.com/Beneficial-AI-Foundation/git-history-proof-engineering-eval 
In it, I “mine” the git history to extract challenge problems from commit at time t, which have a ground truth in that they’re solved in the commit at time t+1 in many cases. In doing this (as you’ll see in the code), the hardcoded .git directory scraper makes some assumptions about patterns in commit messages and more generally the conventions with which git is used for collaboration. 

The proper swing at proof engineering evals via git histories would be an agentic miner/scraper, which dynamically finds those assumptions and patterns on the fly, so you have one scaffold and you drop any proof engineering codebase you please into it. 

This effort should also involve conducting baselines. 

### Deliverable 

Evals for at least the Nova hypervisor specs and proofs, SeL4, Compcert, and Fiat-Crypto registered to inspect and listed on huggingface. The generalized scaffold dynamically synthesizing “miner” scripts that walk across the git histories. Reporting baselines of how current language models do, which includes demonstration of how to download the data from huggingface and make a solver. Stretch goal: demonstrate actual posttraining on these eval-as-envs with open weight models. 

## Discussion in grant application

There’s a whole lot about the ceiling of this project that won’t get done with SPAR resources, but the key idea (liberating human-provenance proof engineering data for huggingface) should be able to hit roughly its ceiling with the resources we’re asking for. Specifically, that would mean “ablation studies” of several major proof engineering repos exposed to huggingface. 

In the long run, proofs are cheap. Having a proof oracle even a few months sooner than the default path could increase our security posture by proving critical infrastructure (including advanced AI training and deployment stacks) correct at crunch time. 

## Milestones

### [ ] June 18th ish

Preliminary huggingface MVPs for a couple of the repos. Scaffold repo is possibly not agentic yet, but written in a way that can turn agentic easily. 

### [ ] July 18th ish

Scaffold repo is agentic, repo and proofstack agnostic. Some way of attaching a slider to the generator of the dataset to ablate more or ablate less and export dataset on the fly. Reproducible pipeline where all tools are installed automatically (i.e. docker or nix). Rudimentary/preliminary baselines demonstration. Huggingface posts continually updated. 

### [ ] August 18th ish

Approximately conference tier writeup (not necessarily submitted or worried about specific peer review), with accompanying websites consisting of baseline information and lightweight “scaling laws” information. MIT licensed scaffold repo with method clear to reproduce for repos we did not ship to huggingface (including, in principle, sensitive/confidential repos). 

Since the goal is to get stolen by frontier companies, ideally we’d catch wind of some internal pilots happening on posttraining teams by now, but this shouldn’t be a hard KPI because if it happens it might not be right away and we might not hear about it. 
