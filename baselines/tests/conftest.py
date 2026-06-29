from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
ASSISTANTS = ("coq", "isabelle", "lean")


@pytest.fixture(params=ASSISTANTS)
def fixture_jsonl(request: pytest.FixtureRequest) -> Path:
    """Path to each ablator's real sample JSONL (one record)."""
    return FIXTURES / f"{request.param}.jsonl"
