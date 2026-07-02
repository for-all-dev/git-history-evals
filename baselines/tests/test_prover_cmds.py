"""Tests for prover backend selection + command/ROOT construction (no execution)."""

from pathlib import Path

import pytest

from apply_ablate.provers import get_prover, run
from apply_ablate.provers.coq import CoqProver, coq_flags
from apply_ablate.provers.isabelle import (
    IsabelleProver,
    _check_root_text,
    _deps_root_text,
    _extra_dirs,
    discover_session,
    imports_of,
    in_session_closure,
    needed_sessions,
    parse_root,
    qualify_imports,
    theory_name,
)
from apply_ablate.provers.lean import LeanProver, _lake_root


def test_registry_selects_backend():
    assert isinstance(get_prover("coq"), CoqProver)
    assert isinstance(get_prover("Isabelle"), IsabelleProver)
    assert isinstance(get_prover(" lean "), LeanProver)


def test_registry_unknown():
    with pytest.raises(KeyError):
        get_prover("agda")


def test_coq_flags_parses_coqproject(tmp_path: Path):
    cp = tmp_path / "_CoqProject"
    cp.write_text("-R . Top\n-Q theories Foo\n-I src\n# comment\nFile.v\n")
    assert coq_flags(cp) == ["-R", ".", "Top", "-Q", "theories", "Foo", "-I", "src"]


def test_isabelle_extra_dirs(tmp_path: Path, monkeypatch):
    # unset → no extra -d flags
    monkeypatch.delenv("ABLATE_ISABELLE_DIRS", raising=False)
    assert _extra_dirs() == []
    # existing dirs become `-d <dir>`; missing ones are skipped
    d1 = tmp_path / "l4v"
    d1.mkdir()
    monkeypatch.setenv("ABLATE_ISABELLE_DIRS", f"{d1}:{tmp_path / 'nope'}")
    assert _extra_dirs() == ["-d", str(d1)]


def test_isabelle_theory_name():
    assert theory_name("theory Sample\nimports Main\nbegin\nend\n") == "Sample"
    assert theory_name("no header here") is None


def test_isabelle_theory_name_skips_comments():
    # l4v's Bisim_UL has `(* A theory of … *)` above the real header; the naive
    # parser used to return "of". Comments must be stripped first.
    src = (
        "(* Copyright *)\n\n(* A theory of guarded monadic bisimulation. *)\n\n"
        "theory Bisim_UL\nimports Main\nbegin\nend\n"
    )
    assert theory_name(src) == "Bisim_UL"


def test_isabelle_imports_of_skips_comments(tmp_path: Path):
    (tmp_path / "T.thy").write_text(
        "(* mentions imports Bogus in a comment *)\n"
        "theory T\nimports Main Foo\nbegin\nend\n",
        encoding="utf-8",
    )
    assert imports_of(tmp_path / "T.thy") == ["Main", "Foo"]


def _write_session(root_dir: Path) -> None:
    """A small AFP-style session: two theories + one needing a cross-session dep."""
    (root_dir / "ROOT").write_text(
        "chapter AFP\n\n"
        'session "Demo" = "HOL-Library" +\n'
        "  options [timeout = 300]\n"
        "  sessions\n"
        '    "HOL-Eisbach"\n'
        '    "Finite-Map-Extras"\n'
        "  theories\n"
        "    Base\n"
        "    Mid\n"
        "    Top\n"
        "  theories [condition = ISABELLE_GHC]\n"
        "    Generated\n"
        '  document_files\n    "root.tex"\n'
    )
    (root_dir / "Base.thy").write_text("theory Base\n  imports Main\nbegin\nend\n")
    (root_dir / "Mid.thy").write_text(
        'theory Mid\n  imports Base "HOL-Eisbach.Eisbach"\nbegin\nend\n'
    )
    (root_dir / "Top.thy").write_text("theory Top\n  imports Mid\nbegin\nend\n")
    (root_dir / "Generated.thy").write_text(
        "theory Generated\n  imports Top\nbegin\nend\n"
    )


