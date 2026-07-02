"""Isabelle backend.

Two modes, chosen automatically:

* **Session mode** (real AFP/l4v theories). When the target theory lives in a
  directory whose `ROOT` declares a session that lists it, we synthesize a throwaway
  session holding just the target's transitive *in-session* import closure plus the
  target itself, in one copy dir:

      session "AblateCheck_<sess>_<thy>" = "<parent>" + sessions <needed> + theories <closure ∪ thy>

  The closure and the target must share one session: bare `imports Foo` resolve only
  *within* a session, so we cannot pre-bake the deps into an ancestor heap and import
  them by bare name (that was tried — cross-session imports require qualified names the
  real sources don't use). Isabelle caches the session heap, so an unchanged target
  re-checks instantly and a changed one rebuilds only this (deliberately shallow)
  closure rather than the whole upstream session. Only the cross-session dependencies
  the closure actually imports (e.g. `Finite-Map-Extras`) are added via `-d <their own
  dir>` — never the whole `thys/`, which would re-register the real session and clash
  on theory names. Unchanged dep sources are copied once; the target is copied afresh
  each call so the latest submission is what gets checked.

  NB: replaying `smt`/`sledgehammer` proofs (common in l4v and parts of AFP) needs the
  external solvers (z3/veriT/cvc) on PATH; a bare Isabelle without them will hang on
  those proofs regardless of this machinery.

* **Throwaway-HOL mode** (synthetic `imports Main` theories, our fixtures). No
  enclosing session ROOT is found, so we fall back to a single `AblateCheck = HOL`
  session listing just the target. Mirrors `isabelle-ablator/rust/src/build_check.rs`.

We always build with `quick_and_dirty=true` so the base heap is option-stable across
the challenge (`allow_holes=True`) and solution (`allow_holes=False`) checks; for the
solution we additionally reject any `sorry`/`oops` left in the target *textually*,
which is what `allow_holes=False` means here (the harness already guards submissions
the same way).

Env: Isabelle needs a writable `ISABELLE_HOME_USER`/`HOME`. We inherit them if set
(e.g. a shared warm-heap dir); otherwise default to a workspace-local dir. Override
these *only* for Isabelle — overriding `HOME` for rustup/cargo breaks the toolchain.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterable

from apply_ablate.provers.base import CheckResult, run

# ROOT/theory-header keywords that terminate a `sessions`/`theories` name list.
_ROOT_KW = {
    "session",
    "options",
    "sessions",
    "theories",
    "directories",
    "document_files",
    "document_theories",
    "export_files",
    "export_classpath",
    "chapter",
}
_HEADER_END = {"begin", "keywords", "abbrevs"}
_HOLE_RE = re.compile(r"\b(sorry|oops)\b")


def strip_comments(text: str) -> str:
    """Drop Isabelle `(* … *)` comments (nestable) so header parsing isn't fooled by
    the word "theory"/"imports" appearing in a comment (e.g. l4v's `(* A theory of … *)`
    above the real `theory <Name>` header)."""
    out: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        two = text[i : i + 2]
        if two == "(*":
            depth += 1
            i += 2
        elif two == "*)" and depth > 0:
            depth -= 1
            i += 2
        elif depth == 0:
            out.append(text[i])
            i += 1
        else:
            i += 1
    return "".join(out)


def theory_name(content: str) -> str | None:
    """The `<Name>` from a `theory <Name>` header."""
    toks = strip_comments(content).split()
    for i, tok in enumerate(toks):
        if tok == "theory" and i + 1 < len(toks):
            return toks[i + 1].strip('"')
    return None


def _isabelle_env(repo: Path) -> dict[str, str]:
    env = dict(os.environ)
    # Isabelle's own `etc/settings` RECOMPUTES `ISABELLE_HOME_USER` from `HOME`
    # (as `$HOME/.isabelle/Isabelle<ver>`), ignoring any `ISABELLE_HOME_USER` we export.
    # So to point it at a prebuilt heap store we must redirect `HOME` — but only for the
    # isabelle subprocess (redirecting the whole process's HOME breaks uv/nix caches).
    # `ABLATE_ISABELLE_HOME=<dir>` sets that subprocess HOME; heaps then resolve under
    # `<dir>/.isabelle/Isabelle<ver>/heaps` and components come from there (so the user's
    # real `~/.isabelle` — e.g. a registered AFP that would clash with l4v's bundled
    # Word_Lib — is not consulted).
    heap_home = env.get("ABLATE_ISABELLE_HOME")
    if heap_home:
        env["HOME"] = heap_home
        # Mirror the path Isabelle derives from HOME so our own ablate-sessions/heaps
        # land in (and reuse) the same store. Prefer an existing `Isabelle<ver>` dir.
        base = Path(heap_home) / ".isabelle"
        versions = sorted(base.glob("Isabelle*")) if base.is_dir() else []
        env["ISABELLE_HOME_USER"] = str(
            versions[-1] if versions else base / "Isabelle2025"
        )
    elif "ISABELLE_HOME_USER" not in env:
        home = (repo / ".isabelle-home").resolve()
        home.mkdir(parents=True, exist_ok=True)
        env["ISABELLE_HOME_USER"] = str(home)
        env.setdefault("HOME", str(home))
    return env


def _fresh_dir(p: Path) -> None:
    """Remove `p` and recreate it empty. The synthesized session source dirs are rebuilt
    every run; a stale mirror from an earlier run (e.g. a whole-dir symlink from an older
    code path) would otherwise collide with the current mirroring or re-introduce a
    duplicate-directory clash. The built heap lives elsewhere (heaps/), so this is cheap —
    identical content ⇒ isabelle reuses the cached heap."""
    if p.exists() or p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
        if p.is_symlink():
            p.unlink(missing_ok=True)
    p.mkdir(parents=True, exist_ok=True)


def _link_session_files(session_dir: Path, dst: Path, _top: bool = True) -> None:
    """Mirror `session_dir` into `dst`, making SUBDIRECTORIES real (recursing) and
    symlinking only FILES. Skips ROOT/ROOTS at the top (the caller writes its own).

    Subdirs must be real (not whole-dir symlinks): the synthesized session declares its
    subdirs via `directories`, and if `dst/wp` were a symlink to the source `lib/Monads/wp`
    it would be the SAME physical directory the real l4v session (registered via
    `-d data/l4v`) also uses — Isabelle then aborts with "Duplicate use of directory".
    Real dirs + per-file symlinks give a distinct path while still resolving `.thy`/ML
    resources. (Symlinked source subdirs from the apply-overlay are recursed into too.)"""
    dst.mkdir(parents=True, exist_ok=True)
    try:
        entries = sorted(session_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        if _top and entry.name in ("ROOT", "ROOTS"):
            continue
        link = dst / entry.name
        if entry.is_dir():  # real OR symlinked-to-dir → mirror as a real dir, recurse
            _link_session_files(entry, link, _top=False)
        elif not (link.exists() or link.is_symlink()):
            try:
                link.symlink_to(entry.resolve())
            except OSError:
                pass


def _extra_dirs() -> list[str]:
    """Extra `-d <dir>` session-search dirs from `ABLATE_ISABELLE_DIRS` (colon-separated).

    The closure/dep discovery here assumes an AFP-style `<root>/<Session>/ROOT` layout;
    repos like l4v scatter sessions (lib/, spec/, …) under a top-level ROOTS file, so
    their cross-session deps (`Monads`, `Word_Lib`, …) aren't found by `_dep_dirs`.
    Pointing `ABLATE_ISABELLE_DIRS` at such a repo root lets `isabelle build` read its
    ROOTS and resolve every session (against prebuilt heaps). Empty by default — no
    effect on Coq/Lean/AFP runs. Returns flat `["-d", d, ...]`."""
    raw = os.environ.get("ABLATE_ISABELLE_DIRS", "")
    out: list[str] = []
    for d in raw.split(":"):
        d = d.strip()
        if d and Path(d).is_dir():
            out += ["-d", d]
    return out


def _tokenize(text: str) -> list[str]:
    """Split ROOT/header text into words, quoted strings, and `= + [ ]` symbols."""
    return re.findall(r'"[^"]*"|[^\s\[\]=+]+|[\[\]=+]', text)


def imports_of(thy: Path) -> list[str]:
    """The theory names in a `.thy` file's `imports ... begin` header."""
    try:
        text = thy.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Header is everything up to the first top-level `begin`.
    toks = _tokenize(strip_comments(text))
    out: list[str] = []
    collecting = False
    for tok in toks:
        if tok in _HEADER_END:
            break
        if tok == "imports":
            collecting = True
            continue
        if not collecting:
            continue
        if tok in ("theory", "imports") or tok in {"[", "]", "=", "+"}:
            continue
        out.append(tok.strip('"'))
    return out


