#!/usr/bin/env bash
# experiments/orchestrate/run-commit.sh
#
# Per-SHA launcher for the fiat-crypto proof-evaluation pipeline (epic #27 /
# issue #19). Wraps `docker compose run --rm` against a single service in a
# compose.yml emitted by gen-compose.py (#23).
#
# Usage:
#   ./run-commit.sh <sha> <run_id> [--compose-file <path>] [--dry-run]
#
# Defaults:
#   --compose-file experiments/results/<run_id>/compose.yml
#     (matches what gen-compose.py writes by default; the issue body says
#     results-aggregate/<run_id>/compose.yml but gen-compose.py writes to
#     experiments/results/<run_id>/compose.yml — we follow the generator.)
#
# Behavior:
#   1. Computes sha_prefix = first 8 chars of <sha>.
#   2. Asserts <compose-file> exists and contains a `cmt-<sha_prefix>:`
#      service; fails with a helpful error otherwise.
#   3. Runs
#        docker compose -f <compose-file> run --rm \
#          --name fc-run-<sha_prefix>-<run_id> \
#          cmt-<sha_prefix>
#      and propagates the container exit code.
#   4. On container failure, leaves the named volume `results-<sha_prefix>`
#      intact (the compose file declares it with a stable `name:`; we do NOT
#      issue `docker volume rm` anywhere).
#   5. On container success, inspects the volume via a one-shot alpine
#      container and prints
#        DONE <sha> agent_n=X baseline_n=Y
#      where X/Y are line counts of /results/<run_id>/{agent,baseline}.jsonl
#      inside the volume (0 if the file is missing — e.g. mode=baseline-only).
#
# --dry-run: logs the docker compose invocation instead of executing, exits 0.

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENTS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${EXPERIMENTS_ROOT}/.." && pwd)"

# ---------------------------------------------------------------------------
# Defaults / globals
# ---------------------------------------------------------------------------
SHA=""
RUN_ID=""
COMPOSE_FILE=""
DRY_RUN=0

# Image used to introspect the named volume post-run. Kept small & hermetic.
SUMMARY_IMAGE="${RUN_COMMIT_SUMMARY_IMAGE:-alpine:3.19}"

# ---------------------------------------------------------------------------
# Logging helpers (stderr; matches build-images.sh convention)
# ---------------------------------------------------------------------------
log()  { printf '[run-commit] %s\n' "$*" >&2; }
warn() { printf '[run-commit] WARN: %s\n' "$*" >&2; }
die()  { printf '[run-commit] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: run-commit.sh <sha> <run_id> [OPTIONS]

Arguments:
  <sha>                   Fiat-crypto commit SHA (full or >=8 chars). The
                          first 8 chars form the sha-prefix used for the
                          compose service name (cmt-<sha-prefix>), container
                          name (fc-run-<sha-prefix>-<run_id>), and volume
                          name (results-<sha-prefix>).
  <run_id>                Run identifier. Used in the compose file path
                          (experiments/results/<run_id>/compose.yml by
                          default), the container name, and — inside the
                          container — the results subdirectory.

Options:
  --compose-file <path>   Path to the compose file to run against.
                          Default: experiments/results/<run_id>/compose.yml
                          (resolved relative to the repo root).
  --dry-run               Log the `docker compose run ...` command that would
                          be issued, without executing it or the post-run
                          summary step. Exits 0 after pre-flight checks.
  -h, --help              Show this help and exit.

Environment:
  RUN_COMMIT_SUMMARY_IMAGE  Image used to read the named volume for the DONE
                            summary. Default: alpine:3.19.

Exit codes:
  0  container ran to completion (or --dry-run succeeded)
  1  container ran but exited non-zero (bubbled up from docker compose run)
  2  usage / pre-flight error (missing arg, compose file not found, service
     not declared in compose file)
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
  local -a positionals=()

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
      --compose-file)
        [ "$#" -ge 2 ] || { usage >&2; die "--compose-file requires a path argument"; }
        COMPOSE_FILE="$2"
        shift 2
        ;;
      --)
        shift
        while [ "$#" -gt 0 ]; do
          positionals+=("$1")
          shift
        done
        ;;
      -*)
        usage >&2
        die "unknown option: $1"
        ;;
      *)
        positionals+=("$1")
        shift
        ;;
    esac
  done

  if [ "${#positionals[@]}" -ne 2 ]; then
    usage >&2
    die "expected exactly 2 positional arguments (<sha> <run_id>); got ${#positionals[@]}"
  fi

  SHA="${positionals[0]}"
  RUN_ID="${positionals[1]}"

  if [ -z "${SHA}" ]; then
    die "<sha> must be non-empty"
  fi
  if [ "${#SHA}" -lt 8 ]; then
    die "<sha> must be at least 8 characters (got ${#SHA}: ${SHA})"
  fi
  if [ -z "${RUN_ID}" ]; then
    die "<run_id> must be non-empty"
  fi
  # run_id becomes a path fragment and a container name suffix; restrict it
  # to a conservative charset to avoid both filesystem and docker name woes.
  case "${RUN_ID}" in
    *[!A-Za-z0-9._-]*)
      die "<run_id> may only contain [A-Za-z0-9._-]; got '${RUN_ID}'"
      ;;
  esac

  if [ -z "${COMPOSE_FILE}" ]; then
    COMPOSE_FILE="${REPO_ROOT}/experiments/results/${RUN_ID}/compose.yml"
  fi
}

