#!/usr/bin/env bash
# experiments/orchestrate/test_aggregate.sh
#
# Bats-less smoke test for aggregate.sh (issue #21). Does NOT require docker
# to be installed — we exercise --skip-copy (which bypasses volume
# enumeration + the alpine copy container) and --dry-run, letting us cover
# the script's full argument-handling + concat + summary.py invocation
# surface in a pure-bash environment.
#
# Tests:
#   1. `bash -n` syntax check.
#   2. `--help` renders usage with expected tokens.
#   3. --dry-run: synthetic results/<id>/ with a compose.yml + a pre-staged
#      raw/<prefix>/agent.jsonl. The test patches enumerate_volumes to avoid
#      needing docker; --dry-run just prints plans. Verify the plans
#      mention the expected tokens.
#   4. --skip-copy happy-path: pre-stage raw/<prefix>/{agent,baseline}.jsonl
#      by hand, run with --skip-copy, assert that summary.json and
#      summary.md exist afterward, and that the printed AGGREGATE summary
#      line counts match what we wrote.
#   5. Auto-select newest run_id: create two dirs 20240101-000000/ and
#      20240102-000000/, omit the positional arg, assert we picked the
#      latter.
#   6. Idempotency: re-run test 4 and assert summary.json is byte-identical
#      to its first-run value.
#
# Exit 0 on success, nonzero on first failure. No docker required.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGGREGATE_SCRIPT="${SCRIPT_DIR}/aggregate.sh"
EXPERIMENTS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

fail() { printf '[test_aggregate] FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '[test_aggregate] PASS: %s\n' "$*" >&2; }

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fc-aggregate-test.XXXXXX")"
trap 'rm -rf -- "${TMP_DIR}"' EXIT

# ---------------------------------------------------------------------------
# Test 1 — bash -n syntax check
# ---------------------------------------------------------------------------
if ! bash -n "${AGGREGATE_SCRIPT}"; then
  fail "bash -n on aggregate.sh returned nonzero"
fi
pass "bash -n aggregate.sh"

# ---------------------------------------------------------------------------
# Test 2 — --help renders usage with expected tokens
# ---------------------------------------------------------------------------
help_out="$("${AGGREGATE_SCRIPT}" --help 2>&1)"
for token in '<run_id>' '--dry-run' '--skip-copy' 'Usage:' 'summary.py'; do
  case "${help_out}" in
    *"${token}"*) ;;
    *) fail "--help output missing '${token}':\n${help_out}" ;;
  esac
done
pass "--help lists positional arg and options"

# ---------------------------------------------------------------------------
# We stage a fake experiments/results/ tree inside TMP_DIR and run
# aggregate.sh against it by symlinking experiments/results -> our tmp
# tree. A backup of the real experiments/results is saved and restored
# via trap.
# ---------------------------------------------------------------------------
REAL_RESULTS="${EXPERIMENTS_ROOT}/results"
FAKE_RESULTS="${TMP_DIR}/results"
mkdir -p "${FAKE_RESULTS}"

# Snapshot the existing results dir so we can restore it. Move, don't copy,
# to preserve symlinks and avoid doubling disk usage on large runs.
BACKUP_DIR="${TMP_DIR}/real-results-backup"
if [ -e "${REAL_RESULTS}" ]; then
  mv "${REAL_RESULTS}" "${BACKUP_DIR}"
fi
# Chain the restore onto the existing rm trap.
trap '
  rc=$?
  rm -rf -- "${REAL_RESULTS}" 2>/dev/null || true
  if [ -e "'"${BACKUP_DIR}"'" ]; then
    mv "'"${BACKUP_DIR}"'" "'"${REAL_RESULTS}"'" || true
  fi
  rm -rf -- "'"${TMP_DIR}"'"
  exit "$rc"
' EXIT

# aggregate.sh resolves RESULTS_ROOT as ${SCRIPT_DIR}/../results — i.e.
# ${EXPERIMENTS_ROOT}/results. Point that at our fake tree via a symlink.
ln -s "${FAKE_RESULTS}" "${REAL_RESULTS}"