@dataclass
class Session:
    name: str
    parent: str
    dep_sessions: list[str]
    theories: list[str]
    session_dir: Path
    afp_root: Path | None  # the `thys/` dir holding sibling AFP sessions, if any
    directories: list[str] = field(default_factory=list)  # declared subdirs (nondet, …)


def parse_root(root: Path) -> Session | None:
    """Parse the first `session` stanza of a ROOT file."""
    # Strip `(* … *)` comments first — l4v ROOTs annotate blocks (e.g.
    # `directories (* not in the AFP: *)`) and those words must not be read as entries.
    toks = _tokenize(strip_comments(root.read_text(encoding="utf-8", errors="replace")))
    n = len(toks)
    # Locate `session <name> [(group)] [in <dir>] = <parent> +`. l4v uses the
    # `(group)` annotation (e.g. `session Lib (lib) = Word_Lib`) and `in <subdir>`
    # (e.g. `session CLib (lib) in clib = CParser`), which a bare `name = parent`
    # parser misses — falling back to throwaway mode and breaking cross-session deps.
    name = parent = None
    in_dir: str | None = None
    start = 0
    for i, tok in enumerate(toks):
        if tok == "session" and i + 1 < n:
            j = i + 2  # scan to the '=' separating the header from the parent
            local_in: str | None = None
            while j < n and toks[j] != "=" and toks[j] != "session":
                if toks[j] == "in" and j + 1 < n:
                    local_in = toks[j + 1].strip('"')
                    j += 2
                    continue
                j += 1
            if j < n and toks[j] == "=" and j + 1 < n:
                name = toks[i + 1].strip('"')
                parent = toks[j + 1].strip('"')
                in_dir = local_in
                start = i
                break
    if name is None or parent is None:
        return None

    dep_sessions: list[str] = []
    theories: list[str] = []
    directories: list[str] = []
    block: str | None = None
    skip_block = False
    i = start
    while i < n:
        tok = toks[i]
        if tok == "session" and i != start:
            break  # a second session stanza — stop at the first
        if tok in _ROOT_KW:
            block = tok if tok in ("sessions", "theories", "directories") else None
            skip_block = False
            if tok == "theories" and i + 1 < n and toks[i + 1] == "[":
                # `theories [condition = ...]` — skip conditional groups (e.g. GHC).
                j = i + 1
                bracket = []
                while j < n and toks[j] != "]":
                    bracket.append(toks[j])
                    j += 1
                skip_block = "condition" in bracket
                i = j + 1
                continue
            i += 1
            continue
        if tok in ("[",):  # an options group for the current block — skip it
            while i < n and toks[i] != "]":
                i += 1
            i += 1
            continue
        if tok in ("=", "+", "]"):
            i += 1
            continue
        clean = tok.strip('"')
        if block == "sessions":
            dep_sessions.append(clean)
        elif block == "theories" and not skip_block:
            theories.append(clean)
        elif block == "directories":
            directories.append(clean)
        i += 1

    # `session … in <dir>` puts the session's theories under root.parent/<dir>.
    session_dir = (root.parent / in_dir) if in_dir else root.parent
    # AFP layout: <afp>/thys/<Session>/ROOT, siblings under <afp>/thys/.
    afp_root = root.parent.parent if (root.parent.parent / "ROOTS").exists() else None
    return Session(
        name, parent, dep_sessions, theories, session_dir, afp_root, directories
    )


