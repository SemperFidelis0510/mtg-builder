#!/usr/bin/env python3
"""
Build the MTG RAG space: install dependencies, download AtomicCards.json, and index cards into ChromaDB.
Run this script (or use main.bat install/download/build) before starting the MCP server.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (must match server.py for CHROMA_PATH, MODEL_NAME, COLLECTION_NAME)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
ATOMIC_CARDS_URL = "https://mtgjson.com/api/v5/AtomicCards.json"
ATOMIC_CARDS_PATH = DATA_DIR / "AtomicCards.json"
CHROMA_PATH = SCRIPT_DIR / "chroma_db"
MODEL_NAME = "all-MiniLM-L6-v2"
COLLECTION_NAME = "mtg_cards"
BATCH_SIZE = 500


def parse_args():
    """Parse CLI arguments. Returns (parser, args)."""
    parser = argparse.ArgumentParser(
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
        cwd=SCRIPT_DIR,
    )
    req_path = SCRIPT_DIR / "requirements.txt"
    if not req_path.exists():
        print("requirements.txt not found; skipping remaining deps.")
        return
    print("Installing requirements from requirements.txt...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
        cwd=SCRIPT_DIR,
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
        total = int(resp.headers.get("Content-Length", 0))
        with open(tmp_path, "wb") as f:
            with tqdm(total=total, unit="B", unit_scale=True, desc="Downloading AtomicCards.json") as pbar:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
        # Validate JSON before committing
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


def card_to_document(face: dict, card_name: str) -> str:
    """Build a single document string for one card face."""
    name = face.get("name") or card_name
    mana = face.get("manaCost") or ""
    type_line = face.get("type") or ""
    text = face.get("text")
    if text is None or (isinstance(text, str) and not text.strip()):
        text = "(No rules text)"
    return (
        f"Name: {name}\nMana Cost: {mana}\nType: {type_line}\nOracle Text: {text}"
    )


def make_id(card_name: str, face_index: int) -> str:
    """Build a unique ID from the dict key (guaranteed unique) and face index."""
    return f"{card_name}::{face_index}"


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

    # Flatten: list of (id, document, metadata)
    rows = []
    for card_name, faces in data.items():
        if not isinstance(faces, list):
            continue
        for i, face in enumerate(faces):
            if not isinstance(face, dict):
                continue
            doc = card_to_document(face, card_name)
            uid = make_id(card_name, i)
            meta = {
                "name": face.get("name") or card_name,
                "type": face.get("type") or "",
                "manaCost": face.get("manaCost") or "",
                "manaValue": face.get("manaValue") or 0.0,
                "colors": ",".join(face.get("colors") or []),
                "colorIdentity": ",".join(face.get("colorIdentity") or []),
                "power": face.get("power") or "",
                "toughness": face.get("toughness") or "",
                "keywords": ",".join(face.get("keywords") or []),
                "subtypes": ",".join(face.get("subtypes") or []),
                "supertypes": ",".join(face.get("supertypes") or []),
                "loyalty": face.get("loyalty") or "",
            }
            rows.append((uid, doc, meta))

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    model = SentenceTransformer(MODEL_NAME, device=device)

    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    n = len(rows)
    for start in tqdm(range(0, n, BATCH_SIZE), desc="Building index", unit="batch"):
        batch = rows[start : start + BATCH_SIZE]
        ids_batch = [r[0] for r in batch]
        docs_batch = [r[1] for r in batch]
        metas_batch = [r[2] for r in batch]
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
