#!/usr/bin/env python3
"""
Install dependencies (PyTorch CUDA + requirements) and download AtomicCards.json.
Run via: python -m src.lib.setup --install [--cuda 11|12|cpu] | --download [--force]
Or use install.bat install / install.bat download.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from src.lib.config import ATOMIC_CARDS_PATH, DATA_DIR, REPO_ROOT

# ---------------------------------------------------------------------------
# Setup-specific constants
# ---------------------------------------------------------------------------
ATOMIC_CARDS_URL: str = "https://mtgjson.com/api/v5/AtomicCards.json"

# (pip packages, wheel index URL, human-readable label)
_CUDA_CONFIGS: dict[str, tuple[list[str], str, str]] = {
    "12": (
        ["torch", "torchvision", "torchaudio"],
        "https://download.pytorch.org/whl/cu128",
        "CUDA 12.8",
    ),
    "11": (
        ["torch==2.1.2", "torchvision==0.16.2", "torchaudio==2.1.2"],
        "https://download.pytorch.org/whl/cu118",
        "CUDA 11.8 (PyTorch 2.1.2)",
    ),
    "cpu": (
        ["torch", "torchvision", "torchaudio"],
        "https://download.pytorch.org/whl/cpu",
        "CPU-only",
    ),
}


def parse_args() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """Parse CLI arguments. Returns (parser, args)."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Setup MTG RAG: install deps, download AtomicCards.json."
    )
    parser.add_argument("--install", action="store_true", help="Install dependencies (PyTorch CUDA + requirements)")
    parser.add_argument(
        "--cuda", default="12", choices=list(_CUDA_CONFIGS),
        help="CUDA version for PyTorch install: 12 (default), 11, or cpu",
    )
    parser.add_argument("--download", action="store_true", help="Download AtomicCards.json")
    parser.add_argument("--force", action="store_true", help="Force re-download even if file exists")
    return parser, parser.parse_args()


def do_install(cuda: str) -> None:
    """Install PyTorch for the chosen CUDA target, then remaining deps from requirements.txt."""
    packages: list[str]
    index_url: str
    label: str
    packages, index_url, label = _CUDA_CONFIGS[cuda]
    print(f"Installing PyTorch ({label})...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", *packages, "--index-url", index_url],
        cwd=REPO_ROOT,
    )
    req_path: Path = REPO_ROOT / "requirements.txt"
    if not req_path.exists():
        print("requirements.txt not found; skipping remaining deps.")
        return
    print("Installing requirements from requirements.txt...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
        cwd=REPO_ROOT,
    )
    print("Install complete.")


def do_download(force: bool) -> None:
    """Download AtomicCards.json with progress bar; atomic write; validate JSON."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if ATOMIC_CARDS_PATH.exists() and not force:
        print(f"Already exists: {ATOMIC_CARDS_PATH}. Use --force to re-download.")
        return
    import requests
    from tqdm import tqdm

    tmp_path = ATOMIC_CARDS_PATH.with_suffix(".json.tmp")
    try:
        resp = requests.get(ATOMIC_CARDS_URL, stream=True, timeout=60)
        resp.raise_for_status()
        total: int = int(resp.headers.get("Content-Length", 0))
        with open(tmp_path, "wb") as f:
            with tqdm(total=total, unit="B", unit_scale=True, desc="Downloading AtomicCards.json") as pbar:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
        with open(tmp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "data" not in data:
            raise ValueError("JSON missing 'data' key")
        tmp_path.rename(ATOMIC_CARDS_PATH)
        print(f"Saved: {ATOMIC_CARDS_PATH}")
    except requests.RequestException as e:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Download request failed: {e}") from e
    except json.JSONDecodeError as e:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Invalid or malformed JSON: {e}") from e
    except (ValueError, OSError) as e:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Download failed: {e}") from e


def main() -> None:
    parser, args = parse_args()
    if args.install:
        do_install(cuda=args.cuda)
        return
    if args.download:
        do_download(force=args.force)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
