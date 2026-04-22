#!/usr/bin/env bash
# experiments/orchestrate/test_run_commit.sh
#
# Bats-less smoke test for run-commit.sh (issue #19). Intentionally does NOT
# require docker to be installed — --dry-run plus grep-based pre-flight
# checks give us full coverage of the script's argument-handling surface.
#
# Tests:
#   1. `bash -n` syntax check.
#   2. `--help` renders usage (shows both positional arg names + the
#      --compose-file / --dry-run options).
#   3. Happy dry-run: synthetic compose.yml with a single cmt-abc12345 service;
#      asserts the logged docker command references the expected service and
#      container name.
#   4. Service-missing error: same compose.yml but a SHA whose prefix doesn't
#      match anything in it; asserts non-zero exit and a helpful message.
#   5. Compose-file-missing error: path that doesn't exist; asserts non-zero
#      exit and a helpful message.
#   6. Missing-argument error: running with only one positional; asserts
#      non-zero exit and usage hint.
#
# Exit 0 on success, nonzero on first failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SCRIPT="${SCRIPT_DIR}/run-commit.sh"

fail() { printf '[test_run_commit] FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '[test_run_commit] PASS: %s\n' "$*" >&2; }

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fc-run-commit-test.XXXXXX")"
trap 'rm -rf -- "${TMP_DIR}"' EXIT

# ---------------------------------------------------------------------------
# Test 1 — bash -n syntax check
# ---------------------------------------------------------------------------
if ! bash -n "${RUN_SCRIPT}"; then
  fail "bash -n on run-commit.sh returned nonzero"
fi
pass "bash -n run-commit.sh"

# ---------------------------------------------------------------------------
# Test 2 — --help renders usage with expected tokens
# ---------------------------------------------------------------------------
help_out="$("${RUN_SCRIPT}" --help 2>&1)"
for token in '<sha>' '<run_id>' '--compose-file' '--dry-run'; do
  case "${help_out}" in
    *"${token}"*) ;;
    *) fail "--help output missing '${token}':\n${help_out}" ;;
  esac
done
pass "--help lists positional args and options"

# ---------------------------------------------------------------------------
# Synthetic compose.yml — one cmt-abc12345 service, one matching volume.
# Hand-written to mirror the shape gen-compose.py emits (two-space indent
# under `services:` and `volumes:`), without pulling in the generator.
# ---------------------------------------------------------------------------
COMPOSE_FILE="${TMP_DIR}/compose.yml"
cat > "${COMPOSE_FILE}" <<'YAML'
name: proof-eval-testrun

services:
  cmt-abc12345:
    image: fc-commit:abc12345
    container_name: fc-run-abc12345-test-run
    volumes:
      - "results-abc12345:/results"
    command:
      - "bash"
      - "-lc"
      - "echo hello"

volumes:
  results-abc12345:
    name: results-abc12345
YAML

# ---------------------------------------------------------------------------
# Test 3 — happy dry-run references service + container name
# ---------------------------------------------------------------------------
DRY_OUT="$("${RUN_SCRIPT}" abc12345deadbeef test-run --compose-file "${COMPOSE_FILE}" --dry-run 2>&1)" \
  || fail "expected dry-run to exit 0, got nonzero; output:\n${DRY_OUT}"

for token in 'cmt-abc12345' 'fc-run-abc12345-test-run' 'docker compose' 'run --rm'; do
  case "${DRY_OUT}" in
    *"${token}"*) ;;
    *) fail "dry-run output missing '${token}':\n${DRY_OUT}" ;;
  esac
done

# Compose file path should be echoed somewhere (in the `-f <path>` arg).
case "${DRY_OUT}" in
  *"${COMPOSE_FILE}"*) ;;
  *) fail "dry-run output missing compose file path '${COMPOSE_FILE}':\n${DRY_OUT}" ;;
esac
pass "dry-run against synthetic compose.yml references cmt-abc12345 and fc-run-abc12345-test-run"

# ---------------------------------------------------------------------------
# Test 4 — service-missing error path
# ---------------------------------------------------------------------------
set +e
MISSING_OUT="$("${RUN_SCRIPT}" unknownsha99999 test-run --compose-file "${COMPOSE_FILE}" 2>&1)"
MISSING_RC=$?
set -e

if [ "${MISSING_RC}" -eq 0 ]; then
  fail "expected non-zero exit when service not in compose; output:\n${MISSING_OUT}"
fi

# "unknownsha99999" => prefix "unknownsh" (first 8 chars: "unknowns") — lowercased.
# We just check the error mentions the missing service AND the compose file.
case "${MISSING_OUT}" in
  *'cmt-unknowns'*) ;;
  *) fail "expected error to name the missing service; got:\n${MISSING_OUT}" ;;
esac
case "${MISSING_OUT}" in
  *'does not declare'*|*'compose file'*)
    ;;
  *) fail "expected error to explain the compose file problem; got:\n${MISSING_OUT}" ;;
esac
pass "unknown-service exits non-zero with clear message"

# ---------------------------------------------------------------------------
# Test 5 — compose-file-missing error path
# ---------------------------------------------------------------------------
set +e
NOFILE_OUT="$("${RUN_SCRIPT}" abc12345deadbeef test-run --compose-file "${TMP_DIR}/does-not-exist.yml" 2>&1)"
NOFILE_RC=$?
set -e

if [ "${NOFILE_RC}" -eq 0 ]; then
  fail "expected non-zero exit when compose file missing; output:\n${NOFILE_OUT}"
fi
case "${NOFILE_OUT}" in
  *'not found'*) ;;
  *) fail "expected 'not found' in missing-compose-file error; got:\n${NOFILE_OUT}" ;;
esac
pass "missing compose file exits non-zero with clear message"

# ---------------------------------------------------------------------------
# Test 6 — missing positional argument
# ---------------------------------------------------------------------------
set +e
BADARGS_OUT="$("${RUN_SCRIPT}" abc12345deadbeef --compose-file "${COMPOSE_FILE}" --dry-run 2>&1)"
BADARGS_RC=$?
set -e

if [ "${BADARGS_RC}" -eq 0 ]; then
  fail "expected non-zero exit when <run_id> missing; output:\n${BADARGS_OUT}"
fi
case "${BADARGS_OUT}" in
  *'positional'*|*'Usage:'*) ;;
  *) fail "expected usage hint when <run_id> missing; got:\n${BADARGS_OUT}" ;;
esac
pass "missing <run_id> exits non-zero with usage hint"

printf '[test_run_commit] all tests passed\n' >&2
