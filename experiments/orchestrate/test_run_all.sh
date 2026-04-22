#!/usr/bin/env bash
# experiments/orchestrate/test_run_all.sh
#
# Bats-less smoke test for run-all.sh (issue #20). Intentionally does NOT
# require docker/tmux/jq/uv to be installed — every assertion runs under
# --dry-run or exercises the pre-flight failure path.
#
# Tests:
#   1. `bash -n` syntax check.
#   2. `--help` renders usage with every key option token.
#   3. Happy dry-run: `--dry-run --shas abc12345,def67890 --skip-build
#      --run-id test-<pid>` prints a plan that references both SHAs,
#      emits gen-compose / tmux commands, and ends with the exact
#      five-line final report.
#   4. Missing-tool pre-flight: build a tmpdir PATH that omits `tmux` and
#      assert the launcher exits non-zero with a clear error.
#   5. Idempotency: running --skip-build twice against the same --run-id
#      (first real, then simulated existing compose file) warns and
#      exits 0 without overwriting.
#   6. Mode validation: `--mode garbage` exits non-zero.
#
# Exit 0 on success, nonzero on first failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SCRIPT="${SCRIPT_DIR}/run-all.sh"

fail() { printf '[test_run_all] FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '[test_run_all] PASS: %s\n' "$*" >&2; }

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fc-run-all-test.XXXXXX")"
trap 'rm -rf -- "${TMP_DIR}"' EXIT

# ---------------------------------------------------------------------------
# Test 1 — bash -n syntax check
# ---------------------------------------------------------------------------
if ! bash -n "${RUN_SCRIPT}"; then
  fail "bash -n on run-all.sh returned nonzero"
fi
pass "bash -n run-all.sh"

# ---------------------------------------------------------------------------
# Test 2 — --help renders usage with expected tokens
# ---------------------------------------------------------------------------
help_out="$("${RUN_SCRIPT}" --help 2>&1)"
for token in \
  '--mode' '--max-parallel' '--skip-build' '--shas' '--run-id' '--dry-run' \
  'Started session:' 'Attach with:' 'Aggregate with:'
do
  case "${help_out}" in
    *"${token}"*) ;;
    *) fail "--help output missing '${token}':\n${help_out}" ;;
  esac
done
pass "--help lists every flag + final-report tokens"

# ---------------------------------------------------------------------------
# Test 3 — happy dry-run with --shas two,commits
# ---------------------------------------------------------------------------
RUN_ID_T3="test-$(date +%s)-$$"
DRY_OUT="$("${RUN_SCRIPT}" \
  --dry-run \
  --shas abc12345,def67890 \
  --skip-build \
  --run-id "${RUN_ID_T3}" \
  2>&1)" || fail "expected dry-run to exit 0, got nonzero; output:\n${DRY_OUT}"

# Both SHAs must appear in planned tmux-window commands.
for token in \
  'abc12345' 'def67890' \
  'cmt-abc12345' 'cmt-def67890' \
  'gen-compose.py' \
  'tmux new-session' 'tmux new-window' \
  "proof-eval-${RUN_ID_T3}" \
  'tail -F' 'docker compose' \
  'bash -lc' 'exec bash'
do
  case "${DRY_OUT}" in
    *"${token}"*) ;;
    *) fail "dry-run output missing '${token}':\n${DRY_OUT}" ;;
  esac
done

# Final report must include the five spec-exact lines.
for line in \
  "Started session: proof-eval-${RUN_ID_T3}" \
  "Results dir:    experiments/results/${RUN_ID_T3}/" \
  "Compose file:   experiments/results/${RUN_ID_T3}/compose.yml" \
  "Attach with:    ./attach.sh ${RUN_ID_T3}" \
  "Aggregate with: ./aggregate.sh ${RUN_ID_T3}"
do
  case "${DRY_OUT}" in
    *"${line}"*) ;;
    *) fail "dry-run final-report missing line '${line}':\n${DRY_OUT}" ;;
  esac
done

# --skip-build must actually skip the build invocation — the dry-run
# should log the "--skip-build" notice but NOT a "DRY-RUN: ... --all
# --max-parallel" line that would only appear when the build phase runs.
case "${DRY_OUT}" in
  *'DRY-RUN: '*'--all --max-parallel'*)
    fail "dry-run would have executed build phase despite --skip-build:\n${DRY_OUT}"
    ;;
esac
case "${DRY_OUT}" in
  *'--skip-build: not invoking'*) ;;
  *) fail "expected --skip-build notice in output; got:\n${DRY_OUT}" ;;
esac
pass "dry-run with two SHAs references both and emits the five-line report"