# ---------------------------------------------------------------------------
# Helper: write a synthetic JSONL for a given mode + deletion_size combo.
# summary.py aggregates by (mode, deletion_size); we want at least one row
# with a valid deletion_size so summary.json gets a non-trivial "groups"
# array.
# ---------------------------------------------------------------------------
write_jsonl() {
  local path="$1"
  local mode="$2"
  mkdir -p "$(dirname "${path}")"
  cat > "${path}" <<JSON
{"mode":"${mode}","deletion_size":3,"verdict":"PASS","inference_time_s":1.0,"output_tokens":100,"normalized_edit_distance":0.1}
{"mode":"${mode}","deletion_size":3,"verdict":"FAIL","inference_time_s":2.0,"output_tokens":200,"normalized_edit_distance":0.5}
JSON
}

# ---------------------------------------------------------------------------
# Test 3 — --dry-run prints expected plans
# ---------------------------------------------------------------------------
RUN_ID_DRY="20240103-000000"
DRY_RUN_DIR="${FAKE_RESULTS}/${RUN_ID_DRY}"
mkdir -p "${DRY_RUN_DIR}/raw/abc12345"
# Write a minimal compose.yml — --dry-run with --skip-copy never actually
# runs docker, so we don't need it valid; we're just asserting plans.
cat > "${DRY_RUN_DIR}/compose.yml" <<'YAML'
name: proof-eval-testdry
services:
  cmt-abc12345:
    image: fc-commit:abc12345
volumes:
  results-abc12345:
    name: results-abc12345
YAML
write_jsonl "${DRY_RUN_DIR}/raw/abc12345/agent.jsonl" "agent"

# --dry-run + --skip-copy avoids both docker and summary.py; we just want
# to confirm the plans are printed for the concat + symlink + summary.py.
DRY_OUT="$("${AGGREGATE_SCRIPT}" "${RUN_ID_DRY}" --dry-run --skip-copy 2>&1)" \
  || fail "expected dry-run to exit 0, got nonzero; output:\n${DRY_OUT}"

for token in 'DRY-RUN' 'agent.jsonl' 'ln -snf' 'summary.py'; do
  case "${DRY_OUT}" in
    *"${token}"*) ;;
    *) fail "dry-run output missing '${token}':\n${DRY_OUT}" ;;
  esac
done
pass "--dry-run --skip-copy prints concat + symlink + summary plans"

# ---------------------------------------------------------------------------
# Test 4 — --skip-copy happy-path with real summary.py invocation
# ---------------------------------------------------------------------------
# summary.py lives at experiments/summary.py and is callable via
# `uv run python summary.py` from experiments/. The test uses `uv run` so
# we need `uv` on PATH; skip that part of the assertion if unavailable.
RUN_ID_HAPPY="20240104-000000"
HAPPY_DIR="${FAKE_RESULTS}/${RUN_ID_HAPPY}"
mkdir -p "${HAPPY_DIR}/raw/aabbccdd"
mkdir -p "${HAPPY_DIR}/raw/11223344"

# Pre-stage per-SHA JSONLs (2 lines each, 2 SHAs, 2 modes -> 4 lines per mode).
write_jsonl "${HAPPY_DIR}/raw/aabbccdd/agent.jsonl" "agent"
write_jsonl "${HAPPY_DIR}/raw/11223344/agent.jsonl" "agent"
write_jsonl "${HAPPY_DIR}/raw/aabbccdd/baseline.jsonl" "baseline"
write_jsonl "${HAPPY_DIR}/raw/11223344/baseline.jsonl" "baseline"

