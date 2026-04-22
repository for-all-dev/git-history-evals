#!/usr/bin/env bash
# experiments/orchestrate/run-all.sh
#
# Top-level launcher for the fiat-crypto proof-evaluation pipeline
# (epic #27 / issue #20). One command the user types to kick off the
# whole eval:
#
#   1. Pre-flight: check tmux, docker + docker compose, jq, uv, and the
#      ANTHROPIC_API_KEY env var are all available.
#   2. Unless --skip-build, invoke ./build-images.sh --all --max-parallel N
#      to (re)build fc-base / fc-deps / fc-commit layers.
#   3. Determine the SHA list (from --shas, else via commits_from_meta in
#      lib.sh).
#   4. Invoke python3 gen-compose.py --run-id <RUN_ID> --mode <MODE> ...
#      to emit experiments/results/<RUN_ID>/compose.yml. Aborts if the
#      file already exists (idempotency guarantee: same RUN_ID never
#      clobbers prior state).
#   5. Start a detached tmux session named proof-eval-<RUN_ID> with:
#        - window 0 'controller'  : tail -F run.log
#        - window 1 'compose-ps'  : watch docker compose ps
#        - one window per SHA     : ./run-commit.sh <sha> <RUN_ID>
#      Each pane wraps its command in `bash -lc '<cmd>; exec bash'` so
#      that when the child process ends the pane stays open for
#      post-mortem inspection.
#   6. Return immediately — we do NOT attach. The user uses the printed
#      `./attach.sh <run_id>` command (tier 6, issue #24).
#
# --dry-run mode prints the planned shell invocations without executing
# build, gen-compose, or tmux — useful for CI and for validation in
# environments that lack docker/tmux.

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENTS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${EXPERIMENTS_ROOT}/.." && pwd)"

BUILD_IMAGES_SH="${SCRIPT_DIR}/build-images.sh"
GEN_COMPOSE_PY="${SCRIPT_DIR}/gen-compose.py"
RUN_COMMIT_SH="${SCRIPT_DIR}/run-commit.sh"
LIB_SH="${SCRIPT_DIR}/lib.sh"

# ---------------------------------------------------------------------------
# Defaults / globals
# ---------------------------------------------------------------------------
MODE="both"
MAX_PARALLEL=4
SKIP_BUILD=0
SHAS_ARG=""
DRY_RUN=0
RUN_ID_OVERRIDE=""
PHASE="startup"

# ---------------------------------------------------------------------------
# Logging helpers (match build-images.sh / run-commit.sh style)
# ---------------------------------------------------------------------------
log()  { printf '[run-all] %s\n' "$*" >&2; }
warn() { printf '[run-all] WARN: %s\n' "$*" >&2; }
die()  { printf '[run-all] ERROR: %s\n' "$*" >&2; exit 1; }

# Failure trap: report which phase the launcher died in so operators know
# whether the eval itself was touched (tmux phase) or the run was aborted
# before anything kicked off.
on_failure() {
  local rc=$?
  if [ "${rc}" -ne 0 ]; then
    local run_id_disp="${RUN_ID:-<unassigned>}"
    printf '[run-all] ERROR: %s aborted at phase %s (exit %d)\n' \
      "${run_id_disp}" "${PHASE}" "${rc}" >&2
  fi
  return "${rc}"
}
trap on_failure ERR

