#!/usr/bin/env bash
# experiments/orchestrate/test_attach.sh
#
# Bats-less smoke test for attach.sh (issue #24). Does NOT require a real
# tmux server — we stub tmux via a fake script prepended to PATH, letting
# us exercise every code path in a pure-bash environment.
#
# Tests:
#   1. `bash -n` syntax check.
#   2. `--help` renders usage with expected tokens.
#   3. Stubbed tmux reporting "no server running": no-arg invocation exits 0
#      and prints a sensible "no sessions" message.
#   4. Stubbed tmux returning a session list containing `proof-eval-abc123`
#      and unrelated noise sessions: no-arg invocation prints only the
#      `proof-eval-*` entries.
#   5. With arg `abc123` and the session not present: exits non-zero with
#      a message that mentions running with no args.
#   6. tmux absent from PATH entirely: exits non-zero with a "tmux not
#      installed" message.
#
# Exit 0 on success, nonzero on first failure. No real tmux required.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ATTACH_SCRIPT="${SCRIPT_DIR}/attach.sh"

fail() { printf '[test_attach] FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '[test_attach] PASS: %s\n' "$*" >&2; }

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fc-attach-test.XXXXXX")"
trap 'rm -rf -- "${TMP_DIR}"' EXIT

# ---------------------------------------------------------------------------
# Test 1 — bash -n syntax check
# ---------------------------------------------------------------------------
if ! bash -n "${ATTACH_SCRIPT}"; then
  fail "bash -n on attach.sh returned nonzero"
fi
pass "bash -n attach.sh"

# ---------------------------------------------------------------------------
# Test 2 — --help renders usage with expected tokens
# ---------------------------------------------------------------------------
help_out="$("${ATTACH_SCRIPT}" --help 2>&1)"
for token in '<run_id>' 'Usage:' 'proof-eval-' 'tmux attach'; do
  case "${help_out}" in
    *"${token}"*) ;;
    *) fail "--help output missing '${token}':
${help_out}" ;;
  esac
done
pass "--help renders usage with expected tokens"

# ---------------------------------------------------------------------------
# Helpers for stubbing tmux.
# ---------------------------------------------------------------------------
# Each stub lives in its own dir so we can swap by prepending that dir
# to PATH. Stubs log invocations to $STUB_LOG when present, so tests can
# assert on the argv the script passed.

# stub_dir <name> -> creates TMP_DIR/<name>/ and prints its path
stub_dir() {
  local name="$1"
  local d="${TMP_DIR}/${name}"
  mkdir -p "${d}"
  printf '%s' "${d}"
}

# ---------------------------------------------------------------------------
# Test 3 — stubbed tmux: "no server running" -> exit 0 + friendly message.
# ---------------------------------------------------------------------------
STUB3="$(stub_dir stub-noserver)"
cat > "${STUB3}/tmux" <<'SH'
#!/usr/bin/env bash
# Mimic tmux's behavior when no server is running: list-sessions exits
# non-zero with a message to stderr. Other subcommands shouldn't be
# invoked in the no-arg path, but be safe.
case "$1" in
  list-sessions)
    printf 'no server running on /tmp/tmux-1000/default\n' >&2
    exit 1
    ;;
  *)
    printf 'tmux stub: unexpected subcommand: %s\n' "$*" >&2
    exit 99
    ;;
esac
SH
chmod +x "${STUB3}/tmux"

OUT3="$(PATH="${STUB3}:${PATH}" "${ATTACH_SCRIPT}" 2>&1)" \
  || fail "no-arg invocation with 'no server' stub should exit 0; got nonzero
output: ${OUT3}"

for token in 'No proof-eval-* tmux sessions' 'run-all.sh'; do
  case "${OUT3}" in
    *"${token}"*) ;;
    *) fail "no-sessions output missing '${token}':
${OUT3}" ;;
  esac
done
pass "no-arg + 'no server' stub: exit 0, prints sensible 'no sessions' message"

# ---------------------------------------------------------------------------
# Test 4 — stubbed tmux: mix of proof-eval-* and unrelated sessions.
# ---------------------------------------------------------------------------
STUB4="$(stub_dir stub-mixed)"
cat > "${STUB4}/tmux" <<'SH'
#!/usr/bin/env bash
case "$1" in
  list-sessions)
    # Emit a realistic mix: two proof-eval-* and two unrelated. The
    # ordering is intentional — we want to confirm filtering is
    # order-independent, not that we rely on alphabetical order.
    cat <<'LIST'
random-session
proof-eval-abc123
editor
proof-eval-20250422-143000
LIST
    exit 0
    ;;
  *)
    printf 'tmux stub: unexpected subcommand: %s\n' "$*" >&2
    exit 99
    ;;
esac
SH
chmod +x "${STUB4}/tmux"

OUT4="$(PATH="${STUB4}:${PATH}" "${ATTACH_SCRIPT}" 2>&1)" \
  || fail "no-arg invocation with mixed-list stub should exit 0; got nonzero
output: ${OUT4}"