if command -v uv >/dev/null 2>&1; then
  HAPPY_OUT="$("${AGGREGATE_SCRIPT}" "${RUN_ID_HAPPY}" --skip-copy 2>&1)" \
    || fail "happy-path expected exit 0, got nonzero; output:\n${HAPPY_OUT}"

  # Assert aggregated JSONLs have the expected line counts.
  agent_n=$(wc -l < "${HAPPY_DIR}/agent.jsonl" | tr -d ' ')
  baseline_n=$(wc -l < "${HAPPY_DIR}/baseline.jsonl" | tr -d ' ')
  [ "${agent_n}" = "4" ] || fail "expected agent.jsonl to have 4 lines, got ${agent_n}"
  [ "${baseline_n}" = "4" ] || fail "expected baseline.jsonl to have 4 lines, got ${baseline_n}"

  # Assert summary.json + summary.md exist.
  [ -f "${HAPPY_DIR}/summary.json" ] || fail "summary.json not written at ${HAPPY_DIR}/summary.json"
  [ -f "${HAPPY_DIR}/summary.md" ] || fail "summary.md not written at ${HAPPY_DIR}/summary.md"

  # Assert the printed AGGREGATE line counts match.
  case "${HAPPY_OUT}" in
    *"AGGREGATE ${RUN_ID_HAPPY}"*) ;;
    *) fail "output missing 'AGGREGATE ${RUN_ID_HAPPY}' header:\n${HAPPY_OUT}" ;;
  esac
  case "${HAPPY_OUT}" in
    *'agent.jsonl:    4 lines'*) ;;
    *) fail "output missing 'agent.jsonl:    4 lines' line:\n${HAPPY_OUT}" ;;
  esac
  case "${HAPPY_OUT}" in
    *'baseline.jsonl: 4 lines'*) ;;
    *) fail "output missing 'baseline.jsonl: 4 lines' line:\n${HAPPY_OUT}" ;;
  esac

  # Assert the latest symlink was updated.
  [ -L "${FAKE_RESULTS}/latest" ] || fail "latest symlink not created"
  latest_target="$(readlink "${FAKE_RESULTS}/latest")"
  [ "${latest_target}" = "${RUN_ID_HAPPY}" ] || fail "latest symlink -> '${latest_target}' (expected '${RUN_ID_HAPPY}')"

  pass "--skip-copy happy-path: aggregates, summary.{json,md}, latest symlink, and AGGREGATE counts line up"

  # -------------------------------------------------------------------------
  # Test 6 — idempotency: second run produces byte-identical summary.json
  # -------------------------------------------------------------------------
  SUMMARY_HASH_1="$(sha256sum "${HAPPY_DIR}/summary.json" | awk '{print $1}')"
  "${AGGREGATE_SCRIPT}" "${RUN_ID_HAPPY}" --skip-copy >/dev/null 2>&1 \
    || fail "idempotent re-run failed"
  SUMMARY_HASH_2="$(sha256sum "${HAPPY_DIR}/summary.json" | awk '{print $1}')"
  [ "${SUMMARY_HASH_1}" = "${SUMMARY_HASH_2}" ] \
    || fail "idempotent re-run produced different summary.json (hash1=${SUMMARY_HASH_1} hash2=${SUMMARY_HASH_2})"
  pass "idempotency: re-running against same run_id produces identical summary.json"
else
  warn_msg="[test_aggregate] SKIP: uv not on PATH; skipping summary.py-dependent happy-path + idempotency tests"
  printf '%s\n' "${warn_msg}" >&2
fi

# ---------------------------------------------------------------------------
# Test 5 — auto-select newest run_id when arg omitted
# ---------------------------------------------------------------------------
# Build a fresh fake results dir with exactly two YYYYMMDD-HHMMSS dirs —
# isolates the selector from test 3/4's run dirs. We point the aggregator's
# RESULTS_ROOT at a nested subdir by replacing the symlink.
AUTO_DIR="${TMP_DIR}/auto-results"
mkdir -p "${AUTO_DIR}/20240101-000000"
mkdir -p "${AUTO_DIR}/20240102-000000"

# Stage compose.yml + raw/ + JSONL in the newer dir so the rest of the
# script proceeds past the selector.
NEWER_DIR="${AUTO_DIR}/20240102-000000"
mkdir -p "${NEWER_DIR}/raw/xxxx1111"
cat > "${NEWER_DIR}/compose.yml" <<'YAML'
name: proof-eval-auto
services:
  cmt-xxxx1111:
    image: fc-commit:xxxx1111
volumes:
  results-xxxx1111:
    name: results-xxxx1111
YAML
write_jsonl "${NEWER_DIR}/raw/xxxx1111/agent.jsonl" "agent"

# Swap the symlink to point at AUTO_DIR.
rm -f "${REAL_RESULTS}"
ln -s "${AUTO_DIR}" "${REAL_RESULTS}"

AUTO_OUT="$("${AGGREGATE_SCRIPT}" --dry-run --skip-copy 2>&1)" \
  || fail "auto-select dry-run expected exit 0, got nonzero; output:\n${AUTO_OUT}"

case "${AUTO_OUT}" in
  *'defaulting to newest: 20240102-000000'*) ;;
  *) fail "expected auto-selector to pick 20240102-000000; got:\n${AUTO_OUT}" ;;
esac
# Double-check it didn't pick the older dir.
case "${AUTO_OUT}" in
  *'20240101-000000'*)
    fail "auto-select output leaked older dir 20240101-000000:\n${AUTO_OUT}"
    ;;
esac
pass "auto-select picks newest YYYYMMDD-HHMMSS directory"

printf '[test_aggregate] all tests passed\n' >&2
