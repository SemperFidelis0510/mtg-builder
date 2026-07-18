# MTG Card Search — MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) server that provides semantic and structured search over the complete Magic: The Gathering card database. Designed for use as a Cursor MCP tool.

## Tools

| Tool | Description |
|------|-------------|
| `plain_search_card` | Structured filter by name, oracle text, type, colors, mana value, power/toughness, keywords, subtypes, supertypes, and format legality. Optional `semantic_query` + `search_type` (`general` / `trigger` / `effect`) ranks GraphRAG evidence *among cards that pass the filters* |

## Prerequisites

- **Windows** (the launcher script is a `.bat` file)
- **Conda** (Anaconda or Miniconda)
- **Git**
- A saved Gemini API key in `%USERPROFILE%\.mtgbuilder\agent\.key`

## Quick Start

```bat
git clone <repo-url>
cd MTG

.\install.bat install       REM 1. Create conda env + install deps
.\install.bat download      REM 2. Download card data from MTGJSON
.\install.bat build         REM 3. Build GraphRAG graph, reports, and LanceDB index
.\server.bat                REM 4. Start the MCP server (stdio)
```

## Step-by-Step Setup

### 1. Install Dependencies

The `install` command creates a conda environment named `mtg-rag` (Python 3.11) and installs the packages in `requirements.txt`. GraphRAG does not support Python 3.13, so run project commands inside this environment.

```bat
.\install.bat install
```

#### Manual Install (without `install.bat`)

```bat
conda create -n mtg-rag python=3.11 -y
conda activate mtg-rag

pip install -r requirements.txt
```

### 2. Download Card Data

