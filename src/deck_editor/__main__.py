"""Entry point for the MTG deck editor web server. Run with: python -m src.deck_editor"""

import threading
import webbrowser

import uvicorn

from src.deck_editor.app import app

_HOST = "127.0.0.1"
_PORT = 8000
_URL = f"http://{_HOST}:{_PORT}"


def _open_browser_after_delay() -> None:
    threading.Timer(1.5, lambda: webbrowser.open(_URL)).start()


if __name__ == "__main__":
    _open_browser_after_delay()
    uvicorn.run(app, host=_HOST, port=_PORT, use_colors=False)
