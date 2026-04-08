from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def repo_root() -> str:
    # Keep this simple and string-based so tests don't depend on internal path helpers.
    return str(pytest.Config.fromdictargs({}, []).rootpath)


@pytest.fixture(scope="session")
def atomic_cards_path() -> str:
    # Import only the config constant (public-ish path definition).
    from src.lib.config import ATOMIC_CARDS_PATH

    return str(ATOMIC_CARDS_PATH)


@pytest.fixture(scope="session")
def has_atomic_cards() -> bool:
    from src.lib.config import ATOMIC_CARDS_PATH

    return ATOMIC_CARDS_PATH.is_file()