# ---------------------------------------------------------------------------
# Test 4 — missing-tool pre-flight simulation
# ---------------------------------------------------------------------------
# Build a tmpdir PATH containing stubs for docker/jq/uv but NOT tmux. Also
# include the system coreutils / bash so the script itself can still run
# (but crucially NOT /usr/bin/tmux if installed).
#
# We simulate by prepending an empty dir to PATH that only contains the
# binaries we want to be "available", and then scoping the real PATH
# lookup to just that dir + coreutils necessities.

STUB_DIR="${TMP_DIR}/stubs"
mkdir -p "${STUB_DIR}"

# Provide stubs for every tool run-all expects EXCEPT tmux, so only tmux
# is missing from PATH. Each stub is a trivial bash script that exits 0.
for t in docker jq uv; do
  cat > "${STUB_DIR}/${t}" <<'STUB'
#!/usr/bin/env bash
# Test stub: succeeds for any invocation. For 'docker compose version'
# we must exit 0 so the pre-flight check passes that particular probe.
exit 0
STUB
  chmod +x "${STUB_DIR}/${t}"
done

# Minimal PATH: the stubs dir + a couple of coreutils dirs so run-all's
# own bash-builtin dependencies resolve. No /usr/bin/tmux here.
CORE_PATHS="/usr/bin:/bin:/usr/sbin:/sbin"

set +e
MISSING_TMUX_OUT="$(
  env -i HOME="${HOME}" PATH="${STUB_DIR}:${CORE_PATHS}" \
    ANTHROPIC_API_KEY="test-key" \
    bash "${RUN_SCRIPT}" --shas abc12345 --skip-build --run-id "missing-tmux-$$" 2>&1
)"
MISSING_TMUX_RC=$?
set -e

if [ "${MISSING_TMUX_RC}" -eq 0 ]; then
  fail "expected non-zero exit when tmux is absent; output:\n${MISSING_TMUX_OUT}"
fi

case "${MISSING_TMUX_OUT}" in
  *'tmux'*)
    ;;
  *) fail "expected 'tmux' to appear in missing-tool error; got:\n${MISSING_TMUX_OUT}" ;;
esac
case "${MISSING_TMUX_OUT}" in
  *'pre-flight'*|*'required'*)
    ;;
  *) fail "expected a pre-flight error message; got:\n${MISSING_TMUX_OUT}" ;;
esac
pass "missing tmux triggers non-zero exit with clear pre-flight error"

# ---------------------------------------------------------------------------
# Test 5 — idempotency: existing compose.yml triggers refuse-and-exit-0
# ---------------------------------------------------------------------------
# Simulate by pre-creating the compose file in the results dir that run-all
# would target. Use --dry-run so we don't need docker.
SCRIPT_EXPERIMENTS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_ID_T5="test-idempotent-$$"
FAKE_RESULTS_DIR="${SCRIPT_EXPERIMENTS_ROOT}/results/${RUN_ID_T5}"
mkdir -p "${FAKE_RESULTS_DIR}"
printf 'name: preexisting\n' > "${FAKE_RESULTS_DIR}/compose.yml"

# Ensure cleanup even on early failure.
trap 'rm -rf -- "${TMP_DIR}" "${FAKE_RESULTS_DIR}"' EXIT

set +e
IDEM_OUT="$("${RUN_SCRIPT}" \
  --dry-run \
  --shas abc12345 \
  --skip-build \
  --run-id "${RUN_ID_T5}" \
  2>&1)"
IDEM_RC=$?
set -e

if [ "${IDEM_RC}" -ne 0 ]; then
  fail "expected exit 0 when compose.yml pre-exists, got ${IDEM_RC}; output:\n${IDEM_OUT}"
fi
case "${IDEM_OUT}" in
  *'already exists'*|*'refusing to overwrite'*)
    ;;
  *) fail "expected 'already exists' / 'refusing to overwrite' warning; got:\n${IDEM_OUT}" ;;
esac
# Confirm we did NOT clobber the pre-existing file.
if ! grep -q 'name: preexisting' "${FAKE_RESULTS_DIR}/compose.yml"; then
  fail "pre-existing compose.yml was overwritten by run-all (idempotency broken)"
fi
pass "pre-existing compose.yml is preserved; run-all warns and exits 0"

# ---------------------------------------------------------------------------
# Test 6 — invalid --mode
# ---------------------------------------------------------------------------
set +e
BADMODE_OUT="$("${RUN_SCRIPT}" --mode garbage --dry-run --shas abc12345 --skip-build --run-id "bad-mode-$$" 2>&1)"
BADMODE_RC=$?
set -e

if [ "${BADMODE_RC}" -eq 0 ]; then
  fail "expected non-zero exit for --mode garbage; output:\n${BADMODE_OUT}"
fi
case "${BADMODE_OUT}" in
  *'--mode must be one of'*|*'baseline'*) ;;
  *) fail "expected helpful --mode error; got:\n${BADMODE_OUT}" ;;
esac
pass "invalid --mode rejected with clear error"

printf '[test_run_all] all tests passed\n' >&2