usage() {
  cat <<'EOF'
Usage: run-all.sh [OPTIONS]

Launches the fiat-crypto proof-evaluation pipeline end-to-end: builds the
layered docker images, generates a per-run compose file, and spawns a
detached tmux session with one window per SHA.

Options:
  --mode <baseline|agent|both>
        Which evaluator(s) each per-commit container should run.
        Default: both.
  --max-parallel N
        Parallel image builds in phase 3 of build-images.sh.
        Default: 4.
  --skip-build
        Skip the ./build-images.sh invocation. Useful when images from
        a prior run are already present.
  --shas <sha1,sha2,...>
        Comma-separated list of fiat-crypto SHAs to evaluate. When
        omitted, the SHAs are discovered via commits_from_meta (lib.sh).
  --run-id <id>
        Override the auto-generated RUN_ID (default: date +%Y%m%d-%H%M%S).
        Primarily useful for reproducing a prior run's layout or for
        re-entering an existing tmux session; the launcher refuses to
        overwrite an existing compose.yml.
  --dry-run
        Print the planned build, gen-compose, and tmux invocations
        without executing them. Safe in environments lacking docker/tmux.
  -h, --help
        Show this help and exit.

After spawning, run-all.sh prints exactly:
  Started session: proof-eval-<run-id>
  Results dir:    experiments/results/<run-id>/
  Compose file:   experiments/results/<run-id>/compose.yml
  Attach with:    ./attach.sh <run-id>
  Aggregate with: ./aggregate.sh <run-id>

Each tmux pane wraps its inner command in `bash -lc '<cmd>; exec bash'`
so the pane stays interactive after the command exits — this lets you
inspect failed evals post-mortem without losing the pane.
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      --mode)
        [ "$#" -ge 2 ] || die "--mode requires an argument"
        MODE="$2"
        case "${MODE}" in
          baseline|agent|both) ;;
          *) die "--mode must be one of: baseline, agent, both (got '${MODE}')" ;;
        esac
        shift 2
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
      --skip-build)
        SKIP_BUILD=1
        shift
        ;;
      --shas)
        [ "$#" -ge 2 ] || die "--shas requires a comma-separated list"
        SHAS_ARG="$2"
        shift 2
        ;;
      --run-id)
        [ "$#" -ge 2 ] || die "--run-id requires an id string"
        RUN_ID_OVERRIDE="$2"
        case "${RUN_ID_OVERRIDE}" in
          *[!A-Za-z0-9._-]*)
            die "--run-id may only contain [A-Za-z0-9._-]; got '${RUN_ID_OVERRIDE}'"
            ;;
        esac
        shift 2
        ;;
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      *)
        die "unknown argument: $1 (try --help)"
        ;;
    esac
  done
}

# ---------------------------------------------------------------------------
# Pre-flight checks: tmux, docker + `docker compose`, jq, uv, ANTHROPIC_API_KEY.
# Accumulate every failure so the operator sees them all at once.
# ---------------------------------------------------------------------------
preflight() {
  PHASE="preflight"
  local -a errors=()

  if ! command -v tmux >/dev/null 2>&1; then
    errors+=("tmux is required but not found on PATH (install via your package manager, e.g. 'apt install tmux')")
  fi

  if ! command -v docker >/dev/null 2>&1; then
    errors+=("docker is required but not found on PATH (https://docs.docker.com/engine/install/)")
  else
    # `docker compose` is a subcommand in modern docker; probe it explicitly.
    if ! docker compose version >/dev/null 2>&1; then
      errors+=("'docker compose' subcommand not available; install the Compose V2 plugin")
    fi
  fi

  if ! command -v jq >/dev/null 2>&1; then
    errors+=("jq is required but not found on PATH")
  fi

  if ! command -v uv >/dev/null 2>&1; then
    errors+=("uv is required but not found on PATH (https://docs.astral.sh/uv/getting-started/installation/)")
  fi

  if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    errors+=("ANTHROPIC_API_KEY is not set in the environment (required for agent mode; export it before re-running)")
  fi

  if [ "${#errors[@]}" -gt 0 ]; then
    local msg
    for msg in "${errors[@]}"; do
      printf '[run-all] ERROR: %s\n' "${msg}" >&2
    done
    die "pre-flight checks failed (${#errors[@]} issue(s)); fix the above and re-run"
  fi

  log "pre-flight OK: tmux, docker, docker compose, jq, uv, ANTHROPIC_API_KEY"
}

# ---------------------------------------------------------------------------
# SHA resolution: --shas override, else commits_from_meta (via lib.sh).
# Emits space-separated SHAs on stdout.
# ---------------------------------------------------------------------------
resolve_shas() {
  PHASE="resolve-shas"
  local -a shas=()
  local s
  if [ -n "${SHAS_ARG}" ]; then
    # Split on commas; trim whitespace.
    local IFS=','
    # shellcheck disable=SC2206
    local -a raw=( ${SHAS_ARG} )
    IFS=$' \t\n'
    for s in "${raw[@]}"; do
      s="${s## }"
      s="${s%% }"
      [ -n "${s}" ] && shas+=("${s}")
    done
  else
    # Source lib.sh and call commits_from_meta. Guard the source behind an
    # `if` so a `set -e` environment doesn't explode on its jq-availability
    # check (which we've already passed in preflight).
    # shellcheck source=./lib.sh
    . "${LIB_SH}"
    while IFS= read -r s; do
      [ -n "${s}" ] && shas+=("${s}")
    done < <(commits_from_meta)
  fi
  if [ "${#shas[@]}" -eq 0 ]; then
    die "no SHAs to evaluate (--shas produced an empty list and commits_from_meta found none)"
  fi
  printf '%s\n' "${shas[@]}"
}

