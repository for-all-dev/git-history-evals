#!/usr/bin/env bash
# experiments/orchestrate/build-images.sh
#
# Idempotent layered Docker builder for the per-commit fiat-crypto eval
# pipeline (epic #27 / issue #18).
#
# Three image layers, tagged as:
#
#   fc-base:<coq>       from experiments/docker/base.Dockerfile       (#9)
#   fc-deps:<coq>       from experiments/docker/deps.Dockerfile       (#12)
#   fc-commit:<sha12>   from experiments/docker/commit.Dockerfile     (#16)
#
# The --all workflow walks every unique commit_hash discovered by
# commits_from_meta (from lib.sh), detects the required Coq version per SHA
# via detect_coq_version.py, builds base+deps once per unique Coq version,
# then builds one fc-commit per SHA in parallel up to --max-parallel.
#
# Skip behavior: if `docker image inspect <tag>` succeeds, we log
# "skipping <tag> (exists)" and move on.
#
# Build context layout (per-SHA): we stage a tmpdir under $TMPDIR, write a
# freshly generated warm-targets.txt (one <file>.vo per meta.json file_path
# whose commit_hash matches), copy commit.Dockerfile in, and point
# `docker build` at that tmpdir. The tmpdir is always removed via a trap.

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENTS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${EXPERIMENTS_ROOT}/.." && pwd)"
DOCKER_DIR="${EXPERIMENTS_ROOT}/docker"

BASE_DOCKERFILE="${DOCKER_DIR}/base.Dockerfile"
DEPS_DOCKERFILE="${DOCKER_DIR}/deps.Dockerfile"
COMMIT_DOCKERFILE="${DOCKER_DIR}/commit.Dockerfile"

DETECT_COQ_VERSION="${SCRIPT_DIR}/detect_coq_version.py"
LIB_SH="${SCRIPT_DIR}/lib.sh"

# Fiat-crypto checkout used by detect_coq_version.py (for `git show <sha>:<path>`).
# Overridable via env; defaults to the submodule path used elsewhere in this repo.
FIAT_CRYPTO_REPO="${FIAT_CRYPTO_REPO:-${REPO_ROOT}/data/fiat-crypto}"

# ---------------------------------------------------------------------------
# Defaults / globals
# ---------------------------------------------------------------------------
MAX_PARALLEL=4
DRY_RUN=0
SUBCOMMAND=""
SUBCOMMAND_ARG=""

# Tmpdirs we've spawned for per-commit build contexts; trap cleans them up.
declare -a _TMPDIRS=()

# ---------------------------------------------------------------------------
# Logging helpers (everything to stderr per the spec)
# ---------------------------------------------------------------------------
log()  { printf '[build-images] %s\n' "$*" >&2; }
warn() { printf '[build-images] WARN: %s\n' "$*" >&2; }
die()  { printf '[build-images] ERROR: %s\n' "$*" >&2; exit 1; }

cleanup() {
  local d
  if [ "${#_TMPDIRS[@]}" -eq 0 ]; then
    return 0
  fi
  for d in "${_TMPDIRS[@]}"; do
    if [ -n "${d}" ] && [ -d "${d}" ]; then
      rm -rf -- "${d}"
    fi
  done
  return 0
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage: build-images.sh [OPTIONS] [--all | --base <coq> | --deps <coq> | --commit <sha>]

Subcommands (mutually exclusive; default is --all):
  --all                 Build every layer implied by the meta.json commit list.
  --base    <coq>       Build fc-base:<coq> only.
  --deps    <coq>       Build fc-deps:<coq> only (requires fc-base:<coq>).
  --commit  <sha>       Build fc-commit:<sha-prefix> only (requires fc-deps).

Options:
  --max-parallel N      Parallel per-commit builds (default: 4).
  --dry-run             Log the docker build commands without executing them.
  -h, --help            Show this help and exit.

Environment:
  FIAT_CRYPTO_REPO      Path to the fiat-crypto checkout detect_coq_version.py
                        reads from. Default: <repo>/data/fiat-crypto.
EOF
}

# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

