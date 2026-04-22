#!/usr/bin/env python3
"""Generate a per-run docker compose.yml for the proof-eval pipeline.

Emits one service per fiat-crypto SHA plus one named volume per SHA, so
re-runs against the same SHA accumulate in the same on-disk volume
(separated by ``/results/<run_id>/`` inside). Snapshotted into
``experiments/results/<run_id>/compose.yml`` so every run is reproducible
from the file alone.

Stdlib only. We render YAML by string templating rather than pulling in
PyYAML -- the emitted shape is small and fixed, and we validate the
output with ``docker compose config --quiet`` afterwards.

CLI
---
    python3 gen-compose.py \
        --run-id <id> \
        --mode <baseline|agent|both> \
        --shas <sha1,sha2,...> \
        [--out <path>] \
        [--skip-validate]

Default ``--out`` is ``experiments/results/<run_id>/compose.yml`` (relative
to the repo root, derived from this file's location).

Design notes
------------
- Each service's ``RUN_CONFIG_JSON`` is produced by sourcing
  ``experiments/orchestrate/lib.sh`` and calling ``run_config_json <sha>``
  in a subprocess. We don't re-implement the logic here; if a SHA has no
  matching meta.json the helper returns an empty ``slots`` list, which is
  a legitimate (if unusual) state -- we pass it through.
- The literal string ``${ANTHROPIC_API_KEY}`` is emitted into the YAML so
  that ``docker compose`` performs the env interpolation at launch rather
  than us baking the value into an artifact that may be committed.
- YAML anchors (``x-defaults: &defaults``) deduplicate the ``restart``
  field and the host-experiments volume mount; per-service fields that
  vary (image, container_name, environment, command) are set inline.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


# The per-container command fragments. We embed them as f-strings later.
# Inside the container, /results is the named volume, and /work is the
# checkout baked by docker/commit.Dockerfile. The outer entry point
# (``_command_for_mode`` below) handles mkdir/cd once; these fragments
# only carry the per-mode runner invocation.
_RUN_BASELINE = (
    'uv run eval-baseline --run-id {run_id} '
    '--out /results/{run_id}/baseline.jsonl'
)
_RUN_AGENT = (
    'uv run eval-agent --run-id {run_id} '
    '--out /results/{run_id}/agent.jsonl'
)


def _repo_root() -> Path:
    """Return the repo root, derived from this file's location.

    This file lives at ``<repo>/experiments/orchestrate/gen-compose.py``,
    so the repo root is two parents up.
    """
    return Path(__file__).resolve().parent.parent.parent


def _sha_prefix(sha: str) -> str:
    """First 8 chars of a SHA, lowercased. Short enough for container names,
    long enough to be practically unique across a single run's targets."""
    return sha.strip().lower()[:8]


def _command_for_mode(mode: str, run_id: str) -> str:
    """Build the bash -lc command string for the given mode.

    ``mkdir -p /results/<run_id> && cd /work`` is the shared prelude;
    the per-mode suffix chains one or both runners with ``&&``.
    """
    prelude = f"mkdir -p /results/{run_id} && cd /work"
    if mode == "baseline":
        runners = _RUN_BASELINE.format(run_id=run_id)
    elif mode == "agent":
        runners = _RUN_AGENT.format(run_id=run_id)
    elif mode == "both":
        runners = (
            _RUN_BASELINE.format(run_id=run_id)
            + " && "
            + _RUN_AGENT.format(run_id=run_id)
        )
    else:
        raise ValueError(f"unknown mode: {mode!r}")
    return f"{prelude} && {runners}"


def _run_config_json_for(sha: str, lib_sh: Path) -> str:
    """Invoke ``run_config_json <sha>`` from lib.sh, return stdout (trimmed).

    We compact the JSON to a single line with ``json.loads`` + ``json.dumps``
    so it embeds cleanly in YAML without multi-line-string gymnastics.
    """
    if not lib_sh.is_file():
        raise FileNotFoundError(f"lib.sh not found at {lib_sh}")
    result = subprocess.run(
        ["bash", "-c", f'source "{lib_sh}" && run_config_json "{sha}"'],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"run_config_json({sha!r}) failed with exit code "
            f"{result.returncode}: {result.stderr.strip()}"
        )
    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError(
            f"run_config_json({sha!r}) produced no output "
            f"(is the SHA present in any meta.json?)"
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"run_config_json({sha!r}) produced invalid JSON: {exc}"
        ) from exc
    return json.dumps(parsed, separators=(",", ":"))


