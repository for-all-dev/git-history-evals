#!/usr/bin/env bash
# experiments/orchestrate/lib.sh
#
# Shared bash helpers sourced by every script under experiments/orchestrate/.
# Encapsulates three concerns:
#   - commits_from_meta:   enumerate unique commit_hash values across every
#                          experiments/{experiments3,admitted-proofs}/*/meta.json
#   - slots_for_commit:    enumerate per-slot evaluation work items for a SHA
#                          (condition A from admitted-proofs, condition B from
#                          experiments3 with multiple deletion sizes)
#   - run_config_json:     emit the JSON object fed to the in-container runner
#                          via the RUN_CONFIG_JSON env var
#
# Sourceable, not executable. No top-level side effects beyond a one-time jq
# availability assertion (guarded by LIB_SH_LOADED).

if [ -z "${LIB_SH_LOADED:-}" ]; then
  if ! command -v jq >/dev/null 2>&1; then
    echo "experiments/orchestrate/lib.sh: jq is required but not found on PATH" >&2
    exit 127
  fi
  LIB_SH_LOADED=1
fi

# Resolve the experiments/ root (parent of orchestrate/) regardless of where
# this file is sourced from. BASH_SOURCE[0] is the path to this file.
_LIB_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_EXPERIMENTS_ROOT="$(cd "${_LIB_SH_DIR}/.." && pwd)"

# commits_from_meta
#
# Prints unique commit_hash values — one per line — discovered by iterating
# every experiments/{experiments3,admitted-proofs}/*/meta.json and extracting
# .commit_hash with jq. Order is sorted/uniq'd for determinism.
commits_from_meta() {
  local meta
  local -a metas=()
  for meta in \
    "${_EXPERIMENTS_ROOT}/experiments3"/*/meta.json \
    "${_EXPERIMENTS_ROOT}/admitted-proofs"/*/meta.json; do
    [ -f "${meta}" ] || continue
    metas+=("${meta}")
  done
  if [ "${#metas[@]}" -eq 0 ]; then
    return 0
  fi
  jq -r '.commit_hash' "${metas[@]}" | sort -u
}

# slots_for_commit <sha>
#
# Prints colon-delimited <condition>:<slot_name>:<deletion_size>:<challenge_file>
# lines for every slot whose meta.json has commit_hash == <sha>.
#
# Condition A  (deletion_size = -1, challenge_file = challenge.v):
#   source = experiments/admitted-proofs/<slot>/
# Condition B  (deletion_size ∈ {3, 5, 7, 10, 15}, challenge_file = challengeN.v):
#   source = experiments/experiments3/<slot>/
#   one line per existing challengeN.v file in the slot directory.
slots_for_commit() {
  local sha="$1"
  if [ -z "${sha}" ]; then
    echo "slots_for_commit: missing <sha> argument" >&2
    return 2
  fi

  local meta slot_dir slot_name slot_sha
  local -a deletion_sizes=(3 5 7 10 15)
  local size challenge

  # Condition A — admitted-proofs
  for meta in "${_EXPERIMENTS_ROOT}/admitted-proofs"/*/meta.json; do
    [ -f "${meta}" ] || continue
    slot_sha="$(jq -r '.commit_hash' "${meta}")"
    [ "${slot_sha}" = "${sha}" ] || continue
    slot_dir="$(dirname "${meta}")"
    slot_name="$(basename "${slot_dir}")"
    if [ -f "${slot_dir}/challenge.v" ]; then
      printf 'A:%s:-1:challenge.v\n' "${slot_name}"
    fi
  done

  # Condition B — experiments3, one line per existing challengeN.v
  for meta in "${_EXPERIMENTS_ROOT}/experiments3"/*/meta.json; do
    [ -f "${meta}" ] || continue
    slot_sha="$(jq -r '.commit_hash' "${meta}")"
    [ "${slot_sha}" = "${sha}" ] || continue
    slot_dir="$(dirname "${meta}")"
    slot_name="$(basename "${slot_dir}")"
    for size in "${deletion_sizes[@]}"; do
      challenge="challenge${size}.v"
      if [ -f "${slot_dir}/${challenge}" ]; then
        printf 'B:%s:%s:%s\n' "${slot_name}" "${size}" "${challenge}"
      fi
    done
  done
}

# run_config_json <sha>
#
# Emits a JSON object of the shape
#   {"commit": "<sha>", "slots": [{"condition": ..., "slot": ..., ...}, ...]}
# ready to be passed via the RUN_CONFIG_JSON env var to the in-container runner.
# Built from slots_for_commit output via jq -n --argjson.
run_config_json() {
  local sha="$1"
  if [ -z "${sha}" ]; then
    echo "run_config_json: missing <sha> argument" >&2
    return 2
  fi

  local slots_json
  slots_json="$(
    slots_for_commit "${sha}" \
      | jq -R -s '
          split("\n")
          | map(select(length > 0))
          | map(split(":"))
          | map({
              condition: .[0],
              slot: .[1],
              deletion_size: (.[2] | tonumber),
              challenge_file: .[3]
            })
        '
  )"

  jq -n --arg sha "${sha}" --argjson slots "${slots_json}" \
    '{commit: $sha, slots: $slots}'
}