# ---------------------------------------------------------------------------
# Pre-flight: verify compose file exists and declares the target service.
# ---------------------------------------------------------------------------
# $1: compose file path
# $2: expected service name (cmt-<sha_prefix>)
assert_compose_has_service() {
  local compose_file="$1"
  local service="$2"

  if [ ! -f "${compose_file}" ]; then
    die "compose file not found: ${compose_file}
(hint: run experiments/orchestrate/gen-compose.py --run-id <id> --mode <m> --shas <shas> first,
 or pass --compose-file <path> explicitly)"
  fi

  # gen-compose.py emits service keys as `  <name>:` at two-space indent under
  # `services:`. Match that exact prefix to avoid collisions with volume keys
  # (e.g. `  results-abc12345:`), which live under `volumes:` with the same
  # indent but a different name.
  if ! grep -Eq "^[[:space:]]{2}${service}:[[:space:]]*(#.*)?\$" "${compose_file}"; then
    die "compose file ${compose_file} does not declare a '${service}:' service
(hint: confirm the SHA prefix matches one that gen-compose.py was invoked with;
 first 8 chars of the <sha> arg form the service name)"
  fi
}

# ---------------------------------------------------------------------------
# Volume introspection: read JSONL line counts for the DONE summary.
# ---------------------------------------------------------------------------
# $1: volume name (results-<sha_prefix>)
# $2: run id
#
# Echoes two lines on stdout:
#   agent_n=<N>
#   baseline_n=<M>
# Missing files produce a 0 count rather than an error — in mode=baseline
# (resp. mode=agent), only one of the two JSONL files exists, which is fine.
read_volume_counts() {
  local volume="$1"
  local run_id="$2"

  # Heredoc inside the alpine container. `wc -l` on a non-existent file is
  # silenced; fallback prints `<name>_n=0`.
  local script
  script=$(cat <<'INNER'
run_id="$1"
for mode in agent baseline; do
  f="/r/${run_id}/${mode}.jsonl"
  if [ -f "$f" ]; then
    n=$(wc -l < "$f" | tr -d ' ')
  else
    n=0
  fi
  printf '%s_n=%s\n' "$mode" "$n"
done
INNER
  )

  # `docker run --rm` leaves no container; the volume itself is read-only
  # here (we don't write), so nothing can accidentally be mutated.
  docker run --rm \
    -v "${volume}:/r:ro" \
    "${SUMMARY_IMAGE}" \
    sh -c "${script}" -- "${run_id}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  parse_args "$@"

  local sha_prefix="${SHA:0:8}"
  # Lowercase the prefix — gen-compose.py lowercases too, so user-supplied
  # uppercase hex still matches the emitted service.
  sha_prefix="$(printf '%s' "${sha_prefix}" | tr '[:upper:]' '[:lower:]')"

  local service="cmt-${sha_prefix}"
  local container_name="fc-run-${sha_prefix}-${RUN_ID}"
  local volume="results-${sha_prefix}"

  assert_compose_has_service "${COMPOSE_FILE}" "${service}"

  # The exact command we (would) execute. Printed to stderr for both dry-run
  # and live runs so operators can see what happened in the logs.
  local -a docker_cmd=(
    docker compose
    -f "${COMPOSE_FILE}"
    run --rm
    --name "${container_name}"
    "${service}"
  )

  if [ "${DRY_RUN}" -eq 1 ]; then
    log "DRY-RUN: ${docker_cmd[*]}"
    return 0
  fi

  # Require docker only when we're actually going to run it; --dry-run is
  # deliberately usable in environments without docker installed.
  if ! command -v docker >/dev/null 2>&1; then
    die "docker is required but not found on PATH"
  fi

  log "running: ${docker_cmd[*]}"
  set +e
  "${docker_cmd[@]}"
  local rc=$?
  set -e

  if [ "${rc}" -ne 0 ]; then
    # Intentionally do NOT `docker volume rm` — partial results survive by
    # spec so operators can inspect /results/<run_id>/ after a failure.
    warn "container ${container_name} exited with code ${rc}; volume ${volume} left intact"
    exit "${rc}"
  fi

  # Success path: summarize what landed in the volume.
  local counts agent_n baseline_n
  if ! counts="$(read_volume_counts "${volume}" "${RUN_ID}" 2>/dev/null)"; then
    warn "failed to introspect volume ${volume} for summary; reporting zeroes"
    counts=$'agent_n=0\nbaseline_n=0'
  fi

  agent_n="$(printf '%s\n' "${counts}" | awk -F= '$1=="agent_n"{print $2; exit}')"
  baseline_n="$(printf '%s\n' "${counts}" | awk -F= '$1=="baseline_n"{print $2; exit}')"
  agent_n="${agent_n:-0}"
  baseline_n="${baseline_n:-0}"

  printf 'DONE %s agent_n=%s baseline_n=%s\n' "${SHA}" "${agent_n}" "${baseline_n}"
}

main "$@"