def discover_session(target: Path) -> Session | None:
    """Find the nearest enclosing session ROOT that owns `target`'s theory.

    A theory belongs to a session if the ROOT lists it in `theories` OR its `.thy` lives
    under the session's directory. The latter is essential for l4v-style ROOTs (e.g.
    Word_Lib), which list only a few top-level theories and rely on imports to pull in
    the rest — so `Least_significant_bit` isn't listed even though it's part of Word_Lib.
    Without the directory check we'd fall back to throwaway-HOL mode and lose the ROOT's
    declared `sessions` (HOL-Library, …), making the build unresolvable."""
    name = target.stem
    tgt = target.resolve()
    here = target.parent
    for d in (here, *here.parents):
        root = d / "ROOT"
        if root.exists():
            s = parse_root(root)
            if s is None:
                continue
            if name in s.theories:
                return s
            try:
                tgt.relative_to(s.session_dir.resolve())
                return s  # theory file lives within this session's directory
            except ValueError:
                pass
    return None


def _theory_file(session: Session, name: str) -> Path:
    """Resolve a theory's base name to its `.thy` file within the session.

    Sessions may scatter theories across declared `directories` subdirs (l4v's Monads
    puts them under `nondet/`, `trace/`, …), so the file isn't always at
    `session_dir/{name}.thy`. Prefer the direct path, else the first match anywhere under
    session_dir (theory names are unique within a session)."""
    direct = session.session_dir / f"{name}.thy"
    if direct.is_file():
        return direct
    # Walk following symlinks: the apply-overlay symlinks session subdirs (wp/, trace/)
    # back to source, and `Path.rglob` does NOT descend symlinked dirs by default
    # (Python 3.13+), so it would miss theories like wp/WPSimp.thy — breaking the closure.
    fname = f"{name}.thy"
    for root_, _dirs, files in os.walk(session.session_dir, followlinks=True):
        if fname in files:
            return Path(root_) / fname
    return direct  # not found — caller handles the missing file


