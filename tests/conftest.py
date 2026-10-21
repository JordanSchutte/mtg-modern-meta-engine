from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def melee_fixture():
    def load(name: str) -> str:
        return (FIXTURES / "melee" / name).read_text(encoding="utf-8")

    return load
