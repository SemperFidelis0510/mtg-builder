# MTG Card Search — MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that provides semantic and structured search over the complete Magic: The Gathering card database. Designed for use as a Cursor MCP tool.

## Tools

| Tool | Description |
|------|-------------|
| `plain_search_card` | Structured filter by name, oracle text, type, colors, mana value, power/toughness, keywords, subtypes, supertypes, and format legality. Optional `semantic_query` + `search_type` (`general` / `trigger` / `effect`) ranks matches by embedding similarity *among cards that pass the filters* |

## Prerequisites

- **Windows** (the launcher script is a `.bat` file)
- **Conda** (Anaconda or Miniconda)
- **Git**
- *Optional*: an NVIDIA GPU with CUDA 11 or 12 for faster embedding generation

## Quick Start

```bat
git clone <repo-url>
cd MTG

.\main.bat install          REM 1. Create conda env + install deps (CUDA 12 default)
.\main.bat download         REM 2. Download card data from MTGJSON
.\main.bat build            REM 3. Build the ChromaDB vector index
.\main.bat serve            REM 4. Start the MCP server (stdio)
```

## Step-by-Step Setup

### 1. Install Dependencies

The `install` command creates a conda environment named `mtg-rag` (Python 3.11), installs PyTorch, and then installs the packages in `requirements.txt`.

```bat
.\main.bat install [11|12|cpu]
```

| Argument | PyTorch variant |
|----------|-----------------|
| `12` *(default)* | Latest PyTorch with CUDA 12.8 |
| `11` | PyTorch 2.1.2 with CUDA 11.8 |
| `cpu` | Latest PyTorch, CPU-only (no GPU) |

#### Manual Install (without `main.bat`)

```bat
conda create -n mtg-rag python=3.11 -y
conda activate mtg-rag

REM Pick ONE of the following PyTorch lines:
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128    &:: CUDA 12
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118    &:: CUDA 11
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu      &:: CPU only

pip install -r requirements.txt
```

### 2. Download Card Data

Downloads `AtomicCards.json` (~130 MB) from [MTGJSON](https://mtgjson.com/) into the `data/` directory.

```bat
.\main.bat download
```

To force a fresh re-download:

```bat
.\main.bat download force
```

### 3. Build the Vector Index

Parses every card face from `AtomicCards.json`, generates embeddings with `all-MiniLM-L6-v2`, and upserts them into a local ChromaDB database under `chroma_db/`.

```bat
.\main.bat build
```

This step benefits significantly from a CUDA GPU but works on CPU as well (just slower).

### 4. Start the Server

```bat
.\main.bat serve
```

The server communicates over **stdio** and is intended to be launched by Cursor via the `mcp.json` config.

## Cursor Integration

The repo includes an `mcp.json` that Cursor reads automatically:

```json
{
  "mcpServers": {
    "mtg-cards": {
      "command": "main.bat",
      "args": ["serve"],
      "env": {
        "MTG_LOG_LEVEL": "DEBUG"
      }
    }
  }
}
```

Once the index is built, Cursor will start the server on demand when you use either MCP tool.

## Configuration

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| `MTG_LOG_LEVEL` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

All paths and the embedding model name are defined in `src/lib/config.py`.

## Project Layout

```
MTG/
├── server.py              # MCP server entry point
├── main.bat               # Windows launcher (install / download / build / serve)
├── mcp.json               # Cursor MCP configuration
├── requirements.txt       # pip dependencies
├── src/
│   ├── lib/
│   │   ├── config.py      # Paths and model constants
│   │   ├── cardDB.py      # CardDB: load/filter AtomicCards + RAG semantic search
│   │   └── build_rag.py   # Install, download, and build pipeline
│   ├── obj/
│   │   └── card.py        # Card dataclass
│   └── utils/
│       └── logger.py      # Logging setup
├── data/                  # AtomicCards.json (downloaded, gitignored)
├── chroma_db/             # ChromaDB index (built, gitignored)
└── logs/                  # Log files (gitignored)
```
