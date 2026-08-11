#!/usr/bin/env python3
"""
Install dependencies, configure the Gemini API key, and download AtomicCards.json.
Run via: python -m src.lib.setup --install | --configure-key | --download [--force]
Or use install.bat install / install.bat key / install.bat download.
"""

import argparse
import json
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from src.lib.config import (
    ATOMIC_CARDS_PATH,
    DATA_DIR,
    GEMINI_API_KEY_PATH,
    GEMINI_API_KEY_URL,
    REPO_ROOT,
    load_optional_gemini_api_key,
    save_gemini_api_key,
)

ATOMIC_CARDS_URL: str = "https://mtgjson.com/api/v5/AtomicCards.json"
_KEY_PROMPT_ATTEMPTS: int = 3
_NO_INPUT_MESSAGE: str = (
    "A Gemini API key is required but none is saved and this terminal cannot accept input. "
    f"Get a key at {GEMINI_API_KEY_URL} and save it to {GEMINI_API_KEY_PATH}, "
    "or run 'install.bat key' from a normal console window."
)


def _prompt(message: str) -> str:
    """Read one line, converting a closed stdin into an actionable error."""
    try:
        return input(message).strip()
    except EOFError as error:
        raise RuntimeError(_NO_INPUT_MESSAGE) from error


def parse_args() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """Parse CLI arguments. Returns (parser, args)."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Setup MTG RAG: install deps, configure Gemini key, download AtomicCards.json."
    )
    parser.add_argument("--install", action="store_true", help="Install dependencies from requirements.txt")
    parser.add_argument(
        "--configure-key",
        action="store_true",
        help="Prompt for and save the Gemini API key required by the build",
    )
    parser.add_argument("--download", action="store_true", help="Download AtomicCards.json")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reinstall satisfied requirements, replace an existing key, or re-download",
    )
    return parser, parser.parse_args()


def _unsatisfied_requirements(req_path: Path) -> list[str]:
    """Return requirement lines that are not already installed in this interpreter.

    Only bare names and `name==version` pins are verified directly; any other
    specifier form is reported as unsatisfied so pip makes the decision.
    """
    unsatisfied: list[str] = []
    for raw_line in req_path.read_text(encoding="utf-8").splitlines():
        line: str = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-") or any(char in line for char in "<>~!;[ "):
            unsatisfied.append(line)
            continue
        name, separator, pinned = line.partition("==")
        name = name.strip()
        pinned = pinned.strip()
        try:
            installed: str = version(name)
        except PackageNotFoundError:
            unsatisfied.append(line)
            continue
        if separator and installed != pinned:
            unsatisfied.append(line)
    return unsatisfied


def do_install(force: bool = False) -> None:
    """Install runtime dependencies, skipping pip when everything is satisfied."""
    req_path: Path = REPO_ROOT / "requirements.txt"
    if not req_path.exists():
        raise FileNotFoundError(f"requirements.txt not found: {req_path}")
    if not force:
        unsatisfied: list[str] = _unsatisfied_requirements(req_path)
        if not unsatisfied:
            print("All requirements already satisfied. Skipping pip. Use --force to reinstall.")
            return
        print(f"Installing {len(unsatisfied)} missing or outdated requirement(s): {', '.join(unsatisfied)}")
    else:
        print("--force: reinstalling all requirements from requirements.txt...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
        cwd=REPO_ROOT,
    )
    print("Install complete.")


def _validate_gemini_api_key(key: str) -> str | None:
    """Check the key against Gemini. Returns an error message, or None when it works."""
    try:
        from google import genai
    except ImportError:
        print("google-genai is not installed yet; skipping key validation.")
        return None
    try:
        # Keep the client alive while the lazy pager performs the request, and
        # ask for a single model so this stays the cheapest possible call.
        client = genai.Client(api_key=key)
        next(iter(client.models.list(config={"page_size": 1})), None)
    except Exception as error:  # noqa: BLE001 - any failure means we cannot confirm the key
        message: str = str(getattr(error, "message", "") or error)
        return message.strip() or type(error).__name__
    return None


def do_configure_key(force: bool = False) -> None:
    """Prompt for and persist the Gemini API key that the GraphRAG build requires."""
    existing: str | None = load_optional_gemini_api_key()
    if existing and not force:
        print(f"Gemini API key already configured: {GEMINI_API_KEY_PATH}")
        print("Use --force (install.bat key --force) to replace it.")
        return
    if not sys.stdin.isatty():
        raise RuntimeError(_NO_INPUT_MESSAGE)
    print()
    print("=== Gemini API key ===")
    print("The GraphRAG build uses Gemini for embeddings and community reports,")
    print("so it cannot run without a key.")
    print()
    print(f"  1. Open {GEMINI_API_KEY_URL}")
    print("  2. Sign in and click 'Create API key'")
    print("  3. Copy the key and paste it below")
    print()
    if existing:
        print(f"Replacing the key currently saved at {GEMINI_API_KEY_PATH}.")
    rejected: int = 0
    while rejected < _KEY_PROMPT_ATTEMPTS:
        key: str = _prompt("Paste your Gemini API key: ")
        if not key:
            # An empty line is a typo, not a failed key; do not spend an attempt on it.
            print("The key cannot be empty.")
            continue
        print("Validating key...")
        error: str | None = _validate_gemini_api_key(key)
        if error is None:
            save_gemini_api_key(key)
            print(f"Key validated and saved to {GEMINI_API_KEY_PATH}")
            return
        rejected += 1
        print(f"That key could not be validated: {error}")
        if rejected < _KEY_PROMPT_ATTEMPTS:
            answer: str = _prompt("Try a different key? [Y/n] (n saves this key anyway): ").lower()
            if answer in ("n", "no"):
                save_gemini_api_key(key)
                print(f"Saved unvalidated key to {GEMINI_API_KEY_PATH}")
                return
    raise RuntimeError(
        f"No usable Gemini API key entered after {_KEY_PROMPT_ATTEMPTS} attempts. "
        f"Get a key at {GEMINI_API_KEY_URL} and rerun 'install.bat key'."
    )


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
        tmp_path.replace(ATOMIC_CARDS_PATH)
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
        do_install(force=args.force)
        return
    if args.configure_key:
        try:
            do_configure_key(force=args.force)
        except (RuntimeError, KeyboardInterrupt) as error:
            # These are user-actionable setup problems, not defects; a traceback
            # only buries the instructions for fixing them.
            print(f"\n{error or 'Key configuration cancelled.'}")
            sys.exit(1)
        return
    if args.download:
        do_download(force=args.force)
        return
    parser.print_help()


if __name__ == "__main__":
    main()
