#!/usr/bin/env python3
"""Entry point for the MTG deck editor web server."""

import uvicorn

from src.deck_editor.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
