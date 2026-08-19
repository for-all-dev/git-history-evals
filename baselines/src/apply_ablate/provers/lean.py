"""Lean backend: `lake build` to prepare (real writes, in-place on the pristine repo
only), bare `lean` to check (never writes anywhere).

Mirrors `lean-ablator/Main.lean`'s `--check-build` (`checkCompiles`) for what gets
compiled. `check` used to run `lake env lean <file>`, but `lake env` elaborates and
resolves the *workspace* from `cwd` — and `cwd` for a challenge is the symlink-overlay
work copy built by `apply.overlay_repo`, whose `.lake` is a symlink back to the
pristine source tree. Lake's git re-clones and compiled-lakefile-config cache writes
then followed that symlink straight into the shared source tree and corrupted it
(#119) — three separate build-environment bugs, of which this was the worst, cost the
corpus ~3,700 valid challenges. Bare `lean <file>` with `LEAN_PATH` set never performs
git operations, never elaborates a lakefile, and never writes `.lake`, so it is now the
*only* way `check` invokes the toolchain — there is no write channel left to redirect.

The one thing `lake env` did that bare `lean` cannot reconstruct by globbing
`.lake/build/lib` is the FFI environment (`LEAN_CC`, `LD_LIBRARY_PATH`, …) that a
package with a C build step (e.g. hex-dev) needs. `prepare` snapshots
`lake env printenv` once per repo — keyed by the *pristine* root, so an overlay work
copy of the same repo shares the snapshot rather than re-invoking lake — and `check`
reuses it. The snapshot command carries `--no-build` so that even this one remaining
`lake env` call fails fast instead of starting a build or clone if the config is stale;
it degrades to a bare `LEAN_PATH` (no FFI vars) rather than writing anything.
"""

from __future__ import annotations

import os
from pathlib import Path

from apply_ablate.provers.base import CheckResult, run

_LAKEFILES = ("lakefile.toml", "lakefile.lean")


def _lean_path(root: Path) -> str:
    """Colon-joined olean search path across the package's own build lib and every
    dependency's, approximating what `lake env` would export. Covers both the
    `build/lib` and `build/lib/lean` layouts used across Lean toolchains."""
    parts: list[str] = []
    for base in [root, *sorted((root / ".lake" / "packages").glob("*"))]:
        for sub in ("lib", "lib/lean"):
            d = base / ".lake" / "build" / sub
            if d.is_dir():
                parts.append(str(d))
    return os.pathsep.join(parts)


def _lake_root(start: Path) -> Path:
    """Nearest ancestor (inclusive of `start`'s dir) containing a lakefile."""
    here = start if start.is_dir() else start.parent
    for d in (here, *here.parents):
        if any((d / name).exists() for name in _LAKEFILES):
            return d
    return here


def _canonical_root(root: Path) -> Path:
    """Resolve `root` to the physical directory its lakefile actually lives in.

    In an overlay work copy the lakefile is a symlink back to the pristine source, so
    `root` (a path under the overlay) and the pristine repo's own root are two distinct
    `Path`s that name the same package. Resolving through the lakefile normalises both
    to one key, so `prepare(src)` and `check(overlay)` share a single env snapshot.
    """
    for name in _LAKEFILES:
        p = root / name
        if p.exists():
            return p.resolve().parent
    return root.resolve()


def _snapshot_lake_env(root: Path, *, timeout: int) -> dict[str, str] | None:
    """One read-only capture of `lake env`'s exports (`KEY=value` lines from the real
    `printenv`). `--no-build` makes lake exit immediately rather than build/clone if the
    config is stale, so this can never become another write channel; a failure just
    means no snapshot (callers fall back to a bare `LEAN_PATH`)."""
    res = run(["lake", "--no-build", "env", "printenv"], cwd=root, timeout=timeout)
    if not res.ok:
        return None
    env: dict[str, str] = {}
    for line in res.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            env[key] = value
    return env or None


class LeanProver:
    key = "lean"

    def __init__(self) -> None:
        # Per-instance (the registry holds one LeanProver for the process), keyed by
        # `_canonical_root` so every overlay work copy of a repo reuses one snapshot.
        self._env_cache: dict[Path, dict[str, str] | None] = {}

    def prepare(self, repo: Path, *, timeout: int) -> CheckResult:
        root = _lake_root(repo)
        res = run(["lake", "build"], cwd=root, timeout=timeout)
        self._env_cache[_canonical_root(root)] = _snapshot_lake_env(
            root, timeout=timeout
        )
        return res

    def _env_for(self, root: Path, *, timeout: int) -> dict[str, str] | None:
        """Reuse the snapshot `prepare` took; if `prepare` was never called for this
        repo (e.g. the baseline driver, which only ever calls `check`), take one lazily
        — always against the *pristine* root (never the overlay `root` argument), so a
        first-use snapshot never runs `lake env` with `cwd` inside a symlink overlay."""
        canonical = _canonical_root(root)
        if canonical not in self._env_cache:
            self._env_cache[canonical] = _snapshot_lake_env(canonical, timeout=timeout)
        return self._env_cache[canonical]

    def check(
        self, repo: Path, rel: Path, *, allow_holes: bool, timeout: int
    ) -> CheckResult:
        target = (repo / rel).resolve()
        root = _lake_root(target)
        env = dict(os.environ)
        snapshot = self._env_for(root, timeout=timeout)
        if snapshot:
            env.update(snapshot)
        lp = _lean_path(root)
        if lp:
            env["LEAN_PATH"] = (
                lp + os.pathsep + env["LEAN_PATH"] if env.get("LEAN_PATH") else lp
            )
        return run(["lean", str(target)], cwd=root, timeout=timeout, env=env)