def test_isabelle_parse_root(tmp_path: Path):
    _write_session(tmp_path)
    s = parse_root(tmp_path / "ROOT")
    assert s is not None
    assert s.name == "Demo"
    assert s.parent == "HOL-Library"
    assert s.dep_sessions == ["HOL-Eisbach", "Finite-Map-Extras"]
    # unconditional theories only — the ISABELLE_GHC-conditional one is skipped
    assert s.theories == ["Base", "Mid", "Top"]


def test_isabelle_imports_of(tmp_path: Path):
    _write_session(tmp_path)
    assert imports_of(tmp_path / "Mid.thy") == ["Base", "HOL-Eisbach.Eisbach"]
    assert imports_of(tmp_path / "Base.thy") == ["Main"]


def test_isabelle_in_session_closure(tmp_path: Path):
    _write_session(tmp_path)
    s = parse_root(tmp_path / "ROOT")
    assert s is not None
    assert in_session_closure("Top", s) == {"Mid", "Base"}
    assert in_session_closure("Base", s) == set()


def test_isabelle_needed_sessions_prunes_unused(tmp_path: Path):
    _write_session(tmp_path)
    s = parse_root(tmp_path / "ROOT")
    assert s is not None
    # Top's closure imports HOL-Eisbach (via Mid) but not Finite-Map-Extras.
    assert needed_sessions(s, {"Top", "Mid", "Base"}) == ["HOL-Eisbach"]


def test_isabelle_discover_session(tmp_path: Path):
    _write_session(tmp_path)
    s = discover_session(tmp_path / "Top.thy")
    assert s is not None and s.name == "Demo"
    # A theory in the session's directory but NOT listed in ROOT still belongs to the
    # session (l4v ROOTs list only top-level theories; the rest are pulled in by imports).
    stray = tmp_path / "Stray.thy"
    stray.write_text("theory Stray\n  imports Main\nbegin\nend\n")
    s2 = discover_session(stray)
    assert s2 is not None and s2.name == "Demo"
    # A theory outside any session directory → no session (throwaway-HOL fallback).
    outside = tmp_path.parent / "Outside.thy"
    outside.write_text("theory Outside\n  imports Main\nbegin\nend\n")
    assert discover_session(outside) is None


def test_isabelle_deps_and_check_roots(tmp_path: Path):
    _write_session(tmp_path)
    s = parse_root(tmp_path / "ROOT")
    assert s is not None
    # deps session = the closure, inheriting the real session parent
    deps = _deps_root_text(s, "Top", {"Mid", "Base"}, ["HOL-Eisbach"])
    assert 'session "AblateDeps_Demo_Top" = "HOL-Library" +' in deps
    assert '"HOL-Eisbach"' in deps and '"Finite-Map-Extras"' not in deps  # pruned
    for thy in ("Base", "Mid"):
        assert f"    {thy}\n" in deps  # listed by bare name
    assert "    Top\n" not in deps  # the target is not in its own deps
    # check session inherits the deps session and lists only the target
    chk = _check_root_text(s, "Top", ["HOL-Eisbach"])
    assert 'session "AblateCheck_Demo_Top" = "AblateDeps_Demo_Top" +' in chk
    assert "    Top\n" in chk


def test_isabelle_qualify_imports():
    content = (
        'theory Top\n  imports Mid "HOL-Eisbach.Eisbach" Main\nbegin\n'
        "lemma foo: Mid.bar by (rule Mid)\nend\n"
    )
    out = qualify_imports(content, {"Mid", "Base"}, "AblateDeps_Demo_Top")
    # the in-closure bare import is qualified; external/Main imports untouched
    assert 'imports "AblateDeps_Demo_Top.Mid" "HOL-Eisbach.Eisbach" Main' in out
    # the body (after `begin`) is left verbatim — only the header is rewritten
    assert "lemma foo: Mid.bar by (rule Mid)" in out


def test_lean_root_discovery(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text("name = 'x'\n")
    (tmp_path / "sub").mkdir()
    f = tmp_path / "sub" / "A.lean"
    f.write_text("theorem t : True := trivial\n")
    assert _lake_root(f) == tmp_path


def test_run_missing_tool_is_graceful(tmp_path: Path):
    res = run(["definitely-not-a-real-command-xyz"], cwd=tmp_path, timeout=5)
    assert not res.ok
    assert res.note == "missing-tool"
