"""Commander Spellbook client and strict combo normalization for GraphRAG."""

from __future__ import annotations

import time
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from src.utils.logger import LOGGER

SPELLBOOK_BASE_URL = "https://backend.commanderspellbook.com"
_PAGE_SIZE = 100
_MAX_RETRIES = 20


@dataclass(frozen=True)
class SpellbookCombo:
    """Validated subset of a Commander Spellbook variant used by the graph."""

    combo_id: str
    card_names: tuple[str, ...]
    produces: tuple[str, ...]
    description: str
    legalities: tuple[str, ...]
    requirements: tuple[str, ...]


def _card_names_from_variant(value: Any) -> list[str]:
    """Extract card names from one documented Commander Spellbook variant."""
    if not isinstance(value, dict):
        LOGGER.error("Commander Spellbook variant must be an object")
        raise TypeError("Commander Spellbook variant must be an object")
    uses = value["uses"]
    if not isinstance(uses, list):
        LOGGER.error("Commander Spellbook variant uses must be a list")
        raise TypeError("Commander Spellbook variant uses must be a list")
    names: list[str] = []
    for use in uses:
        if not isinstance(use, dict) or not isinstance(use["card"], dict):
            LOGGER.error("Commander Spellbook variant use must include a card object")
            raise TypeError("Commander Spellbook variant use must include a card object")
        name = use["card"]["name"]
        if not isinstance(name, str) or not name.strip():
            LOGGER.error("Commander Spellbook card name must be non-empty")
            raise ValueError("Commander Spellbook card name must be non-empty")
        names.append(name.strip())
    return names


def normalize_combo_variant(value: Any) -> SpellbookCombo:
    """Validate one API variant and convert it into stable graph source data."""
    if not isinstance(value, dict):
        LOGGER.error("normalize_combo_variant: variant must be an object")
        raise TypeError("Commander Spellbook variant must be an object")
    combo_id = value["id"]
    if not isinstance(combo_id, (str, int)) or not str(combo_id).strip():
        LOGGER.error("normalize_combo_variant: variant id is invalid: %r", combo_id)
        raise ValueError("Commander Spellbook variant id must be a non-empty string or integer")
    card_names = tuple(_card_names_from_variant(value))
    if not card_names:
        LOGGER.error("normalize_combo_variant: combo %s has no exact cards", combo_id)
        raise ValueError(f"Commander Spellbook combo {combo_id} has no exact cards")

    produces_value = value["produces"]
    if not isinstance(produces_value, list):
        LOGGER.error("normalize_combo_variant: combo %s produced features must be a list", combo_id)
        raise TypeError(f"Commander Spellbook combo {combo_id} produced features must be a list")
    produces: list[str] = []
    for produced in produces_value:
        if not isinstance(produced, dict) or not isinstance(produced["feature"], dict):
            LOGGER.error("normalize_combo_variant: combo %s has invalid produced feature", combo_id)
            raise TypeError(f"Commander Spellbook combo {combo_id} has an invalid produced feature")
        feature_name = produced["feature"]["name"]
        if not isinstance(feature_name, str) or not feature_name.strip():
            LOGGER.error("normalize_combo_variant: combo %s has empty produced feature name", combo_id)
            raise ValueError(f"Commander Spellbook combo {combo_id} has an empty produced feature name")
        produces.append(feature_name.strip())

    description = value["description"]
    if not isinstance(description, str) or not description.strip():
        LOGGER.error("normalize_combo_variant: combo %s has no description", combo_id)
        raise ValueError(f"Commander Spellbook combo {combo_id} has no description")
    legalities_value = value["legalities"]
    if not isinstance(legalities_value, dict):
        LOGGER.error("normalize_combo_variant: combo %s legalities must be an object", combo_id)
        raise TypeError(f"Commander Spellbook combo {combo_id} legalities must be an object")
    legalities = tuple(
        sorted(
            key
            for key, is_legal in legalities_value.items()
            if isinstance(key, str) and is_legal is True
        )
    )
    requirements_value = value["requires"]
    if not isinstance(requirements_value, list):
        LOGGER.error("normalize_combo_variant: combo %s requires must be a list", combo_id)
        raise TypeError(f"Commander Spellbook combo {combo_id} requires must be a list")
    requirements: list[str] = []
    for requirement in requirements_value:
        if not isinstance(requirement, dict) or not isinstance(requirement["template"], dict):
            LOGGER.error("normalize_combo_variant: combo %s has invalid requirement", combo_id)
            raise TypeError(f"Commander Spellbook combo {combo_id} has an invalid requirement")
        requirement_name = requirement["template"]["name"]
        if not isinstance(requirement_name, str) or not requirement_name.strip():
            LOGGER.error("normalize_combo_variant: combo %s has empty requirement name", combo_id)
            raise ValueError(f"Commander Spellbook combo {combo_id} has an empty requirement name")
        requirements.append(requirement_name.strip())
    return SpellbookCombo(
        combo_id=str(combo_id),
        card_names=card_names,
        produces=tuple(produces),
        description=description.strip(),
        legalities=legalities,
        requirements=tuple(requirements),
    )