def _yaml_escape_double(s: str) -> str:
    """Escape a string so it can be placed inside a YAML double-quoted scalar.

    YAML double-quoted strings recognise the usual JSON-style escapes. The
    two characters we need to guard against are backslash and the closing
    double quote; newlines get ``\\n``.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _emit_compose_yaml(
    *,
    run_id: str,
    mode: str,
    shas: list[str],
    repo_root: Path,
    lib_sh: Path,
) -> str:
    """Build the full compose.yml text as a string."""
    host_experiments = str(repo_root / "experiments")

    lines: list[str] = []
    lines.append(f"name: proof-eval-{run_id}")
    lines.append("")

    # Anchored defaults: fields every service inherits unchanged. We keep
    # this tiny -- anything service-specific (env, command, image, ...)
    # stays inline so it's obvious at a glance what varies per-SHA.
    lines.append("x-defaults: &defaults")
    lines.append('  restart: "no"')
    lines.append("")

    lines.append("services:")
    for sha in shas:
        prefix = _sha_prefix(sha)
        run_config_json = _run_config_json_for(sha, lib_sh)
        command = _command_for_mode(mode, run_id)

        lines.append(f"  cmt-{prefix}:")
        lines.append("    <<: *defaults")
        lines.append(f"    image: fc-commit:{prefix}")
        lines.append(f"    container_name: fc-run-{prefix}-{run_id}")
        lines.append("    environment:")
        # The literal ${ANTHROPIC_API_KEY} string: compose resolves at
        # launch. Single-quoted so YAML doesn't try to interpret the $.
        lines.append("      ANTHROPIC_API_KEY: '${ANTHROPIC_API_KEY}'")
        # RUN_CONFIG_JSON is a JSON object; we double-quote it as a YAML
        # scalar and escape inner double-quotes.
        lines.append(
            f'      RUN_CONFIG_JSON: "{_yaml_escape_double(run_config_json)}"'
        )
        lines.append(f'      RUN_ID: "{run_id}"')
        lines.append(f'      MODE: "{mode}"')
        lines.append("    volumes:")
        lines.append(f'      - "results-{prefix}:/results"')
        lines.append(
            f'      - "{host_experiments}:/work/host-experiments:ro"'
        )
        lines.append("    command:")
        lines.append('      - "bash"')
        lines.append('      - "-lc"')
        lines.append(f'      - "{_yaml_escape_double(command)}"')
    lines.append("")

    lines.append("volumes:")
    for sha in shas:
        prefix = _sha_prefix(sha)
        lines.append(f"  results-{prefix}:")
        lines.append(f"    name: results-{prefix}")
    lines.append("")

    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a per-run docker compose.yml for the proof-eval pipeline.",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Stable identifier for this evaluation run (used in compose project name, container names, and result paths).",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=("baseline", "agent", "both"),
        help="Which evaluator(s) to run per commit.",
    )
    parser.add_argument(
        "--shas",
        required=True,
        help="Comma-separated list of fiat-crypto SHAs to emit services for.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path. Defaults to experiments/results/<run_id>/compose.yml (repo-relative).",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip the final `docker compose config --quiet` validation step.",
    )
    return parser.parse_args(argv)


def _validate_with_compose(out_path: Path) -> int:
    """Run ``docker compose -f <out> config --quiet``. Returns exit code."""
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(out_path), "config", "--quiet"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        # No docker on PATH -- tell the caller how to bypass next time.
        print(
            f"docker not found on PATH; wrote {out_path} but skipped validation. "
            f"Pass --skip-validate to silence.",
            file=sys.stderr,
        )
        return 0
    if result.returncode != 0:
        print(
            f"docker compose validation FAILED for {out_path}:",
            file=sys.stderr,
        )
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    shas = [s.strip() for s in args.shas.split(",") if s.strip()]
    if not shas:
        print("--shas produced an empty list", file=sys.stderr)
        return 2

    repo_root = _repo_root()
    lib_sh = repo_root / "experiments" / "orchestrate" / "lib.sh"

    if args.out is None:
        out_path = repo_root / "experiments" / "results" / args.run_id / "compose.yml"
    else:
        out_path = Path(args.out).resolve()

    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        content = _emit_compose_yaml(
            run_id=args.run_id,
            mode=args.mode,
            shas=shas,
            repo_root=repo_root,
            lib_sh=lib_sh,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"gen-compose: {exc}", file=sys.stderr)
        return 1

    out_path.write_text(content, encoding="utf-8")
    print(f"wrote {out_path}")

    if args.skip_validate:
        return 0

    rc = _validate_with_compose(out_path)
    if rc != 0:
        print(
            f"gen-compose: compose file at {out_path} is invalid (see above).",
            file=sys.stderr,
        )
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
