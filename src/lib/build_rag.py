#!/usr/bin/env python3
"""
Build the MTG RAG space: install dependencies, download AtomicCards.json, and index cards into ChromaDB.
Run this script (or use main.bat install/download/build) before starting the MCP server.
"""

import argparse
import json
import subprocess
import sys

from src.lib.config import ATOMIC_CARDS_PATH, CHROMA_PATH, COLLECTION_NAME, DATA_DIR, MODEL_NAME, REPO_ROOT
from src.lib.card_data import make_id
from src.obj.card_face import CardFace

# ---------------------------------------------------------------------------
# Build-specific constants
# ---------------------------------------------------------------------------
ATOMIC_CARDS_URL: str = "https://mtgjson.com/api/v5/AtomicCards.json"
BATCH_SIZE: int = 500


def parse_args() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """Parse CLI arguments. Returns (parser, args)."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Build MTG RAG: install deps, download AtomicCards, build ChromaDB index."
    )
    parser.add_argument("--install", action="store_true", help="Install dependencies (PyTorch CUDA + requirements)")
    parser.add_argument("--download", action="store_true", help="Download AtomicCards.json")
    parser.add_argument("--force", action="store_true", help="Force re-download even if file exists")
    parser.add_argument("--build", action="store_true", help="Ingest JSON and build ChromaDB index")
    return parser, parser.parse_args()


def do_install() -> None:
    """Install CUDA-optimized PyTorch, then remaining dependencies from requirements.txt."""
    print("Installing PyTorch with CUDA 12.4...")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "torch",
            "torchvision",
            "torchaudio",
            "--index-url",
            "https://download.pytorch.org/whl/cu128",
        ],
        cwd=REPO_ROOT,
    )
    req_path = REPO_ROOT / "requirements.txt"
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


def do_build() -> None:
    """Parse AtomicCards.json, generate embeddings on GPU, batch upsert to ChromaDB."""
    if not ATOMIC_CARDS_PATH.exists():
        raise FileNotFoundError(
            f"Data not found: {ATOMIC_CARDS_PATH}. Run with --download first."
        )

    import torch
    from sentence_transformers import SentenceTransformer
    import chromadb
    from tqdm import tqdm

    with open(ATOMIC_CARDS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("data")
    if not data:
        raise ValueError("AtomicCards.json has no 'data' key")

    rows: list[tuple[str, str, dict]] = []
    for card_name, faces in data.items():
        if not isinstance(faces, list):
            continue
        for i, face in enumerate(faces):
            if not isinstance(face, dict):
                continue
            card: CardFace = CardFace.from_json_face(face, card_name)
            uid: str = make_id(card_name, i)
            rows.append((uid, card.to_document(), card.to_chroma_metadata()))

    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    model = SentenceTransformer(MODEL_NAME, device=device)

    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    n: int = len(rows)
    for start in tqdm(range(0, n, BATCH_SIZE), desc="Building index", unit="batch"):
        batch = rows[start : start + BATCH_SIZE]
        ids_batch: list[str] = [r[0] for r in batch]
        docs_batch: list[str] = [r[1] for r in batch]
        metas_batch: list[dict] = [r[2] for r in batch]
        emb = model.encode(docs_batch, device=device, show_progress_bar=False)
        emb_list = emb.tolist()
        collection.upsert(
            ids=ids_batch,
            documents=docs_batch,
            embeddings=emb_list,
            metadatas=metas_batch,
        )
    print(f"Indexed {n} card faces in {COLLECTION_NAME}.")


def main() -> None:
    parser, args = parse_args()
    if args.install:
        do_install()
        return
    if args.download:
        do_download(force=args.force)
        return
    if args.build:
        do_build()
        return
    parser.print_help()


if __name__ == "__main__":
    main()