def _theory_relpath(session: Session, name: str) -> str:
    """The theory's path relative to session_dir, sans `.thy` (e.g. `nondet/Foo`)."""
    f = _theory_file(session, name)
    try:
        return f.relative_to(session.session_dir).as_posix()[:-4]
    except ValueError:
        return name


def _realize_for_target(check_dir: Path, session_dir: Path, relpath: str) -> Path:
    """Return `check_dir/relpath.thy` as a writable REAL path, materializing any ancestor
    subdirs (mirroring session_dir's, siblings symlinked) so we never write *through* a
    directory symlink into the pristine source tree."""
    parts = relpath.split("/")
    cur_check, cur_src = check_dir, session_dir
    for d in parts[:-1]:
        cur_src = cur_src / d
        cur_check = cur_check / d
        if cur_check.is_symlink():
            cur_check.unlink()  # was a whole-dir symlink to the source subdir
        if not cur_check.exists():
            cur_check.mkdir(parents=True, exist_ok=True)
            for entry in sorted(cur_src.iterdir()):  # mirror siblings read-only
                link = cur_check / entry.name
                if not (link.exists() or link.is_symlink()):
                    try:
                        link.symlink_to(entry.resolve())
                    except OSError:
                        pass
    tgt = cur_check / f"{parts[-1]}.thy"
    if tgt.is_symlink() or tgt.exists():
        tgt.unlink()
    return tgt


def in_session_closure(target_theory: str, session: Session) -> set[str]:
    """Transitive in-session imports of `target_theory` (excluding itself).

    A bare import is in-session iff its `.thy` exists under `session_dir` — NOT iff
    it's listed in the ROOT's `theories` block. l4v lists only top-level theories and
    relies on imports to pull in the rest (e.g. `NICTATools` imports the unlisted
    `Try_Attribute`), so filtering by the listed set drops them and the synthesized
    deps session fails to load them. Genuinely cross-session qualified imports
    (`Word_Lib.Foo`) are handled via `needed_sessions` — but l4v also *self-qualifies*
    in-session imports with the session's own name (`Monads.Datatype_Schematic`); those
    are in-session and must be followed (the prefix is stripped)."""
    self_prefix = session.name + "."
    seen: set[str] = set()
    stack = [target_theory]
    while stack:
        t = stack.pop()
        for imp in imports_of(_theory_file(session, t)):
            base = imp
            if imp.startswith(self_prefix):
                base = imp[len(self_prefix) :]  # self-qualified → in-session
            elif "." in imp:
                continue  # genuine cross-session
            if base == target_theory or base in seen:
                continue
            if _theory_file(session, base).is_file():
                seen.add(base)
                stack.append(base)
    return seen


def _sanitize(s: str) -> str:
    return re.sub(r"\W+", "_", s)