def _get_page(offset: int, timeout_s: float) -> dict[str, Any]:
    """Fetch one page, retrying only transient rate-limit/server failures."""
    last_status = 0
    for attempt in range(_MAX_RETRIES):
        response = requests.get(
            f"{SPELLBOOK_BASE_URL}/variants/",
            params={
                "limit": _PAGE_SIZE,
                "offset": offset,
                "ordering": "id",
                "groupByCombo": "true",
            },
            timeout=timeout_s,
            headers={"Accept": "application/json", "User-Agent": "MTG-GraphRAG/1.0"},
        )
        last_status = response.status_code
        if response.status_code == 429 or response.status_code >= 500:
            LOGGER.error(
                "_get_page: transient HTTP %s at offset=%d attempt=%d",
                response.status_code,
                offset,
                attempt + 1,
            )
            retry_after = response.headers["Retry-After"] if "Retry-After" in response.headers else ""
            delay = float(retry_after) if retry_after else min(60.0, 2.0 * (2**attempt))
            time.sleep(delay)
            continue
        if not response.ok:
            LOGGER.error("_get_page: HTTP %s at offset=%d", response.status_code, offset)
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            LOGGER.error("_get_page: response at offset=%d must be an object", offset)
            raise TypeError("Commander Spellbook response must be an object")
        return payload
    LOGGER.error("_get_page: retries exhausted at offset=%d status=%s", offset, last_status)
    raise RuntimeError(
        f"Commander Spellbook request retries exhausted at offset {offset}; last HTTP status {last_status}"
    )


def _write_checkpoint(
    path: Path,
    offset: int,
    variants: list[SpellbookCombo],
    *,
    complete: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "next_offset": offset,
        "complete": complete,
        "variants": [
            {
                "combo_id": combo.combo_id,
                "card_names": combo.card_names,
                "produces": combo.produces,
                "description": combo.description,
                "legalities": combo.legalities,
                "requirements": combo.requirements,
            }
            for combo in variants
        ],
    }
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary_path.replace(path)


