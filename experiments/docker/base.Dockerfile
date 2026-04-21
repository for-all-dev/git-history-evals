# syntax=docker/dockerfile:1.7
#
# experiments/docker/base.Dockerfile — reusable base image: Coq + uv + tooling.
#
# Built as `fc-base:${COQ_VERSION}` (tag enforced by the orchestrator, see
# experiments/orchestrate/build-images.sh, issue #18). Per-commit images layer
# the actual fiat-crypto source on top of this base; they do NOT re-install
# Coq or Python deps.
#
# Build context: repo root (so `experiments/pyproject.toml` + `experiments/uv.lock`
# are reachable via the COPY below). Example:
#
#   docker build \
#     --build-arg COQ_VERSION=8.20.0 \
#     -f experiments/docker/base.Dockerfile \
#     -t fc-base:8.20.0 \
#     .
#
# Python deps are managed by `uv`, which we pull from the official distroless
# image `ghcr.io/astral-sh/uv:latest` (chosen over the curl|sh install script
# because the image is already signed+pinned by GHCR and avoids a network
# fetch at build time). `uv sync --locked --no-install-project --no-dev`
# builds the venv from pyproject.toml + uv.lock ONLY, so the Python layer
# cache stays hot across source-only changes.
#
# A full `docker build` of this file requires `experiments/pyproject.toml`
# and `experiments/uv.lock` to exist (delivered by issue #22). Until #22
# merges, only the pre-COPY stages will succeed.

ARG COQ_VERSION=8.20.0
FROM coqorg/coq:${COQ_VERSION}

USER root

# System tooling: git for submodules, make for Coq builds, jq for JSONL
# wrangling in orchestrator scripts, ca-certificates+curl for HTTPS fetches.
# No python3/python3-venv — uv manages its own Python.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git \
      make \
      jq \
      ca-certificates \
      curl \
 && rm -rf /var/lib/apt/lists/*

# Install uv from the official distroless image (see header comment).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Make Coq tools available on PATH without needing `opam init` (mirrors the
# root Dockerfile lines 21-25, extended to `-sf` for idempotent rebuilds).
RUN COQBIN=$(dirname "$(find /home/coq/.opam -name coqc -type f | head -1)") && \
    ln -sf "$COQBIN/coqc"         /usr/local/bin/coqc         && \
    ln -sf "$COQBIN/coqtop"       /usr/local/bin/coqtop       && \
    ln -sf "$COQBIN/coq_makefile" /usr/local/bin/coq_makefile && \
    ln -sf "$COQBIN/coqdep"       /usr/local/bin/coqdep

# uv environment — copy-link for bind-mount compatibility, bytecode-compile
# eagerly so first-run startup isn't hit by .pyc generation, and pin the
# venv location so subsequent `uv run` invocations (and PATH) find it.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    VIRTUAL_ENV=/work/experiments/.venv
ENV PATH="/work/experiments/.venv/bin:${PATH}"

# Copy ONLY the lockfile + manifest (not the source) so the venv layer is
# cached across source changes. Source is layered on per-commit images.
COPY experiments/pyproject.toml experiments/uv.lock /work/experiments/

# Install deps into the venv. --no-install-project: don't install the
# experiments package itself (source isn't here). --no-dev: skip dev deps.
# --locked: fail if uv.lock is stale rather than silently resolving.
RUN cd /work/experiments && \
    uv sync --locked --no-install-project --no-dev

# Drop back to the unprivileged coq user before any runtime.
USER coq
WORKDIR /work

# No CMD / ENTRYPOINT — per-commit images and the orchestrator's runners
# supply those.
