from __future__ import annotations

import json
import contextlib
import socket
import time

import pytest
from fastapi.testclient import TestClient

from tests._sse import parse_sse_event


@pytest.fixture()
def client() -> TestClient:
    # Import the module under test (public FastAPI app).
    from src.deck_editor.app import app

    return TestClient(app)


@pytest.mark.integration
def test_root_serves_editor_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "MTG Deck Editor" in r.text


@pytest.mark.integration
def test_get_deck_returns_shape(client: TestClient) -> None:
    r = client.get("/api/deck")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, dict)
    assert "deck" in payload
    assert "stats" in payload
    assert isinstance(payload["deck"], dict)
    assert isinstance(payload["stats"], dict)


@pytest.mark.integration
def test_deck_meta_round_trip_empty(client: TestClient) -> None:
    r0 = client.get("/api/deck/meta")
    assert r0.status_code == 200
    meta0 = r0.json()
    assert meta0["name"] == ""
    assert meta0["commander"] == ""

    r1 = client.put("/api/deck", json={"name": "Test", "format": "modern"})
    assert r1.status_code == 200

    r2 = client.get("/api/deck/meta")
    assert r2.status_code == 200
    meta2 = r2.json()
    assert meta2["name"] == "Test"
    assert meta2["format"] == "modern"


@pytest.mark.integration
def test_historic_brawl_accepts_cards_with_mtgjson_brawl_legality(client: TestClient) -> None:
    load_response = client.post("/api/deck", json={"name": "Historic Brawl", "format": "historicbrawl"})
    assert load_response.status_code == 200

    add_response = client.post(
        "/api/add_card",
        json={
            "names": ["Awaken the Woods", "Craterhoof Behemoth", "Azusa, Lost but Seeking"],
            "board": "main",
        },
    )
    assert add_response.status_code == 200
    deck = add_response.json()["deck"]
    assert "Awaken the Woods" in deck["sorcery"]
    assert "Craterhoof Behemoth" in deck["creature"]
    assert "Azusa, Lost but Seeking" in deck["creature"]


@pytest.mark.integration
def test_historic_brawl_search_uses_mtgjson_brawl_legality(client: TestClient) -> None:
    response = client.post(
        "/api/search",
        json={"name": "Awaken the Woods", "format_legal": "historicbrawl"},
    )
    assert response.status_code == 200
    assert any(card["name"] == "Awaken the Woods" for card in response.json()["results"])