# Must include the proof-eval-* names.
for token in 'proof-eval-abc123' 'proof-eval-20250422-143000'; do
  case "${OUT4}" in
    *"${token}"*) ;;
    *) fail "mixed-list output missing proof-eval entry '${token}':
${OUT4}" ;;
  esac
done

# Must NOT include the unrelated session names as listed entries. We check
# word-boundary-ish: the unrelated names themselves shouldn't appear in the
# output since they don't match proof-eval-*.
for noise in 'random-session' 'editor'; do
  case "${OUT4}" in
    *"${noise}"*)
      fail "mixed-list output leaked noise session '${noise}':
${OUT4}"
      ;;
  esac
done
pass "no-arg + mixed-list stub: prints only proof-eval-* entries"

# ---------------------------------------------------------------------------
# Test 5 — arg 'abc123' with session NOT present: exit non-zero, mention
# running with no args.
# ---------------------------------------------------------------------------
STUB5="$(stub_dir stub-missing)"
cat > "${STUB5}/tmux" <<'SH'
#!/usr/bin/env bash
case "$1" in
  has-session)
    # Always report "no such session" regardless of -t argument.
    printf "can't find session: %s\n" "${3:-<unknown>}" >&2
    exit 1
    ;;
  list-sessions)
    exit 0
    ;;
  attach)
    # We shouldn't reach attach in this test, but fail loudly if we do.
    printf 'tmux stub: attach called unexpectedly: %s\n' "$*" >&2
    exit 88
    ;;
  *)
    printf 'tmux stub: unexpected subcommand: %s\n' "$*" >&2
    exit 99
    ;;
esac
SH
chmod +x "${STUB5}/tmux"

set +e
OUT5="$(PATH="${STUB5}:${PATH}" "${ATTACH_SCRIPT}" abc123 2>&1)"
RC5=$?
set -e

[ "${RC5}" -ne 0 ] || fail "expected non-zero exit when session missing; got 0
output: ${OUT5}"

# Error message must mention the session and the hint about no-args listing.
case "${OUT5}" in
  *'proof-eval-abc123'*) ;;
  *) fail "missing-session error should mention 'proof-eval-abc123':
${OUT5}" ;;
esac
case "${OUT5}" in
  *'no arguments'*|*'no args'*) ;;
  *) fail "missing-session error should hint at running with no args:
${OUT5}" ;;
esac
pass "arg with missing session: non-zero exit, error mentions running with no args"

# ---------------------------------------------------------------------------
# Test 6 — tmux absent from PATH entirely.
# ---------------------------------------------------------------------------
# We need coreutils + bash + env on PATH for the script to even run (the
# shebang `#!/usr/bin/env bash` resolves through PATH), but tmux must be
# unavailable. Build a synthetic PATH that symlinks everything reachable
# via `command -v` EXCEPT tmux into a scratch dir, then point PATH at only
# that dir.
NO_TMUX_DIR="$(stub_dir no-tmux)"
for bin in env bash sh printf grep sed awk cat tr mkdir readlink rm ls wc sha256sum mktemp chmod head tail cut sort uniq find; do
  src="$(command -v "${bin}" 2>/dev/null || true)"
  if [ -n "${src}" ] && [ ! -e "${NO_TMUX_DIR}/${bin}" ]; then
    ln -s "${src}" "${NO_TMUX_DIR}/${bin}"
  fi
done
# Sanity-check: tmux must NOT be reachable through NO_TMUX_DIR.
if PATH="${NO_TMUX_DIR}" command -v tmux >/dev/null 2>&1; then
  fail "test setup bug: tmux still reachable through ${NO_TMUX_DIR}"
fi

set +e
OUT6="$(PATH="${NO_TMUX_DIR}" "${ATTACH_SCRIPT}" 2>&1)"
RC6=$?
set -e

[ "${RC6}" -ne 0 ] || fail "expected non-zero exit when tmux missing from PATH; got 0
output: ${OUT6}"

case "${OUT6}" in
  *'tmux is not installed'*|*'tmux not installed'*) ;;
  *) fail "tmux-missing error should mention 'tmux is not installed':
${OUT6}" ;;
esac
pass "tmux absent from PATH: non-zero exit with 'tmux not installed' message"

# Also exercise the same condition via the with-arg path, which exercises
# attach_session's require_tmux call rather than list_sessions'.
set +e
OUT6b="$(PATH="${NO_TMUX_DIR}" "${ATTACH_SCRIPT}" abc123 2>&1)"
RC6b=$?
set -e

[ "${RC6b}" -ne 0 ] || fail "expected non-zero exit (with arg) when tmux missing; got 0
output: ${OUT6b}"
case "${OUT6b}" in
  *'tmux is not installed'*|*'tmux not installed'*) ;;
  *) fail "tmux-missing (with arg) error should mention 'tmux is not installed':
${OUT6b}" ;;
esac
pass "tmux absent + with arg: non-zero exit with 'tmux not installed' message"

printf '[test_attach] all tests passed\n' >&2
