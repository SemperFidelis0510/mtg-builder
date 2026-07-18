"""Tests for the card database update orchestrator and clean rebuild flag propagation."""

from __future__ import annotations

from typing import Any

import pytest


def test_do_update_calls_download_prices_and_build_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """do_update must call download(force=True), then prices, then clean rebuild, in that order."""
    from src.lib import update_card_database

    calls: list[tuple[str, Any]] = []

    def fake_download(force: bool) -> None:
        calls.append(("download", force))

    def fake_prices() -> dict[str, float]:
        calls.append(("prices", None))
        return {}

    def fake_build(clean: bool = False) -> None:
        calls.append(("build", clean))

    monkeypatch.setattr(update_card_database, "do_download", fake_download)
    monkeypatch.setattr(update_card_database, "update_all_prices", fake_prices)
    monkeypatch.setattr(update_card_database, "do_build_all", fake_build)

    update_card_database.do_update()

    assert calls == [("download", True), ("prices", None), ("build", True)]


def test_do_update_propagates_download_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure in the download step must propagate; subsequent steps are not run."""
    from src.lib import update_card_database

    subsequent_calls: list[str] = []

    def raise_download(force: bool) -> None:
        raise RuntimeError("simulated download failure")

    def fake_prices() -> dict[str, float]:
        subsequent_calls.append("prices")
        return {}

    def fake_build(clean: bool = False) -> None:
        subsequent_calls.append("build")

    monkeypatch.setattr(update_card_database, "do_download", raise_download)
    monkeypatch.setattr(update_card_database, "update_all_prices", fake_prices)
    monkeypatch.setattr(update_card_database, "do_build_all", fake_build)

    with pytest.raises(RuntimeError, match="simulated download failure"):
        update_card_database.do_update()
    assert subsequent_calls == []


def test_do_update_propagates_prices_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure in the prices step must propagate; build is not run."""
    from src.lib import update_card_database

    subsequent_calls: list[str] = []

    def fake_download(force: bool) -> None:
        pass

    def raise_prices() -> dict[str, float]:
        raise RuntimeError("simulated prices failure")

    def fake_build(clean: bool = False) -> None:
        subsequent_calls.append("build")

    monkeypatch.setattr(update_card_database, "do_download", fake_download)
    monkeypatch.setattr(update_card_database, "update_all_prices", raise_prices)
    monkeypatch.setattr(update_card_database, "do_build_all", fake_build)

    with pytest.raises(RuntimeError, match="simulated prices failure"):
        update_card_database.do_update()
    assert subsequent_calls == []


def test_do_update_propagates_build_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure in the build step must propagate."""
    from src.lib import update_card_database

    def fake_download(force: bool) -> None:
        pass

    def fake_prices() -> dict[str, float]:
        return {}

    def raise_build(clean: bool = False) -> None:
        raise RuntimeError("simulated build failure")

    monkeypatch.setattr(update_card_database, "do_download", fake_download)
    monkeypatch.setattr(update_card_database, "update_all_prices", fake_prices)
    monkeypatch.setattr(update_card_database, "do_build_all", raise_build)

    with pytest.raises(RuntimeError, match="simulated build failure"):
        update_card_database.do_update()


def test_do_build_all_creates_graph_and_lancedb_then_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public rebuild pipeline executes graph, vector, and community stages."""
    from src.lib import build_rag

    calls: list[str] = []
    result = type("Result", (), {"card_count": 1, "combo_count": 1})()
    monkeypatch.setattr(build_rag, "_load_cards", lambda: ["card"])
    monkeypatch.setattr(
        build_rag,
        "build_graph_artifacts",
        lambda cards, output, **kwargs: calls.append("graph") or result,
    )
    monkeypatch.setattr(build_rag, "_write_settings", lambda workflows: calls.append("settings"))
    monkeypatch.setattr(build_rag, "_build_lancedb", lambda graph_result: calls.append("lancedb"))
    monkeypatch.setattr(
        build_rag,
        "_build_community_reports",
        lambda graph_result: calls.append("reports"),
    )
    monkeypatch.setattr(build_rag, "_run_graphrag_workflows", lambda: calls.append("workflows"))
    monkeypatch.setattr(
        build_rag,
        "_run_runtime_embeddings",
        lambda graph_result: calls.append("embeddings"),
    )
    monkeypatch.setattr(build_rag, "_validate_generated_index", lambda graph_result: calls.append("validate"))
    monkeypatch.setattr(build_rag, "_finalize_manifest", lambda graph_result: calls.append("manifest"))

    build_rag.do_build_all()

    assert calls == [
        "graph",
        "settings",
        "workflows",
        "reports",
        "settings",
        "embeddings",
        "lancedb",
        "settings",
        "validate",
        "manifest",
    ]