@pytest.mark.integration
def test_sse_emits_initial_and_update_events_real_server() -> None:
    """
    SSE behaves like an infinite stream; in-process ASGI transports tend to buffer forever.
    This test runs a real uvicorn server on localhost and streams SSE over HTTP.
    """
    import subprocess

    import requests

    def _free_port() -> int:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    port = _free_port()

    # Use a subprocess on Windows (spawn mode) to avoid pickling issues.
    import os

    env = dict(os.environ)
    env["MTG_DISABLE_RAG_STARTUP"] = "1"
    env["MTG_DISABLE_PRICE_STARTUP"] = "1"

    proc = subprocess.Popen(
        [
            "python",
            "-m",
            "uvicorn",
            "src.deck_editor.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        base = f"http://127.0.0.1:{port}"

        # Wait for server to accept connections.
        deadline = time.time() + 10
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                r = requests.get(base + "/api/deck", timeout=1)
                if r.status_code == 200:
                    break
            except Exception as e:
                last_err = e
                time.sleep(0.2)
        else:
            raise TimeoutError(f"uvicorn did not become ready; last_err={last_err!r}")

        with requests.get(base + "/api/events", stream=True, timeout=(3, 30)) as sse:
            assert sse.status_code == 200

            import queue
            import threading

            line_q: queue.Queue[str] = queue.Queue()
            err_q: queue.Queue[Exception] = queue.Queue()

            def _reader() -> None:
                try:
                    for raw in sse.iter_lines(decode_unicode=True):
                        if raw is None:
                            continue
                        line_q.put(str(raw))
                except Exception as exc:
                    err_q.put(exc)

            t = threading.Thread(target=_reader, daemon=True)
            t.start()

            def _read_one_event(timeout_s: float = 6.0):
                buf_lines: list[str] = []
                deadline_evt = time.time() + timeout_s
                while time.time() < deadline_evt:
                    if not err_q.empty():
                        exc = err_q.get_nowait()
                        raise TimeoutError(f"SSE reader failed: {exc}") from exc
                    try:
                        line = line_q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    buf_lines.append(line)
                    if line == "":
                        chunk = "\n".join(buf_lines).strip("\n") + "\n\n"
                        return parse_sse_event(chunk)

                status = proc.poll()
                out = ""
                if proc.stdout is not None:
                    try:
                        out = proc.stdout.read() or ""
                    except Exception:
                        out = ""
                raise TimeoutError(
                    f"Timed out waiting for SSE event (proc_status={status}) output_tail={out[-400:]!r}"
                )

            first = _read_one_event()
            assert first.event == "deck_updated"
            init_payload = first.data_json()
            assert isinstance(init_payload, dict)
            assert "deck" in init_payload

            mut = requests.put(base + "/api/deck", json={"name": "SSE Test", "format": "legacy"}, timeout=5)
            assert mut.status_code == 200

            updated = _read_one_event()
            assert updated.event == "deck_updated"
            updated_payload = updated.data_json()
            assert updated_payload["deck"]["name"] == "SSE Test"
            assert updated_payload["deck"]["format"] == "legacy"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


@pytest.mark.integration
def test_export_formats_and_export_json(client: TestClient) -> None:
    r0 = client.get("/api/export/formats")
    assert r0.status_code == 200
    formats = r0.json()
    assert "formats" in formats
    assert "json" in formats["formats"]

    r1 = client.get("/api/export", params={"format": "json"})
    assert r1.status_code == 200
    out = r1.json()
    assert "text" in out
    # Must be valid JSON text
    parsed = json.loads(out["text"])
    assert isinstance(parsed, dict)


@pytest.mark.integration
def test_export_cardkingdom_buylist_is_versionless_and_main_deck_only(client: TestClient) -> None:
    update = client.put(
        "/api/deck",
        json={
            "artifact": ["Sol Ring", "Sol Ring"],
            "sideboard": ["Sol Ring"],
        },
    )
    assert update.status_code == 200

    formats = client.get("/api/export/formats")
    assert formats.status_code == 200
    assert formats.json()["formats"]["cardkingdom"] == "Card Kingdom Buylist CSV"

    response = client.get("/api/export", params={"format": "cardkingdom"})

    assert response.status_code == 200
    assert response.json()["text"] == "Title,Edition,Foil,Quantity\nSol Ring,,,2\n"


@pytest.mark.integration
def test_import_rejects_invalid_body(client: TestClient) -> None:
    r = client.post("/api/import", json={"format": "arena"})
    assert r.status_code == 400


@pytest.mark.integration
def test_import_json_round_trip_empty_deck(client: TestClient) -> None:
    r0 = client.get("/api/export", params={"format": "json"})
    assert r0.status_code == 200
    exported = r0.json()["text"]

    r1 = client.post("/api/import", json={"text": exported, "format": "json"})
    assert r1.status_code == 200


@pytest.mark.integration
def test_search_semantic_requires_rag(client: TestClient) -> None:
    # Even if AtomicCards exists, semantic_query must 503 until RAG is ready.
    ready = client.get("/api/rag_ready").json().get("ready")
    assert isinstance(ready, bool)
    if ready:
        pytest.skip("RAG already ready in this environment; semantic_query test is not meaningful")
    r = client.post("/api/search", json={"name": "sol", "semantic_query": "mana rock", "search_type": "general"})
    assert r.status_code == 503


def _redirect_issue_dirs(monkeypatch, tmp_path) -> None:
    """Point the issue/bug/feature directories and log source under tmp_path for tests."""
    import src.deck_editor.app as app_mod

    # The endpoint computes a repo-relative path using REPO_ROOT, so keep all dirs under it.
    monkeypatch.setattr(app_mod, "REPO_ROOT", tmp_path, raising=True)
    monkeypatch.setattr(app_mod, "_BUG_REPORT_DIR", tmp_path / ".ai" / "BR", raising=True)
    monkeypatch.setattr(app_mod, "_FEATURE_REQUEST_DIR", tmp_path / ".ai" / "FR", raising=True)


@pytest.mark.integration
def test_submit_issue_bug_attaches_log(client: TestClient, tmp_path, monkeypatch) -> None:
    _redirect_issue_dirs(monkeypatch, tmp_path)
    # A log file under logs/deck_editor should be snapshotted into the bug report folder.
    logs_dir = tmp_path / "logs" / "deck_editor"
    logs_dir.mkdir(parents=True)
    (logs_dir / "mtg_test.log").write_text("log line", encoding="utf-8")

    r = client.post("/api/submit_issue", json={"description": "test bug", "issue_type": "bug"})
    assert r.status_code == 200
    out = r.json()
    assert out["issue_type"] == "bug"
    assert out["logs_attached"] == ["mtg_test.log"]
    assert (tmp_path / ".ai" / "BR").is_dir()
    assert (tmp_path / out["path"] / "bug_report.md").is_file()
    assert (tmp_path / out["path"] / "mtg_test.log").is_file()


@pytest.mark.integration
def test_submit_issue_feature_has_no_log(client: TestClient, tmp_path, monkeypatch) -> None:
    _redirect_issue_dirs(monkeypatch, tmp_path)
    # Even if a log exists, feature requests must not copy it.
    logs_dir = tmp_path / "logs" / "deck_editor"
    logs_dir.mkdir(parents=True)
    (logs_dir / "mtg_test.log").write_text("log line", encoding="utf-8")

    r = client.post("/api/submit_issue", json={"description": "please add X", "issue_type": "feature"})
    assert r.status_code == 200
    out = r.json()
    assert out["issue_type"] == "feature"
    assert out["logs_attached"] == []
    assert (tmp_path / ".ai" / "FR").is_dir()
    assert (tmp_path / out["path"] / "feature_request.md").is_file()
    assert not list((tmp_path / out["path"]).glob("*.log"))


@pytest.mark.integration
def test_submit_issue_rejects_invalid_or_missing_type(client: TestClient, tmp_path, monkeypatch) -> None:
    _redirect_issue_dirs(monkeypatch, tmp_path)
    assert client.post("/api/submit_issue", json={"description": "x", "issue_type": "nonsense"}).status_code == 400
    assert client.post("/api/submit_issue", json={"description": "x"}).status_code == 400


@pytest.mark.integration
def test_deck_sort_orders_every_board_and_unpriced_cards_last(client: TestClient) -> None:
    deck = {
        "cards": [
            {"name": "Main Unknown", "canonical_name": "Main Unknown", "type_line": "Artifact", "price_usd": -1},
            {"name": "Main Expensive", "canonical_name": "Main Expensive", "type_line": "Artifact", "price_usd": 10.0},
            {"name": "Main Cheap", "canonical_name": "Main Cheap", "type_line": "Artifact", "price_usd": 1.0},
        ],
        "maybe": [
            {"name": "Maybe Unknown", "canonical_name": "Maybe Unknown", "type_line": "Artifact", "price_usd": -1},
            {"name": "Maybe Priced", "canonical_name": "Maybe Priced", "type_line": "Artifact", "price_usd": 2.0},
        ],
        "sideboard": [
            {"name": "Side Unknown", "canonical_name": "Side Unknown", "type_line": "Artifact", "price_usd": -1},
            {"name": "Side Priced", "canonical_name": "Side Priced", "type_line": "Artifact", "price_usd": 3.0},
        ],
    }
    assert client.post("/api/deck", json=deck).status_code == 200

    response = client.post("/api/deck/sort", json={"criterion": "price", "direction": "descending"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sort"] == {"criterion": "price", "direction": "descending"}
    assert payload["deck"]["artifact"] == ["Main Expensive", "Main Cheap", "Main Unknown"]
    assert payload["deck"]["maybe_names"] == ["Maybe Priced", "Maybe Unknown"]
    assert payload["deck"]["sideboard_names"] == ["Side Priced", "Side Unknown"]


@pytest.mark.integration
def test_deck_sort_mana_value_uses_color_identity_tie_breakers(client: TestClient) -> None:
    deck = {
        "cards": [
            {"name": "Multicolor", "canonical_name": "Multicolor", "type_line": "Artifact", "mana_value": 2, "color_identity": ["W", "U"]},
            {"name": "Green", "canonical_name": "Green", "type_line": "Artifact", "mana_value": 2, "color_identity": ["G"]},
            {"name": "White", "canonical_name": "White", "type_line": "Artifact", "mana_value": 2, "color_identity": ["W"]},
            {"name": "Colorless", "canonical_name": "Colorless", "type_line": "Artifact", "mana_value": 2, "color_identity": []},
            {"name": "Higher Value", "canonical_name": "Higher Value", "type_line": "Artifact", "mana_value": 3, "color_identity": []},
        ],
    }
    assert client.post("/api/deck", json=deck).status_code == 200

    response = client.post("/api/deck/sort", json={"criterion": "mana_value", "direction": "descending"})

    assert response.status_code == 200
    assert response.json()["deck"]["artifact"] == [
        "Higher Value",
        "Colorless",
        "White",
        "Green",
        "Multicolor",
    ]


@pytest.mark.integration
def test_deck_sort_reapplies_after_add_and_resets_on_load_or_manual(client: TestClient) -> None:
    assert client.post("/api/deck", json={"cards": ["Sol Ring", "Arcane Signet"]}).status_code == 200
    assert client.post("/api/deck/sort", json={"criterion": "name", "direction": "ascending"}).status_code == 200

    added = client.post("/api/add_card", json={"name": "Aether Vial", "board": "main"})

    assert added.status_code == 200
    assert added.json()["deck"]["artifact"] == ["Aether Vial", "Arcane Signet", "Sol Ring"]

    loaded = client.post(
        "/api/deck",
        json={
            "cards": [
                {"name": "Zebra", "canonical_name": "Zebra", "type_line": "Artifact"},
                {"name": "Alpha", "canonical_name": "Alpha", "type_line": "Artifact"},
            ],
        },
    )

    assert loaded.status_code == 200
    assert loaded.json()["sort"] == {"criterion": "manual", "direction": "ascending"}
    assert loaded.json()["deck"]["artifact"] == ["Zebra", "Alpha"]
    manual = client.post("/api/deck/sort", json={"criterion": "manual", "direction": "descending"})
    assert manual.status_code == 200
    assert manual.json()["deck"]["artifact"] == ["Zebra", "Alpha"]


@pytest.mark.integration
def test_deck_sort_rejects_invalid_criterion_and_direction(client: TestClient) -> None:
    assert client.post("/api/deck/sort", json={"criterion": "wrong", "direction": "ascending"}).status_code == 400
    assert client.post("/api/deck/sort", json={"criterion": "name", "direction": "wrong"}).status_code == 400


@pytest.mark.integration
def test_recommendations_prevalidate_and_reuse_add_to_maybe(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recommendation endpoint excludes illegal colors and keeps mutation on /api/add_card."""
    from types import SimpleNamespace

    from src.lib.cardDB import CardDB

    loaded = client.post(
        "/api/deck",
        json={"name": "White", "format": "commander", "colors": ["W"], "cards": ["Plains"]},
    )
    assert loaded.status_code == 200
    card_db = CardDB.inst()
    monkeypatch.setattr(card_db, "is_rag_ready", lambda: True)
    monkeypatch.setattr(card_db, "get_graph_manifest_hash", lambda: "manifest-hash")

    async def fake_analyze(deck_cards, eligible_candidates, limit, deck_context):
        eligible_names = {card.canonical_name or card.name for card in eligible_candidates}
        assert "Soul Warden" in eligible_names
        assert "Lightning Bolt" not in eligible_names
        assert deck_context["format"] == "commander"
        return SimpleNamespace(
            recommendations=(
                SimpleNamespace(
                    name="Soul Warden",
                    score=8.0,
                    reasons=("Provides deck needs: gain_life",),
                    sources=("MTGJSON oracle text",),
                ),
            ),
            explanation="Soul Warden matches the deck's creature plan [Data: Entities].",
            fingerprint="analysis-fingerprint",
        )

    monkeypatch.setattr(card_db, "analyze_deck", fake_analyze)
    response = client.post("/api/recommendations", json={"limit": 5})

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["recommendations"]] == ["Soul Warden"]
    assert body["analysis_fingerprint"] == "analysis-fingerprint"
    assert body["graph_manifest_hash"] == "manifest-hash"

    add_response = client.post("/api/add_card", json={"name": "Soul Warden", "board": "maybe"})
    assert add_response.status_code == 200
    assert "Soul Warden" in add_response.json()["deck"]["maybe_names"]


@pytest.mark.integration
def test_synergy_response_exposes_graph_evidence_and_provenance(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.lib.cardDB import CardDB

    card_db = CardDB.inst()
    monkeypatch.setattr(card_db, "is_rag_ready", lambda: True)
    monkeypatch.setattr(
        card_db,
        "get_synergy_evidence",
        lambda name_a, name_b: (
            16.0,
            [
                {
                    "source": name_a,
                    "target": name_b,
                    "token": "combo-1",
                    "kind": "combo",
                    "provenance": "Commander Spellbook",
                }
            ],
        ),
    )

    response = client.get(
        "/api/synergy",
        params={"name1": "Sanguine Bond", "name2": "Exquisite Blood"},
    )

    assert response.status_code == 200
    assert response.json()["synergy_score"] == 16.0
    assert response.json()["sources"] == ["Commander Spellbook"]
    assert response.json()["evidence"][0]["kind"] == "combo"

