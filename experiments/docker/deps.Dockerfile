# syntax=docker/dockerfile:1.7
#
# experiments/docker/deps.Dockerfile — opam deps layer on top of fc-base.
#
# The built image's tag MUST be `fc-deps:${COQ_VERSION}` (tag enforced by the
# orchestrator, see experiments/orchestrate/build-images.sh). Per-commit images
# (#16, Tier 2) layer the actual fiat-crypto source and submodule-deps on top
# of this image; they do NOT re-install these heavy opam packages unless the
# commit needs a different version.
#
# Build context: experiments/docker/ (this directory). Example:
#
#   docker build \
#     --build-arg COQ_VERSION=8.20.0 \
#     -f experiments/docker/deps.Dockerfile \
#     -t fc-deps:8.20.0 \
#     experiments/docker/
#
# Requires `fc-base:${COQ_VERSION}` to already exist locally (built via
# experiments/docker/base.Dockerfile, issue #9).
#
# Why `|| true` on the opam install: old fiat-crypto commits may pin different
# versions of coq-bedrock2 / coq-rupicola / etc. than what's currently in the
# opam repo, so the install can fail for reasons specific to a snapshot in
# time. The per-commit layer (#16) rebuilds deps from fiat-crypto's bundled
# submodules anyway (`make deps`), so this layer is purely a best-effort cache
# warm — a successful install speeds up the common case, a failed install is
# recovered by the per-commit submodule build.

ARG COQ_VERSION=8.20.0
FROM fc-base:${COQ_VERSION}

# Match base.Dockerfile: run as the unprivileged `coq` user so opam writes
# into /home/coq/.opam (the switch the base image set up) rather than root's.
USER coq
WORKDIR /work

# Best-effort install of fiat-crypto's heavy opam dependencies. See header
# comment for why failures are tolerated.
RUN opam install -y \
      coq-coqprime \
      coq-bedrock2 \
      coq-coqutil \
      coq-rewriter \
      coq-rupicola \
 || true

# No CMD / ENTRYPOINT — inherited from fc-base (which also has none); the
# per-commit image and orchestrator runners supply those.