def needed_sessions(session: Session, theories: set[str]) -> list[str]:
    """The subset of `dep_sessions` that `theories` actually import (qualified).

    The session ROOT may declare deps (e.g. `Finite-Map-Extras`) needed only by
    theories outside the target's closure; pulling those into the base heap drags in
    their (heavy) own closures for nothing. A cross-session import is qualified
    (`Finite-Map-Extras.Finite_Map_Extras`); its prefix names the session.
    """
    declared = set(session.dep_sessions)
    used: set[str] = set()
    for t in theories:
        for imp in imports_of(_theory_file(session, t)):
            if "." in imp:
                prefix = imp.split(".", 1)[0]
                if prefix in declared:
                    used.add(prefix)
    return [d for d in session.dep_sessions if d in used]


def _dep_dirs(session: Session, deps: list[str]) -> list[str]:
    """`-d` dirs for the needed cross-session AFP deps — each session's own dir.

    Registering `thys/` wholesale would also register the real session and clash on
    its theory names; pointing at each dependency's directory registers only it.
    """
    dirs: list[str] = []
    if session.afp_root is None:
        return dirs
    for dep in deps:
        cand = session.afp_root / dep
        if (cand / "ROOT").exists():
            dirs.append(str(cand.resolve()))
    return dirs


def _sessions_block(dep_sessions: list[str]) -> str:
    if not dep_sessions:
        return ""
    return "  sessions\n" + "".join(f'    "{d}"\n' for d in dep_sessions)


def _dirs_block(session: Session, names: Iterable[str]) -> str:
    """`directories` block for the subdirs the given theories live in.

    Isabelle refuses "implicit use of directory" — a theory whose `.thy` sits in a subdir
    must have that subdir declared (l4v's Monads keeps theories under nondet/, trace/, …).
    We then list theories by bare name and Isabelle finds them across declared dirs."""
    # Declare exactly the REAL subdirs the given theories live in (from their file
    # locations). We deliberately do NOT use the ROOT's literal `directories` (which may
    # contain `$L4V_ARCH` — clashing with the resolved `ARM` — or be empty), and the
    # check session declares only its single target's dir so it never re-declares a subdir
    # the parent AblateDeps already uses (Isabelle: "Duplicate use of directory").
    dirs: set[str] = set()
    for nm in names:
        parent = str(Path(_theory_relpath(session, nm)).parent)
        if parent and parent != ".":
            dirs.add(parent)
    if not dirs:
        return ""
    return "  directories\n" + "".join(f'    "{d}"\n' for d in sorted(dirs))


def _deps_name(session: Session, target: str) -> str:
    return f"AblateDeps_{_sanitize(session.name)}_{_sanitize(target)}"


def _check_name(session: Session, target: str) -> str:
    return f"AblateCheck_{_sanitize(session.name)}_{_sanitize(target)}"


def _deps_root_text(
    session: Session, target: str, deps: set[str], deps_sess: list[str]
) -> str:
    """ROOT for the prebuilt deps session — the target's in-session import closure.

    `skip_proofs` makes the deps' own proofs oracle-backed facts (their *statements* are
    what the target needs), so the deps build never runs their tactics. Essential
    because some AFP proofs (e.g. Solidity's `ReadShow` via `smt (verit, …)`) hang the
    bundled veriT indefinitely; skipping them lets the deps build in seconds while the
    target is still checked with real proofs. It is a per-session ROOT option, so the
    target check session (which omits it) is unaffected.
    """
    name = _deps_name(session, target)
    # Declare the subdirs the deps live in (nondet/, …) then list by bare name — theories
    # are reachable via the mirrored (symlinked) subdirs in the deps dir.
    body = "".join(f"    {t}\n" for t in sorted(deps))
    return (
        f'session "{name}" = "{session.parent}" +\n'
        f"  options [skip_proofs = true]\n"
        f"{_sessions_block(deps_sess)}{_dirs_block(session, deps)}  theories\n{body}"
    )


def _check_root_text(session: Session, target: str, deps_sess: list[str]) -> str:
    """ROOT for the single-theory check session, inheriting the prebuilt deps heap."""
    check = _check_name(session, target)
    parent = _deps_name(session, target)
    return (
        f'session "{check}" = "{parent}" +\n'
        f"{_sessions_block(deps_sess)}{_dirs_block(session, [target])}"
        f"  theories\n    {target}\n"
    )


_THY_ANTIQUOT = re.compile(r'@\{theory\s+("?)([^}"]*)\1\}')