Downloads `AtomicCards.json` (~130 MB) from [MTGJSON](https://mtgjson.com/) into the `data/` directory.

```bat
.\install.bat download
```

To force a fresh re-download:

```bat
.\install.bat download --force
```

### 3. Build the GraphRAG Index

Build reads MTGJSON oracle text and EDHREC ranks, produces deterministic typed mechanic signatures, imports attributed Commander Spellbook combos, runs GraphRAG community detection, generates top-level community reports, and writes Gemini embeddings to local LanceDB under `data/graphrag/`. The graph build is intentionally strict: unavailable Gemini or Commander Spellbook data fails the build rather than producing an incomplete index.

```bat
.\install.bat build
```

The build uses Gemini `gemini-embedding-001` for 34,633 canonical cards and 775 mechanic entities. Those vectors are reused for card text units and the runtime search table rather than billed twice. Gemini `gemini-3.1-flash-lite` generates 46 top-level community reports on the current corpus; report responses are cached by graph content and model. A complete first build currently takes about 30 minutes on the tested Windows machine. Actual cost depends on token counts and Google's current pricing, so review the Gemini price and quota pages before rebuilding.

Commander Spellbook downloads are checkpointed under `data/graphrag/`, retried on 429/5xx responses, and reused by clean index rebuilds. The initial import currently contains 27,332 combo variants; later builds do not redownload that snapshot unless it is removed explicitly.

Before treating a newly built index as production-ready, collect the offline benchmark:

```bat
conda activate mtg-rag
python -m src.lib.graphrag_benchmark --collect --cases tests\fixtures\graphrag_benchmark.json --report data\graphrag\benchmark-report.json
```

Review every deck in `manual_decks` in that report, then create a JSON review with `approved: true`, a non-empty `reviewer`, and one `{name, passed, notes}` object per representative deck. Run the fail-closed gate:

```bat
python -m src.lib.graphrag_benchmark --cases tests\fixtures\graphrag_benchmark.json --report data\graphrag\benchmark-report.json --manual-review data\graphrag\manual-review.json
```

The gate requires mean nDCG@10 and Recall@10 at least equal to the frozen Chroma baseline, all held-out Commander Spellbook completion cases, zero illegal recommendations, and approved representative reviews. Chroma is not a runtime dependency; its frozen results remain only as the pre-cutover benchmark.

### 4. Start the Server

```bat
.\server.bat
```

The server communicates over **stdio** and is intended to be launched by Cursor via the `mcp.json` config.

## Updating the Card Database

When new sets release (or you want fresh Scryfall prices), run:

```bat
.\install.bat update
```

This runs three steps in order:

1. **Download** — force-refresh `data/AtomicCards.json` from MTGJSON.
2. **Prices** — refresh USD prices from Scryfall into `data/prices.json`.
3. **Rebuild** — clean-rebuild deterministic graph tables, top-level GraphRAG community reports, and LanceDB documents so cards removed or renamed upstream do not leave stale rows behind. The complete Commander Spellbook checkpoint is preserved and reused.

**Restart required.** The MCP server (`server.bat`) and the deck editor (`editor.bat`) cache card JSON, name indexes, and GraphRAG handles in memory. Stop them before running the update — or restart them afterward — so they pick up the new data.

## Cursor Integration

The repo includes an `mcp.json` that Cursor reads automatically:

```json
{
  "mcpServers": {
    "mtg-cards": {
      "command": "server.bat",
      "args": [],
      "env": {
        "MTG_LOG_LEVEL": "DEBUG"
      }
    }
  }
}
```

Once the index is built, Cursor starts the server on demand when the MCP tool is used. Run `.\editor.bat` to open the deck editor and its on-demand Recommendations tab.

## Deck Recommendations

The Recommendations tab analyzes the current boards, commander, format, and color identity only when requested. Exact deck rules prevalidate candidates before graph ranking. Results include deterministic mechanic/combo reasons, one GraphRAG local-search explanation with citations, and an explicit **Add to Maybe** confirmation action. Complete analyses are cached by canonical deck state, eligible candidates, graph manifest, model IDs, and request limit; any relevant change invalidates the cache.

## Configuration

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| `MTG_LOG_LEVEL` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `MTG_DISABLE_PRICE_STARTUP` | Set to `1`, `true`, `yes`, or `on` to skip the deck editor's background startup price refresh | unset |

All GraphRAG paths and model names are defined in `src/lib/config.py`. The generated `data/graphrag/output/manifest.json` records the graph schema, source hashes, artifact hashes, and model IDs used for exact runtime validation and cache invalidation.

## Attribution

- [Microsoft GraphRAG](https://github.com/microsoft/graphrag) (MIT, Microsoft Corporation) provides BYOG community indexing and local search.
- [Commander Spellbook](https://commanderspellbook.com/) and its [MIT-licensed backend](https://github.com/SpaceCowMedia/commander-spellbook-backend) supply curated combo data through the public API; every corresponding edge and explanation identifies Commander Spellbook provenance.
- Urza, HexDek, DeckSage, and j4th/mtg-mcp-server informed design and evaluation only; none are runtime dependencies.

## Project Layout

```
MTG/
├── server.py              # MCP server entry point
├── install.bat            # Windows setup, download, update, and build launcher
├── server.bat             # MCP server launcher
├── editor.bat             # Deck editor launcher
├── mcp.json               # Cursor MCP configuration
├── requirements.txt       # pip dependencies
├── src/
│   ├── lib/
│   │   ├── config.py      # Paths and model constants
│   │   ├── cardDB.py      # CardDB: exact filters + GraphRAG integration
│   │   ├── graphrag_build.py      # Deterministic BYOG graph generation
│   │   ├── graphrag_service.py    # Runtime retrieval, synergy, and recommendations
│   │   ├── graphrag_benchmark.py  # Fail-closed quality gate
│   │   └── build_rag.py           # GraphRAG/embedding build pipeline
│   ├── obj/
│   │   └── card.py        # Card dataclass
│   └── utils/
│       └── logger.py      # Logging setup
├── data/                  # AtomicCards.json (downloaded, gitignored)
├── data/graphrag/         # GraphRAG tables, reports, and LanceDB index (built, gitignored)
└── logs/                  # Log files (gitignored)
```