def _read_checkpoint(path: Path) -> tuple[int, bool, list[SpellbookCombo]]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or not isinstance(payload["next_offset"], int)
        or not isinstance(payload["complete"], bool)
    ):
        LOGGER.error("_read_checkpoint: checkpoint schema is invalid: %s", path)
        raise ValueError(f"Commander Spellbook checkpoint schema is invalid: {path}")
    values = payload["variants"]
    if not isinstance(values, list):
        LOGGER.error("_read_checkpoint: checkpoint variants must be a list: %s", path)
        raise TypeError(f"Commander Spellbook checkpoint variants must be a list: {path}")
    variants: list[SpellbookCombo] = []
    for value in values:
        if not isinstance(value, dict):
            LOGGER.error("_read_checkpoint: checkpoint combo must be an object")
            raise TypeError("Commander Spellbook checkpoint combo must be an object")
        combo_id = value["combo_id"]
        description = value["description"]
        if not isinstance(combo_id, str) or not combo_id or not isinstance(description, str) or not description:
            LOGGER.error("_read_checkpoint: checkpoint combo id/description is invalid")
            raise ValueError("Commander Spellbook checkpoint combo id/description is invalid")
        tuple_values: dict[str, tuple[str, ...]] = {}
        for field_name in ("card_names", "produces", "legalities", "requirements"):
            field_value = value[field_name]
            if not isinstance(field_value, list) or not all(
                isinstance(item, str) and item for item in field_value
            ):
                LOGGER.error("_read_checkpoint: checkpoint field %s is invalid", field_name)
                raise ValueError(f"Commander Spellbook checkpoint field {field_name} is invalid")
            tuple_values[field_name] = tuple(field_value)
        variants.append(
            SpellbookCombo(
                combo_id=combo_id,
                card_names=tuple_values["card_names"],
                produces=tuple_values["produces"],
                description=description,
                legalities=tuple_values["legalities"],
                requirements=tuple_values["requirements"],
            )
        )
    if payload["next_offset"] != len(variants):
        LOGGER.error("_read_checkpoint: offset/count mismatch in %s", path)
        raise ValueError(f"Commander Spellbook checkpoint offset/count mismatch: {path}")
    return payload["next_offset"], payload["complete"], variants


def fetch_combo_variants(
    timeout_s: float = 30.0,
    checkpoint_path: Path | None = None,
) -> list[SpellbookCombo]:
    """Fetch one validated representative variant for every Spellbook combo."""
    if timeout_s <= 0:
        LOGGER.error("fetch_combo_variants: timeout_s must be positive")
        raise ValueError("fetch_combo_variants: timeout_s must be positive")
    if checkpoint_path is not None and checkpoint_path.is_file():
        offset, complete, variants = _read_checkpoint(checkpoint_path)
        LOGGER.info("fetch_combo_variants: resumed %d validated variants", len(variants))
        if complete:
            return variants
    else:
        variants = []
        offset = 0
    while True:
        payload = _get_page(offset, timeout_s)
        if not isinstance(payload, dict) or not isinstance(payload["results"], list):
            LOGGER.error("fetch_combo_variants: invalid response schema")
            raise ValueError("Commander Spellbook response is missing results list")
        page = payload["results"]
        for variant in page:
            variants.append(normalize_combo_variant(variant))
        next_url = payload["next"]
        if next_url is None:
            offset += len(page)
            if checkpoint_path is not None:
                _write_checkpoint(checkpoint_path, offset, variants, complete=True)
            break
        if not isinstance(next_url, str) or not next_url:
            LOGGER.error("fetch_combo_variants: next must be a URL or null at offset=%d", offset)
            raise ValueError("Commander Spellbook response contains an invalid next page URL")
        if not page:
            LOGGER.error("fetch_combo_variants: empty page has a next URL at offset=%d", offset)
            raise ValueError("Commander Spellbook returned an empty page with a next URL")
        offset += len(page)
        next_offset_values = parse_qs(urlparse(next_url).query)["offset"]
        if len(next_offset_values) != 1 or int(next_offset_values[0]) != offset:
            LOGGER.error(
                "fetch_combo_variants: next URL offset mismatch expected=%d url=%s",
                offset,
                next_url,
            )
            raise ValueError("Commander Spellbook next URL offset does not match the received page")
        if offset % 5000 == 0:
            LOGGER.info("fetch_combo_variants: fetched %d variants", len(variants))
        if checkpoint_path is not None and offset % 1000 == 0:
            _write_checkpoint(checkpoint_path, offset, variants, complete=False)
        time.sleep(0.5)
    LOGGER.info("fetch_combo_variants: fetched %d variants", len(variants))
    return variants
