# syntax=docker/dockerfile:1.7
#
# experiments/docker/commit.Dockerfile — per-SHA fiat-crypto layer on fc-deps.
#
# The built image's tag is managed by the orchestrator (see
# experiments/orchestrate/build-images.sh, issue #18) which passes a concrete
# ${COMMIT} and ${COQ_VERSION} per target SHA. This Dockerfile assumes the
# orchestrator has:
#   1. Already built fc-base:${COQ_VERSION} (#9) and fc-deps:${COQ_VERSION} (#12).
#   2. Written a `warm-targets.txt` file into the build context — one make
#      target per line. An empty file is fine; a missing file is a build error
#      (the COPY below fails), which is intentional: we want the orchestrator
#      to explicitly decide what to warm-cache, even if the decision is "none".
#
# Build context: experiments/docker/ (same as deps.Dockerfile). Example:
#
#   # orchestrator writes experiments/docker/warm-targets.txt first, then:
#   docker build \
#     --build-arg COQ_VERSION=8.20.0 \
#     --build-arg COMMIT=<sha> \
#     -f experiments/docker/commit.Dockerfile \
#     -t fc-commit:<sha> \
#     experiments/docker/
#
# Why `|| true` on `make deps` and on the warm-xargs step: both are cache
# warmers, not correctness gates. Some historical fiat-crypto SHAs have
# broken `deps` targets (submodule pinning drift, upstream API churn); we
# accept best-effort. The runner that actually evaluates challenges (issue
# #18 onward) is responsible for surfacing genuine build failures on the
# challenge files themselves, not on the ambient dependency graph.
#
# Why `git clone --filter=blob:none` then checkout (no explicit `git fetch`):
# a blob-filtered clone still fetches all commits and trees from every ref,
# so any SHA reachable from a remote branch (which is true for any commit
# landed on master or a release branch of fiat-crypto) is checkoutable in
# detached-HEAD state without a second fetch. If we ever need to pin SHAs
# that only exist on PR refs, add `git fetch origin ${COMMIT}` before the
# checkout.

ARG COQ_VERSION=8.20.0
ARG COMMIT
ARG FIAT_CRYPTO_REMOTE=https://github.com/mit-plv/fiat-crypto.git

FROM fc-deps:${COQ_VERSION}

# Re-declare build args needed after FROM (Dockerfile scoping rule).
ARG COMMIT
ARG FIAT_CRYPTO_REMOTE

# Inherit the unprivileged `coq` user from fc-base (fc-deps doesn't change it).
USER coq
WORKDIR /work

# Clone fiat-crypto at the requested SHA. Blob-filter keeps the clone light;
# commit/tree objects are fully present so any reachable SHA is checkoutable.
RUN git clone --filter=blob:none "${FIAT_CRYPTO_REMOTE}" /work/repo

# Detached-HEAD checkout of the target commit.
RUN git -C /work/repo checkout "${COMMIT}"

# Submodules are how fiat-crypto vendors coqutil/bedrock2/rupicola/rewriter;
# --recursive catches nested submodules (bedrock2 pulls coqutil, etc.).
RUN git -C /work/repo submodule update --init --recursive

# Best-effort build of the submodule deps graph. Tolerate failure — see header.
RUN make -C /work/repo -j"$(nproc)" deps || true

# Per-SHA warm-cache of the challenge files' transitive .vo deps. The build
# script (#18) computes the target list from coqdep output against the
# challenge files for this commit and writes it to warm-targets.txt. Missing
# file => intentional build failure (see header).
COPY warm-targets.txt /tmp/warm.txt

# -r: do nothing if warm.txt is empty. -k: keep going past per-target failures
# (one broken .vo shouldn't abort the warm of the rest). `|| true` at the end
# absorbs the xargs exit code when -k leaves some targets unbuilt — we still
# want the image to build.
RUN xargs -a /tmp/warm.txt -r make -j"$(nproc)" -k -C /work/repo || true

LABEL fiat_crypto_commit="${COMMIT}" \
      coq_version="${COQ_VERSION}"

# /results is the named volume the orchestrator mounts to collect per-SHA
# runner output (JSONL logs, compile reports).
VOLUME /results

# Default to an interactive shell; the orchestrator overrides with the
# runner entry point (issue #18+).
CMD ["bash"]