# ---------------------------------------------------------------------------
# Build images (phase 2).
# ---------------------------------------------------------------------------
run_build_images() {
  PHASE="build-images"
  local -a cmd=( "${BUILD_IMAGES_SH}" --all --max-parallel "${MAX_PARALLEL}" )
  if [ "${DRY_RUN}" -eq 1 ]; then
    log "DRY-RUN: ${cmd[*]}"
    return 0
  fi
  log "building images: ${cmd[*]}"
  if ! "${cmd[@]}"; then
    die "build-images.sh failed"
  fi
}

# ---------------------------------------------------------------------------
# Generate compose file (phase 4).
# Refuses to overwrite an existing compose.yml — that's our idempotency
# guarantee when the user supplies --run-id.
# ---------------------------------------------------------------------------
run_gen_compose() {
  PHASE="gen-compose"
  local shas_csv="$1"
  local out="$2"
  local -a cmd=(
    python3 "${GEN_COMPOSE_PY}"
    --run-id "${RUN_ID}"
    --mode "${MODE}"
    --shas "${shas_csv}"
    --out "${out}"
  )

  if [ -e "${out}" ]; then
    warn "compose file already exists at ${out}; refusing to overwrite"
    warn "(same RUN_ID as a previous run — delete the file or pass a new --run-id to regenerate)"
    exit 0
  fi

  if [ "${DRY_RUN}" -eq 1 ]; then
    log "DRY-RUN: ${cmd[*]}"
    return 0
  fi
  log "generating compose file: ${cmd[*]}"
  if ! "${cmd[@]}"; then
    die "gen-compose.py failed"
  fi
  if [ ! -f "${out}" ]; then
    die "gen-compose.py returned 0 but ${out} was not created"
  fi
}