# Pick docker buildx if available, else plain docker build. Echoes the
# command prefix as a single token-split-safe string via printf %q.
docker_build_cmd() {
  if command -v docker >/dev/null 2>&1; then
    if docker buildx version >/dev/null 2>&1; then
      printf 'docker buildx build --load'
      return 0
    fi
    printf 'docker build'
    return 0
  fi
  die "docker is required but not found on PATH"
}

image_exists() {
  local tag="$1"
  docker image inspect "${tag}" >/dev/null 2>&1
}

# Run (or log, if DRY_RUN) a docker build invocation.
#   $1: tag
#   $2: dockerfile path
#   $3: build context path
#   $@: extra args (typically --build-arg ...)
run_docker_build() {
  local tag="$1"; shift
  local dockerfile="$1"; shift
  local context="$1"; shift

  local cmd
  cmd="$(docker_build_cmd)"

  # Assemble as an array so quoting of extra args is exact.
  local -a full=()
  # shellcheck disable=SC2206  # word-splitting of cmd is intentional
  full=( ${cmd} -f "${dockerfile}" -t "${tag}" "$@" "${context}" )

  if [ "${DRY_RUN}" -eq 1 ]; then
    log "DRY-RUN: ${full[*]}"
    return 0
  fi

  log "building ${tag}"
  if ! "${full[@]}" >&2; then
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Layer builders
# ---------------------------------------------------------------------------

build_base() {
  local coq="$1"
  local tag="fc-base:${coq}"
  [ -z "${coq}" ] && die "build_base: missing coq version"

  if [ "${DRY_RUN}" -eq 0 ] && image_exists "${tag}"; then
    log "skipping ${tag} (exists)"
    return 0
  fi

  # base.Dockerfile's build context is the repo root (needs experiments/ dir).
  run_docker_build "${tag}" "${BASE_DOCKERFILE}" "${REPO_ROOT}" \
    --build-arg "COQ_VERSION=${coq}"
}

build_deps() {
  local coq="$1"
  local tag="fc-deps:${coq}"
  [ -z "${coq}" ] && die "build_deps: missing coq version"

  if [ "${DRY_RUN}" -eq 0 ] && image_exists "${tag}"; then
    log "skipping ${tag} (exists)"
    return 0
  fi

  # deps.Dockerfile's build context is experiments/docker/ — it only needs
  # the Dockerfile itself; nothing is COPYed in.
  run_docker_build "${tag}" "${DEPS_DOCKERFILE}" "${DOCKER_DIR}" \
    --build-arg "COQ_VERSION=${coq}"
}

# commits_from_meta / detect_coq_version.py both use full SHAs; the per-commit
# image tag takes the first 12 chars for readability.
sha_prefix() {
  local sha="$1"
  printf '%s' "${sha:0:12}"
}

# Emit the warm-targets list for a SHA to stdout. Each meta.json whose
# commit_hash equals <sha> contributes one line `<file_path>.vo`. Duplicates
# (same file_path across both conditions) are dropped.
warm_targets_for_commit() {
  local sha="$1"
  local meta commit_hash file_path
  local -a metas=()

  for meta in \
    "${EXPERIMENTS_ROOT}/admitted-proofs"/*/meta.json \
    "${EXPERIMENTS_ROOT}/experiments3"/*/meta.json; do
    [ -f "${meta}" ] || continue
    metas+=("${meta}")
  done
  if [ "${#metas[@]}" -eq 0 ]; then
    return 0
  fi

  # Pull (commit_hash, file_path) pairs with jq, filter by sha, map to .vo.
  jq -r '[.commit_hash, .file_path] | @tsv' "${metas[@]}" \
    | while IFS=$'\t' read -r commit_hash file_path; do
        [ "${commit_hash}" = "${sha}" ] || continue
        [ -n "${file_path}" ] || continue
        # Strip any trailing .v so we don't end up with .v.vo.
        case "${file_path}" in
          *.v) printf '%s\n' "${file_path%.v}.vo" ;;
          *)   printf '%s.vo\n' "${file_path}" ;;
        esac
      done \
    | sort -u
}

