"""Tests for experiments.orchestrate.detect_coq_version.

These exercise ``detect_from_contents`` -- the pure core of the detector --
with short inline workflow / travis fixtures so we can skip subprocess
mocking and PyYAML. Real-SHA validation happens in CI / by hand.
"""

from __future__ import annotations

import pytest

from orchestrate.detect_coq_version import (
    WORKFLOW_DEBIAN,
    WORKFLOW_DOCKER,
    WORKFLOW_OPAM,
    TRAVIS,
    detect_from_contents,
)


DOCKER_WITH_EXPLICIT_VERSION = """\
name: CI (Coq, docker)
jobs:
  build:
    strategy:
      matrix:
        include:
        - env: { COQ_VERSION: "8.18", DOCKER_COQ_VERSION: "8.18.0", DOCKER_OCAML_VERSION: "default" }
          os: 'ubuntu-latest'
"""


DOCKER_ONLY_DEV = """\
name: CI (Coq, docker, dev)
jobs:
  build:
    strategy:
      matrix:
        include:
        - env: { COQ_VERSION: "master", DOCKER_COQ_VERSION: "dev", DOCKER_OCAML_VERSION: "default" }
          os: 'ubuntu-latest'
  validate:
    strategy:
      matrix:
        include:
        - env: { COQ_VERSION: "master", DOCKER_COQ_VERSION: "dev", DOCKER_OCAML_VERSION: "default" }
          os: 'ubuntu-latest'
"""


DOCKER_DEV_THEN_STABLE = """\
jobs:
  build:
    strategy:
      matrix:
        include:
        - env: { DOCKER_COQ_VERSION: "dev" }
        - env: { DOCKER_COQ_VERSION: "8.20.0" }
"""


OPAM_INLINE = """\
jobs:
  install:
    strategy:
      matrix:
        coq-version: ['dev', '8.20.0']
        os: [{name: 'Ubuntu'}]
"""


OPAM_BLOCK = """\
jobs:
  install:
    strategy:
      matrix:
        coq-version:
          - 'dev'
          - '8.19.1'
"""


DEBIAN_MIN = """\
name: CI (Coq, Debian)
jobs:
  build:
    runs-on: 'ubuntu-22.04'
"""


TRAVIS_FIRST_NONMASTER_EXPLICIT = """\
jobs:
  include:
    - stage: early
      env: COQ_VERSION="master" COQ_PACKAGE="coq"       PPA="ppa:jgross-h/coq-master-daily"
    - stage: selected
      env: COQ_VERSION="8.8.2"  COQ_PACKAGE="coq-8.8.2" PPA="ppa:jgross-h/many-coq-versions"
    - stage: selected
      env: COQ_VERSION="8.7.2"  COQ_PACKAGE="coq-8.7.2" PPA="ppa:jgross-h/many-coq-versions"
"""


TRAVIS_FIRST_NONMASTER_HINT_ONLY = """\
jobs:
  include:
    - stage: early
      env: COQ_VERSION="master" COQ_PACKAGE="coq"   PPA="ppa:jgross-h/coq-master-daily"
    - stage: early
      env: COQ_VERSION="v8.9"   COQ_PACKAGE="coq"   PPA="ppa:jgross-h/coq-8.9-daily"
    - stage: selected
      env: COQ_VERSION="8.8.2"  COQ_PACKAGE="coq-8.8.2" PPA="ppa:jgross-h/many-coq-versions"
"""


# ----------------------------------------------------------------------
# docker workflow (precedence step 1)
# ----------------------------------------------------------------------


def test_docker_matrix_explicit_version_returns_it() -> None:
    files = {WORKFLOW_DOCKER: DOCKER_WITH_EXPLICIT_VERSION}
    assert detect_from_contents(files) == "8.18.0"


def test_docker_matrix_only_dev_returns_dev() -> None:
    files = {WORKFLOW_DOCKER: DOCKER_ONLY_DEV}
    assert detect_from_contents(files) == "dev"


def test_docker_matrix_prefers_first_non_dev() -> None:
    files = {WORKFLOW_DOCKER: DOCKER_DEV_THEN_STABLE}
    assert detect_from_contents(files) == "8.20.0"


# ----------------------------------------------------------------------
# opam-package workflow (precedence step 2)
# ----------------------------------------------------------------------


def test_opam_inline_matrix_picks_first_non_dev() -> None:
    files = {WORKFLOW_OPAM: OPAM_INLINE}
    assert detect_from_contents(files) == "8.20.0"


def test_opam_block_style_matrix_picks_first_non_dev() -> None:
    files = {WORKFLOW_OPAM: OPAM_BLOCK}
    assert detect_from_contents(files) == "8.19.1"


# ----------------------------------------------------------------------
# debian workflow (precedence step 3)
# ----------------------------------------------------------------------


def test_debian_workflow_returns_dev_fallback() -> None:
    files = {WORKFLOW_DEBIAN: DEBIAN_MIN}
    assert detect_from_contents(files) == "dev"


# ----------------------------------------------------------------------
# travis.yml (precedence step 4)
# ----------------------------------------------------------------------


def test_travis_first_nonmaster_explicit_package() -> None:
    files = {TRAVIS: TRAVIS_FIRST_NONMASTER_EXPLICIT}
    assert detect_from_contents(files) == "8.8.2"


def test_travis_first_nonmaster_hint_only() -> None:
    files = {TRAVIS: TRAVIS_FIRST_NONMASTER_HINT_ONLY}
    # First non-master entry is COQ_PACKAGE="coq" + COQ_VERSION="v8.9";
    # detector returns the X.Y hint.
    assert detect_from_contents(files) == "8.9"


# ----------------------------------------------------------------------
# precedence ordering
# ----------------------------------------------------------------------


def test_docker_takes_precedence_over_travis() -> None:
    files = {
        WORKFLOW_DOCKER: DOCKER_WITH_EXPLICIT_VERSION,
        TRAVIS: TRAVIS_FIRST_NONMASTER_EXPLICIT,
    }
    assert detect_from_contents(files) == "8.18.0"


def test_opam_takes_precedence_over_debian() -> None:
    files = {
        WORKFLOW_OPAM: OPAM_INLINE,
        WORKFLOW_DEBIAN: DEBIAN_MIN,
    }
    assert detect_from_contents(files) == "8.20.0"


def test_only_travis_available_skips_missing_workflows() -> None:
    # Scenario (c) from the task: no .github/workflows/ files, only .travis.yml.
    files = {TRAVIS: TRAVIS_FIRST_NONMASTER_EXPLICIT}
    assert detect_from_contents(files) == "8.8.2"


def test_no_files_returns_none() -> None:
    assert detect_from_contents({}) is None


@pytest.fixture
def fiat_crypto_old_sha_files() -> dict[str, str]:
    """Fixture representing a pre-.github/workflows/ fiat-crypto SHA."""
    return {TRAVIS: TRAVIS_FIRST_NONMASTER_EXPLICIT}


def test_fixture_old_sha_returns_travis_version(
    fiat_crypto_old_sha_files: dict[str, str],
) -> None:
    assert detect_from_contents(fiat_crypto_old_sha_files) == "8.8.2"
