#!/usr/bin/env bash
# experiments/orchestrate/attach.sh
#
# Trivial attach helper for the fiat-crypto proof-evaluation pipeline
# (epic #27 / issue #24). run-all.sh (#20) spawns a detached tmux session
# named `proof-eval-<RUN_ID>`; this script is the one-liner the operator
# invokes to attach to it, so nobody has to memorize the session-name
# pattern.
#
# Usage:
#   ./attach.sh                 — list all proof-eval-* sessions (exit 0)
#   ./attach.sh <run_id>        — exec tmux attach -t proof-eval-<run_id>
#
# This script only ATTACHES. It never creates a session — that's run-all.sh's
# job. If the requested session doesn't exist, we print a clear error that
# directs the operator back to `./attach.sh` (no args) to see what's running.
#
# The happy path uses `exec tmux attach` so the shell is replaced — the
# script doesn't linger as a stray process after the operator detaches.

set -euo pipefail

# ---------------------------------------------------------------------------
# Error helper (matches aggregate.sh / run-all.sh style). log/warn aren't
# needed here — this script is a one-liner wrapper; the only stderr output
# is its error messages.
# ---------------------------------------------------------------------------
die()  { printf '[attach] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: attach.sh [<run_id>]

Attach to a detached tmux session spawned by run-all.sh, whose name follows
the convention `proof-eval-<run_id>`.

Arguments:
  <run_id>       If given, exec `tmux attach -t proof-eval-<run_id>`. The
                 shell is replaced, so this script never lingers after you
                 detach. If the session doesn't exist, the command exits
                 non-zero with a hint to re-run with no args to list the
                 sessions that DO exist.
                 If omitted, lists all proof-eval-* tmux sessions and exits
                 0 — even when zero sessions are running (a clean empty
                 state is not an error).

Options:
  -h, --help     Show this help and exit.

Examples:
  ./attach.sh                     # list proof-eval-* sessions
  ./attach.sh 20250422-143000     # attach to proof-eval-20250422-143000
EOF
}

# ---------------------------------------------------------------------------
# tmux availability check — shared by both list and attach paths.
# ---------------------------------------------------------------------------
require_tmux() {
  if ! command -v tmux >/dev/null 2>&1; then
    die "tmux is not installed or not on PATH (install via your package manager, e.g. 'apt install tmux'). This script only attaches to sessions created by run-all.sh."
  fi
}

# ---------------------------------------------------------------------------
# list_sessions
#
# Prints every tmux session whose name matches `proof-eval-*`, one per line.
# Exits 0 whether zero or many are found — "no sessions" is a legitimate
# steady state, not an error.
#
# `tmux list-sessions` exits non-zero with "no server running on ..." when
# no tmux server has been started yet; we tolerate that and treat it as the
# empty-list case.
# ---------------------------------------------------------------------------
list_sessions() {
  require_tmux

  local names
  # Capture both stdout and stderr; swallow the non-zero exit via || true so
  # `set -e` doesn't abort us. The `no server running` case is the common
  # "no sessions" state, not a failure.
  names="$(tmux list-sessions -F '#{session_name}' 2>/dev/null || true)"

  local matches
  # grep -E against an exact prefix; `|| true` so no matches (exit 1) is OK.
  matches="$(printf '%s\n' "${names}" | grep -E '^proof-eval-' || true)"

  if [ -z "${matches}" ]; then
    printf 'No proof-eval-* tmux sessions are currently running.\n'
    printf 'Start one with: experiments/orchestrate/run-all.sh\n'
    return 0
  fi

  printf 'Running proof-eval-* tmux sessions:\n'
  # Indent each for readability; no sort needed (tmux already orders by
  # creation time which is useful for the operator).
  printf '%s\n' "${matches}" | sed 's/^/  /'
  printf '\nAttach with: ./attach.sh <run_id>\n'
  printf '  (where <run_id> is the trailing part after proof-eval-)\n'
  return 0
}

# ---------------------------------------------------------------------------
# attach_session <run_id>
#
# `exec tmux attach -t proof-eval-<run_id>` — shell is replaced so the
# script doesn't hang as a stray process after the user detaches.
#
# We probe `tmux has-session` first so we can produce a friendlier error
# than tmux's own "can't find session". This costs one extra fork but makes
# the tool much nicer to use.
# ---------------------------------------------------------------------------
attach_session() {
  local run_id="$1"
  require_tmux

  local session="proof-eval-${run_id}"

  if ! tmux has-session -t "${session}" 2>/dev/null; then
    printf '[attach] ERROR: no tmux session named %q\n' "${session}" >&2
    printf '[attach] Run ./attach.sh with no arguments to list available proof-eval-* sessions.\n' >&2
    exit 1
  fi

  # exec replaces this shell; no code runs after this.
  exec tmux attach -t "${session}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  case "${1:-}" in
    -h|--help)
      usage
      exit 0
      ;;
    '')
      list_sessions
      ;;
    -*)
      usage >&2
      die "unknown option: $1 (try --help)"
      ;;
    *)
      if [ "$#" -gt 1 ]; then
        usage >&2
        die "expected at most 1 positional argument (<run_id>); got $#"
      fi
      attach_session "$1"
      ;;
  esac
}

main "$@"