def _strip_self_prefix(head: str, session_name: str) -> str:
    """Turn a theory's *self-qualified* imports (`Monads.Foo`, from l4v) into bare `Foo`,
    and neutralize `@{theory "…"}` doc antiquotations to plain text.

    Within a renamed synthesized session, `SessionName.Foo` import prefixes are dangling
    ancestors (bared here, then re-qualified for the target). And `@{theory "Name"}`
    antiquotations in `text` blocks are *validated* against the session's theory names —
    which no longer match (our session is `AblateDeps_…`, not `Monads`) — so we turn them
    into inert text (`Name`) rather than chase the dynamic session-qualified name."""
    # both quoted "Monads.Foo" and bare Monads.Foo
    head = re.sub(rf'"{re.escape(session_name)}\.([\w\']+)"', r'"\1"', head)
    head = re.sub(rf"(?<![\w.]){re.escape(session_name)}\.(?=[\w'])", "", head)
    head = _THY_ANTIQUOT.sub(r"\2", head)
    return head


def qualify_imports(
    content: str, deps: set[str], deps_session: str, session_name: str | None = None
) -> str:
    """Rewrite the target's bare in-session-dep imports to qualified `Deps.Foo` form.

    Bare `imports Foo` resolve only within a session, so to import a dep that now lives
    in a *separate* prebuilt session we must qualify it (`"DepsSession.Foo"`). Only the
    theory header (up to `begin`) is touched, and only names in the dep closure — Main
    and other-session imports (already qualified) are left alone. Self-qualified in-session
    imports (`SessionName.Foo`) are first stripped to bare so they get re-qualified too.
    """
    # Strip self-qualified refs everywhere: besides imports, l4v `text`/doc blocks contain
    # `@{theory "Monads.Foo"}` antiquotations that Isabelle validates as ancestors — those
    # dangle in the renamed session too, so bare them across the whole file.
    if session_name:
        content = _strip_self_prefix(content, session_name)
    m = re.search(r"\bbegin\b", content)
    end = m.start() if m else len(content)
    head, body = content[:end], content[end:]
    for nm in deps:
        head = re.sub(
            rf'(?<![\w."]){re.escape(nm)}(?![\w."])',
            f'"{deps_session}.{nm}"',
            head,
        )
    return head + body


def _materialize_self_qualified(
    base_dir: Path, session: Session, names: Iterable[str]
) -> None:
    """For any closure theory whose header self-qualifies imports (`SessionName.Foo`),
    replace its symlink in `base_dir` with a real copy whose header uses bare names, so
    the import resolves within the synthesized (renamed) deps session instead of a
    dangling `SessionName` ancestor."""
    self_prefix = session.name + "."
    for nm in names:
        src = _theory_file(session, nm)
        try:
            content = src.read_text(encoding="utf-8")
        except OSError:
            continue
        if self_prefix not in content and "@{theory" not in content:
            continue
        rel = _theory_relpath(session, nm)
        tgt = _realize_for_target(base_dir, session.session_dir, rel)
        # Strip self-qualification across the whole file (imports AND `@{theory "…"}`
        # doc antiquotations, which Isabelle validates as ancestors).
        tgt.write_text(_strip_self_prefix(content, session.name), encoding="utf-8")