build_commit() {
  local sha="$1"
  local coq="${2:-}"

  [ -z "${sha}" ] && die "build_commit: missing sha"
  if [ -z "${coq}" ]; then
    if [ -n "${BUILD_IMAGES_COQ_OVERRIDE:-}" ]; then
      coq="${BUILD_IMAGES_COQ_OVERRIDE}"
    else
      coq="$(detect_coq_version "${sha}")" \
        || die "build_commit: failed to detect coq version for ${sha}"
    fi
  fi

  local prefix tag
  prefix="$(sha_prefix "${sha}")"
  tag="fc-commit:${prefix}"

  if [ "${DRY_RUN}" -eq 0 ] && image_exists "${tag}"; then
    log "skipping ${tag} (exists)"
    return 0
  fi

  # Build context: a tmpdir containing the commit.Dockerfile and a fresh
  # warm-targets.txt. commit.Dockerfile's COPY step requires warm-targets.txt
  # to exist; an empty file is acceptable.
  local ctx
  ctx="$(mktemp -d "${TMPDIR:-/tmp}/fc-ctx-${prefix}.XXXXXX")"
  _TMPDIRS+=("${ctx}")

  cp "${COMMIT_DOCKERFILE}" "${ctx}/Dockerfile"
  warm_targets_for_commit "${sha}" > "${ctx}/warm-targets.txt"

  log "commit ${prefix}: coq=${coq} warm-targets=$(wc -l < "${ctx}/warm-targets.txt" | tr -d ' ')"

  run_docker_build "${tag}" "${ctx}/Dockerfile" "${ctx}" \
    --build-arg "COQ_VERSION=${coq}" \
    --build-arg "COMMIT=${sha}"
}

# ---------------------------------------------------------------------------
# Coq-version detection wrapper
# ---------------------------------------------------------------------------

# Invoke detect_coq_version.py for <sha>; echo the resolved version to stdout.
detect_coq_version() {
  local sha="$1"
  if [ ! -x "${DETECT_COQ_VERSION}" ] && [ ! -f "${DETECT_COQ_VERSION}" ]; then
    die "detect_coq_version.py not found at ${DETECT_COQ_VERSION}"
  fi
  if [ ! -d "${FIAT_CRYPTO_REPO}" ]; then
    die "FIAT_CRYPTO_REPO not a directory: ${FIAT_CRYPTO_REPO}"
  fi
  python3 "${DETECT_COQ_VERSION}" "${FIAT_CRYPTO_REPO}" "${sha}"
}

# ---------------------------------------------------------------------------
# --all orchestration
# ---------------------------------------------------------------------------

