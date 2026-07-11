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
    # Some environments (Windows + MKL/OpenMP) can otherwise error when torch is imported.
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

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