class IsabelleProver:
    key = "isabelle"

    def prepare(self, repo: Path, *, timeout: int) -> CheckResult:
        # The HOL heap ships with the distribution; `-b HOL` warms/ensures it.
        return run(
            ["isabelle", "build", "-b", "HOL"],
            cwd=repo,
            timeout=timeout,
            env=_isabelle_env(repo),
        )

    def check(
        self, repo: Path, rel: Path, *, allow_holes: bool, timeout: int
    ) -> CheckResult:
        target = (repo / rel).resolve()
        content = target.read_text(encoding="utf-8")
        name = theory_name(content)
        if name is None:
            return CheckResult(
                ok=False,
                cmd=[],
                stdout="",
                stderr=f"no `theory <Name>` header in {target}",
                note="no-theory-header",
            )
        # A recovered solution must contain no incomplete proofs (we build with
        # quick_and_dirty for heap stability, so enforce sorry-freeness textually).
        if not allow_holes:
            m = _HOLE_RE.search(content)
            if m is not None:
                return CheckResult(
                    ok=False,
                    cmd=[],
                    stdout="",
                    stderr=f"incomplete-proof marker `{m.group(0)}` in solution",
                    note="hole-in-solution",
                )

        env = _isabelle_env(repo)
        session = discover_session(target)
        if session is not None:
            return self._check_session(session, name, env, timeout)
        return self._check_throwaway(target, name, repo, env, timeout)

    def _check_session(
        self, session: Session, name: str, env: dict[str, str], timeout: int
    ) -> CheckResult:
        # The target's in-session closure is built ONCE as a separate `AblateDeps`
        # session (cached heap); each check then re-elaborates only the single target
        # theory against it. The target's bare in-session imports are rewritten to
        # qualified `AblateDeps_….Foo` so they resolve to that prebuilt heap (bare
        # cross-session imports do not). A directory belongs to one session, so deps and
        # target get separate copy dirs.
        deps = in_session_closure(name, session)
        dep_sessions = needed_sessions(session, deps | {name})
        root = Path(env["ISABELLE_HOME_USER"]) / "ablate-sessions"
        check = _check_name(session, name)
        check_dir = root / check
        _fresh_dir(check_dir)
        cmd = ["isabelle", "build", "-o", "quick_and_dirty=true"]
        cmd += _extra_dirs()  # repo-root dirs (e.g. l4v) so scattered sessions resolve
        for d in _dep_dirs(session, dep_sessions):
            cmd += ["-d", d]
        if deps:
            deps_name = _deps_name(session, name)
            deps_dir = root / deps_name
            _fresh_dir(deps_dir)
            # Symlink every session-dir entry (all .thy + ML files + subdirs) into the
            # deps dir, except ROOT/ROOTS (we write our own). l4v theories load auxiliary
            # resources by relative path (`ML_file "crunch-cmd.ML"`), so copying just the
            # .thy isn't enough — mirror the whole dir read-only.
            _link_session_files(session.session_dir, deps_dir)
            # Rewrite self-qualified imports (`Monads.Foo`) in the deps to bare names so
            # they resolve within the renamed AblateDeps session, not a dangling ancestor.
            _materialize_self_qualified(deps_dir, session, deps)
            (deps_dir / "ROOT").write_text(
                _deps_root_text(session, name, deps, dep_sessions), encoding="utf-8"
            )
            # The target's resources too (in case it ML_files); then overwrite its .thy
            # with the import-qualified version so its in-session deps resolve to AblateDeps.
            _link_session_files(session.session_dir, check_dir)
            content = _theory_file(session, name).read_text(encoding="utf-8")
            content = qualify_imports(content, deps, deps_name, session.name)
            # Write the qualified target at its real relative path (materializing any
            # subdir) so it's a real file — never written through a symlink to source.
            tgt = _realize_for_target(
                check_dir, session.session_dir, _theory_relpath(session, name)
            )
            tgt.write_text(content, encoding="utf-8")
            (check_dir / "ROOT").write_text(
                _check_root_text(session, name, dep_sessions), encoding="utf-8"
            )
            cmd += ["-d", str(deps_dir)]
        else:
            # No in-session deps — a one-session check inheriting the session parent.
            rel = _theory_relpath(session, name)
            tgt = _realize_for_target(check_dir, session.session_dir, rel)
            shutil.copyfile(_theory_file(session, name), tgt)
            (check_dir / "ROOT").write_text(
                f'session "{check}" = "{session.parent}" +\n'
                f"{_sessions_block(dep_sessions)}{_dirs_block(session, [name])}"
                f"  theories\n    {name}\n",
                encoding="utf-8",
            )
        cmd += ["-d", str(check_dir), check]
        return run(cmd, cwd=session.session_dir, timeout=timeout, env=env)

    def _check_throwaway(
        self, target: Path, name: str, repo: Path, env: dict[str, str], timeout: int
    ) -> CheckResult:
        tmp = Path(tempfile.mkdtemp(prefix=f"ablate-check-{name}-"))
        try:
            root = (
                f'session "AblateCheck" = "HOL" +\n'
                f'  directories "{target.parent.resolve()}"\n'
                f"  theories\n"
                f'    "{name}"\n'
            )
            (tmp / "ROOT").write_text(root, encoding="utf-8")
            return run(
                [
                    "isabelle",
                    "build",
                    "-o",
                    "quick_and_dirty=true",
                    *_extra_dirs(),
                    "-d",
                    str(tmp),
                    "AblateCheck",
                ],
                cwd=repo,
                timeout=timeout,
                env=env,
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
