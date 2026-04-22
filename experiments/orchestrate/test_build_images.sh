#!/usr/bin/env bash
# experiments/orchestrate/test_build_images.sh
#
# Bats-less smoke test for build-images.sh (issue #18). Exercises:
#   1. `bash -n` syntax check.
#   2. `--help` renders usage listing all four subcommands.
#   3. `--dry-run --all` with a stubbed `docker` in PATH and
#      BUILD_IMAGES_SHAS_OVERRIDE / BUILD_IMAGES_COQ_OVERRIDE set to avoid
#      hitting commits_from_meta or detect_coq_version.py — asserts that the
#      dry-run log mentions at least one fc-base, fc-deps, and fc-commit.
#
# Exit 0 on success, nonzero on first failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_SCRIPT="${SCRIPT_DIR}/build-images.sh"

fail() { printf '[test_build_images] FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '[test_build_images] PASS: %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Test 1 — bash -n syntax check
# ---------------------------------------------------------------------------
if ! bash -n "${BUILD_SCRIPT}"; then
  fail "bash -n on build-images.sh returned nonzero"
fi
pass "bash -n build-images.sh"

# ---------------------------------------------------------------------------
# Test 2 — --help lists all four subcommands
# ---------------------------------------------------------------------------
help_out="$("${BUILD_SCRIPT}" --help 2>&1)"
for flag in --all --base --deps --commit; do
  case "${help_out}" in
    *"${flag}"*) ;;
    *) fail "--help output missing '${flag}': ${help_out}" ;;
  esac
done
pass "--help lists --all, --base, --deps, --commit"

# ---------------------------------------------------------------------------
# Test 3 — --dry-run --all with stubbed docker
# ---------------------------------------------------------------------------
STUB_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fc-stub.XXXXXX")"
trap 'rm -rf -- "${STUB_DIR}"' EXIT

# Stub `docker`: log its invocation & exit 0. Also handles `docker buildx
# version` (return 0 to let the script pick the buildx code path) and
# `docker image inspect` (return 1 so nothing is reported as existing).
cat > "${STUB_DIR}/docker" <<'STUB'
#!/usr/bin/env bash
printf 'STUB-DOCKER: %s\n' "$*" >&2
case "$1" in
  buildx)
    case "${2:-}" in
      version) exit 0 ;;
      build)   exit 0 ;;
    esac
    exit 0
    ;;
  image)
    # Always report not-present so we exercise the build path.
    exit 1
    ;;
  build)
    exit 0
    ;;
esac
exit 0
STUB
chmod +x "${STUB_DIR}/docker"

# Put stub first on PATH.
export PATH="${STUB_DIR}:${PATH}"

# Inject fake SHAs + a fixed Coq version so we don't need the real fiat-crypto
# checkout or call detect_coq_version.py. Two SHAs with the same Coq version
# lets us confirm base/deps deduplication AND two fc-commit builds.
export BUILD_IMAGES_SHAS_OVERRIDE="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
export BUILD_IMAGES_COQ_OVERRIDE="8.20.0"

DRY_OUT="$("${BUILD_SCRIPT}" --all --dry-run 2>&1)" || fail "--dry-run --all exited nonzero: ${DRY_OUT}"

# Must show each layer at least once.
grep -q 'DRY-RUN:.*fc-base:8.20.0'   <<<"${DRY_OUT}" \
  || fail "dry-run output missing fc-base build:\n${DRY_OUT}"
grep -q 'DRY-RUN:.*fc-deps:8.20.0'   <<<"${DRY_OUT}" \
  || fail "dry-run output missing fc-deps build:\n${DRY_OUT}"
grep -q 'DRY-RUN:.*fc-commit:aaaaaaaaaaaa' <<<"${DRY_OUT}" \
  || fail "dry-run output missing fc-commit build for SHA a:\n${DRY_OUT}"
grep -q 'DRY-RUN:.*fc-commit:bbbbbbbbbbbb' <<<"${DRY_OUT}" \
  || fail "dry-run output missing fc-commit build for SHA b:\n${DRY_OUT}"

# Base/deps must only be emitted once each (unique Coq version dedup).
base_count="$(grep -c 'DRY-RUN:.*fc-base:8.20.0'  <<<"${DRY_OUT}" || true)"
deps_count="$(grep -c 'DRY-RUN:.*fc-deps:8.20.0'  <<<"${DRY_OUT}" || true)"
[ "${base_count}" -eq 1 ] \
  || fail "expected exactly 1 fc-base build, got ${base_count}:\n${DRY_OUT}"
[ "${deps_count}" -eq 1 ] \
  || fail "expected exactly 1 fc-deps build, got ${deps_count}:\n${DRY_OUT}"

pass "--dry-run --all emits base, deps, and per-SHA commit builds"

printf '[test_build_images] all tests passed\n' >&2