# ---------------------------------------------------------------------------
# Spawn tmux session (phase 5). Session is always started DETACHED; we
# never attach — the user invokes attach.sh (#24) when ready.
#
# Pane command convention: wrap in `bash -lc '<cmd>; exec bash'` so that
# when the child process ends (successful eval OR failure) the pane stays
# open for post-mortem inspection. This is a deliberate trade-off: it
# prevents the whole session from collapsing when evals finish out of
# order, at the cost of a tmux "ghost" pane per completed SHA that the
# user must close (Ctrl-b & or `exit`).
# ---------------------------------------------------------------------------
spawn_tmux() {
  PHASE="spawn-tmux"
  local session="$1"
  local compose_file="$2"
  local run_log="$3"
  shift 3
  local -a shas=("$@")

  # Idempotency: if a session with this name already exists, refuse.
  if [ "${DRY_RUN}" -ne 1 ]; then
    if command -v tmux >/dev/null 2>&1 && tmux has-session -t "${session}" 2>/dev/null; then
      die "tmux session '${session}' already exists; attach with './attach.sh ${RUN_ID}' or pass a new --run-id"
    fi
  fi

  # Touch the run.log so tail -F doesn't error if nothing has written yet.
  if [ "${DRY_RUN}" -ne 1 ]; then
    : > "${run_log}"
  fi

  local controller_cmd="tail -F ${run_log}"
  local compose_ps_cmd
  # Note: `watch` needs a single shell string — we pass the whole docker
  # compose invocation as its argument.
  compose_ps_cmd="watch -n 5 \"docker compose -f ${compose_file} ps\""

  # Helper: format a "bash -lc '<cmd>; exec bash'" tmux pane command.
  # Using single quotes around the inner cmd means it can itself contain
  # double quotes freely (we never embed a literal ' inside).
  wrap_pane() {
    local inner="$1"
    printf "bash -lc '%s; exec bash'" "${inner}"
  }

  # tmux session creation.
  local tmux_new_session tmux_new_window_ps
  tmux_new_session="tmux new-session -d -s \"${session}\" -n controller $(wrap_pane "${controller_cmd}")"
  tmux_new_window_ps="tmux new-window -t \"${session}\" -n compose-ps $(wrap_pane "${compose_ps_cmd}")"

  if [ "${DRY_RUN}" -eq 1 ]; then
    log "DRY-RUN: ${tmux_new_session}"
    log "DRY-RUN: ${tmux_new_window_ps}"
  else
    eval "${tmux_new_session}"
    eval "${tmux_new_window_ps}"
  fi

  local sha sha_prefix win_name cmd_str spawn_str
  for sha in "${shas[@]}"; do
    # Lowercase + 8-char prefix matches gen-compose.py's service naming.
    sha_prefix="$(printf '%s' "${sha:0:8}" | tr '[:upper:]' '[:lower:]')"
    win_name="cmt-${sha_prefix}"
    cmd_str="${RUN_COMMIT_SH} ${sha} ${RUN_ID}"
    spawn_str="tmux new-window -t \"${session}\" -n \"${win_name}\" $(wrap_pane "${cmd_str}")"
    if [ "${DRY_RUN}" -eq 1 ]; then
      log "DRY-RUN: ${spawn_str}"
    else
      eval "${spawn_str}"
    fi
  done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  parse_args "$@"

  # Pre-flight is deliberately skipped for --dry-run: a dry run should
  # work in environments without docker/tmux/jq/uv so CI can validate the
  # plan text. ANTHROPIC_API_KEY is similarly not required for --dry-run.
  if [ "${DRY_RUN}" -ne 1 ]; then
    preflight
  else
    log "DRY-RUN: skipping pre-flight tool checks"
  fi

  PHASE="init-run"
  if [ -n "${RUN_ID_OVERRIDE}" ]; then
    RUN_ID="${RUN_ID_OVERRIDE}"
  else
    RUN_ID="$(date +%Y%m%d-%H%M%S)"
  fi

  local session="proof-eval-${RUN_ID}"
  local results_dir="${EXPERIMENTS_ROOT}/results/${RUN_ID}"
  local compose_file="${results_dir}/compose.yml"
  local run_log="${results_dir}/run.log"

  # Results dir — safe to create in dry-run too, it's cheap and deterministic.
  if [ "${DRY_RUN}" -ne 1 ]; then
    mkdir -p "${results_dir}"
  else
    log "DRY-RUN: would mkdir -p ${results_dir}"
  fi

  # Phase 2: build images (unless skipped).
  if [ "${SKIP_BUILD}" -eq 1 ]; then
    log "--skip-build: not invoking build-images.sh"
  else
    run_build_images
  fi

  # Phase 3: resolve SHAs.
  local -a SHAS=()
  local line
  while IFS= read -r line; do
    [ -n "${line}" ] && SHAS+=("${line}")
  done < <(resolve_shas)
  log "resolved ${#SHAS[@]} SHA(s): ${SHAS[*]}"

  # Phase 4: emit compose file.
  local shas_csv
  shas_csv="$(IFS=','; echo "${SHAS[*]}")"
  run_gen_compose "${shas_csv}" "${compose_file}"

  # Phase 5: spawn tmux.
  spawn_tmux "${session}" "${compose_file}" "${run_log}" "${SHAS[@]}"

  PHASE="done"

  # Display paths as repo-relative when they live under REPO_ROOT, so the
  # printed form matches what's in the acceptance criteria exactly.
  local results_disp compose_disp
  case "${results_dir}" in
    "${REPO_ROOT}/"*) results_disp="${results_dir#${REPO_ROOT}/}" ;;
    *)                results_disp="${results_dir}" ;;
  esac
  case "${compose_file}" in
    "${REPO_ROOT}/"*) compose_disp="${compose_file#${REPO_ROOT}/}" ;;
    *)                compose_disp="${compose_file}" ;;
  esac

  # Final report — format is spec-exact. stdout so it's cleanly parseable.
  printf 'Started session: %s\n'  "${session}"
  printf 'Results dir:    %s/\n'  "${results_disp}"
  printf 'Compose file:   %s\n'   "${compose_disp}"
  printf 'Attach with:    ./attach.sh %s\n' "${RUN_ID}"
  printf 'Aggregate with: ./aggregate.sh %s\n' "${RUN_ID}"
}

main "$@"