# Run all phases for every SHA produced by commits_from_meta.
# Phases 1-2 (base/deps) are serialized per Coq version.
# Phase 3 (per-commit) is parallelized up to MAX_PARALLEL via background jobs.
run_all() {
  # Source lib.sh lazily so --help / --base / --commit don't pay the jq check.
  # shellcheck source=./lib.sh
  . "${LIB_SH}"

  # SHAS: array of all unique commit hashes from meta.json files.
  local -a SHAS=()
  if [ -n "${BUILD_IMAGES_SHAS_OVERRIDE:-}" ]; then
    # Test hook: space/newline separated SHAs injected by the test harness.
    # Using read -r -a to preserve portability.
    # shellcheck disable=SC2206
    SHAS=( ${BUILD_IMAGES_SHAS_OVERRIDE} )
  else
    while IFS= read -r line; do
      [ -n "${line}" ] && SHAS+=("${line}")
    done < <(commits_from_meta)
  fi

  if [ "${#SHAS[@]}" -eq 0 ]; then
    warn "no SHAs discovered via commits_from_meta; nothing to do"
    return 0
  fi
  log "discovered ${#SHAS[@]} unique SHA(s)"

  # Build the SHA -> Coq version map. Parallel keys: the `COQ_VERSIONS` assoc.
  declare -A COQ_VERSIONS=()
  local sha coq
  for sha in "${SHAS[@]}"; do
    if [ -n "${BUILD_IMAGES_COQ_OVERRIDE:-}" ]; then
      coq="${BUILD_IMAGES_COQ_OVERRIDE}"
    else
      if ! coq="$(detect_coq_version "${sha}")"; then
        warn "coq-version detection failed for ${sha}; skipping"
        continue
      fi
    fi
    COQ_VERSIONS["${sha}"]="${coq}"
  done

  # Unique Coq versions across all detected SHAs.
  local -a UNIQUE_COQ=()
  local v
  declare -A _seen=()
  for sha in "${!COQ_VERSIONS[@]}"; do
    v="${COQ_VERSIONS[${sha}]}"
    if [ -z "${_seen[${v}]:-}" ]; then
      _seen["${v}"]=1
      UNIQUE_COQ+=("${v}")
    fi
  done

  log "unique coq versions: ${UNIQUE_COQ[*]:-(none)}"

  # Phase 1 + 2: base & deps, serialized per Coq version.
  local rc=0
  for v in "${UNIQUE_COQ[@]}"; do
    if ! build_base "${v}"; then
      warn "fc-base:${v} build failed"; rc=1; continue
    fi
    if ! build_deps "${v}"; then
      warn "fc-deps:${v} build failed"; rc=1; continue
    fi
  done

  # Phase 3: per-commit builds, parallelized.
  local -a pids=()
  local active=0
  local phase3_rc=0
  for sha in "${SHAS[@]}"; do
    coq="${COQ_VERSIONS[${sha}]:-}"
    if [ -z "${coq}" ]; then
      warn "no coq version for ${sha}; skipping commit build"
      phase3_rc=1
      continue
    fi

    # Throttle to MAX_PARALLEL concurrent builds.
    while [ "${active}" -ge "${MAX_PARALLEL}" ]; do
      if wait -n 2>/dev/null; then
        :
      else
        # wait -n returned nonzero => one child failed
        phase3_rc=1
      fi
      active=$((active - 1))
    done

    # Spawn background build (dry-run & sequential path also works fine here).
    (
      build_commit "${sha}" "${coq}"
    ) &
    pids+=("$!")
    active=$((active + 1))
  done

  # Drain remaining children.
  local p
  for p in "${pids[@]}"; do
    if ! wait "${p}"; then
      phase3_rc=1
    fi
  done

  if [ "${phase3_rc}" -ne 0 ]; then
    rc=1
  fi
  return "${rc}"
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parse_args() {
  if [ "$#" -eq 0 ]; then
    SUBCOMMAND="--all"
    return 0
  fi

  while [ "$#" -gt 0 ]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      --max-parallel)
        [ "$#" -ge 2 ] || die "--max-parallel requires an integer argument"
        MAX_PARALLEL="$2"
        case "${MAX_PARALLEL}" in
          ''|*[!0-9]*) die "--max-parallel must be a positive integer" ;;
        esac
        [ "${MAX_PARALLEL}" -ge 1 ] || die "--max-parallel must be >= 1"
        shift 2
        ;;
      --all)
        SUBCOMMAND="--all"
        shift
        ;;
      --base|--deps|--commit)
        [ -z "${SUBCOMMAND}" ] || die "subcommand already set to ${SUBCOMMAND}; cannot also use $1"
        SUBCOMMAND="$1"
        [ "$#" -ge 2 ] || die "$1 requires an argument"
        SUBCOMMAND_ARG="$2"
        shift 2
        ;;
      *)
        die "unknown argument: $1 (try --help)"
        ;;
    esac
  done

  [ -n "${SUBCOMMAND}" ] || SUBCOMMAND="--all"
}

main() {
  parse_args "$@"

  case "${SUBCOMMAND}" in
    --all)
      run_all
      ;;
    --base)
      build_base "${SUBCOMMAND_ARG}"
      ;;
    --deps)
      build_deps "${SUBCOMMAND_ARG}"
      ;;
    --commit)
      build_commit "${SUBCOMMAND_ARG}"
      ;;
    *)
      die "internal: unknown subcommand ${SUBCOMMAND}"
      ;;
  esac
}

main "$@"
