"""Card dataclass -- single source of truth for MTG card data representation."""

import dataclasses
from dataclasses import dataclass, field, fields
from typing import Any


def _field_default(f: dataclasses.Field) -> Any:
    """Return the effective default value for a dataclass field."""
    if f.default is not dataclasses.MISSING:
        return f.default
    assert f.default_factory is not dataclasses.MISSING, f"Field {f.name} has no default"
    return f.default_factory()


@dataclass
class Card:
    """Represents a Magic: The Gathering card (one face).

    Field metadata drives implicit JSON parsing, ChromaDB serialization,
    and display formatting via ``fields()`` iteration -- adding a new field
    automatically propagates through the whole pipeline.

    Metadata keys per field:
        json_key      -- key in AtomicCards.json (required for MTGJSON-sourced fields)
        fallback_key  -- secondary JSON key tried when the primary is None
        chroma        -- False to exclude from ChromaDB metadata (default True)
        source        -- "derived" for fields not in MTGJSON (e.g. triggers, effects)
    """

    name: str = field(default="", metadata={"json_key": "name"})
    type_line: str = field(default="", metadata={"json_key": "type"})
    types: list[str] = field(default_factory=list, metadata={"json_key": "types"})
    subtypes: list[str] = field(default_factory=list, metadata={"json_key": "subtypes"})
    supertypes: list[str] = field(default_factory=list, metadata={"json_key": "supertypes"})
    text: str = field(default="", metadata={"json_key": "text", "chroma": False})
    mana_cost: str = field(default="", metadata={"json_key": "manaCost"})
    mana_value: float = field(
        default=0.0,
        metadata={"json_key": "manaValue", "fallback_key": "convertedManaCost"},
    )
    colors: list[str] = field(default_factory=list, metadata={"json_key": "colors"})
    color_identity: list[str] = field(default_factory=list, metadata={"json_key": "colorIdentity"})
    power: str = field(default="", metadata={"json_key": "power"})
    toughness: str = field(default="", metadata={"json_key": "toughness"})
    keywords: list[str] = field(default_factory=list, metadata={"json_key": "keywords"})
    loyalty: str = field(default="", metadata={"json_key": "loyalty"})
    defense: str = field(default="", metadata={"json_key": "defense"})
    legalities: dict[str, str] = field(
        default_factory=dict,
        metadata={"json_key": "legalities", "chroma": False},
    )
    triggers: list[str] = field(
        default_factory=list,
        metadata={"source": "derived"},
    )
    effects: list[str] = field(
        default_factory=list,
        metadata={"source": "derived"},
    )

    def __iter__(self):
        for f in fields(self):
            yield f.name, getattr(self, f.name)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_json_face(cls, face: dict, card_name: str) -> "Card":
        """Parse a raw JSON face dict from AtomicCards.json into a Card."""
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.metadata.get("source") == "derived":
                continue
            json_key: str = f.metadata["json_key"]
            raw = face.get(json_key)
            if raw is None and "fallback_key" in f.metadata:
                raw = face.get(f.metadata["fallback_key"])

            default = _field_default(f)

            if isinstance(default, float):
                kwargs[f.name] = float(raw) if raw is not None else default
            elif isinstance(default, list):
                kwargs[f.name] = raw if isinstance(raw, list) else []
            elif isinstance(default, dict):
                kwargs[f.name] = raw if isinstance(raw, dict) else {}
            else:
                kwargs[f.name] = raw or default

        if not kwargs["name"]:
            kwargs["name"] = card_name
        return cls(**kwargs)

    @classmethod
    def from_chroma_result(cls, meta: dict | None, doc: str) -> "Card":
        """Build a Card from a ChromaDB query result (flat metadata + document)."""
        safe_meta: dict = meta or {}
        text: str = doc.split("Oracle Text:", 1)[-1].strip() if doc else ""

        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name == "text":
                kwargs["text"] = text
                continue
            if f.metadata.get("source") == "derived":
                continue

            json_key: str = f.metadata["json_key"]
            raw = safe_meta.get(json_key)
            default = _field_default(f)

            if isinstance(default, float):
                kwargs[f.name] = float(raw) if raw is not None else default
            elif isinstance(default, list):
                if isinstance(raw, str):
                    kwargs[f.name] = [v.strip() for v in raw.split(",") if v.strip()] if raw else []
                elif isinstance(raw, list):
                    kwargs[f.name] = raw
                else:
                    kwargs[f.name] = []
            elif isinstance(default, dict):
                kwargs[f.name] = raw if isinstance(raw, dict) else {}
            else:
                kwargs[f.name] = raw or default

        return cls(**kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Card":
        """Create a Card from a dict with field-name keys."""
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name in data:
                kwargs[f.name] = data[f.name]
        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Serialization / display
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this Card to a plain dict keyed by field name."""
        result: dict[str, Any] = {}
        for name, value in self:
            if isinstance(value, list):
                result[name] = list(value)
            elif isinstance(value, dict):
                result[name] = dict(value)
            else:
                result[name] = value
        return result

    def to_document(self) -> str:
        """Build a document string for embedding / indexing this card."""
        text: str = self.text if self.text.strip() else "(No rules text)"
        return f"Name: {self.name}\nMana Cost: {self.mana_cost}\nType: {self.type_line}\nOracle Text: {text}"

    def to_chroma_metadata(self) -> dict[str, str | float]:
        """Build a flat metadata dict for ChromaDB (lists are comma-joined).

        Fields with ``metadata["chroma"] == False`` or ``metadata["source"] == "derived"`` are excluded.
        """
        meta: dict[str, str | float] = {}
        for f in fields(self):
            if f.metadata.get("source") == "derived":
                continue
            if "chroma" in f.metadata and not f.metadata["chroma"]:
                continue
            json_key: str = f.metadata["json_key"]
            val = getattr(self, f.name)
            if isinstance(val, list):
                meta[json_key] = ",".join(str(v) for v in val)
            else:
                meta[json_key] = val
        return meta

    def format_display(self, index: int, total: int) -> str:
        """Format this card for display in search / filter results."""
        text: str = self.text.strip() or "(No rules text)"
        return (
            f"--- Card {index} of {total} ---\n"
            f"Name: {self.name}\nMana Cost: {self.mana_cost}\nType: {self.type_line}\nOracle Text: {text}"
        )
