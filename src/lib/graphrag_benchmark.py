"""Offline GraphRAG-vs-Chroma quality gate and representative deck review."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from src.deck_editor.app import app
from src.lib.cardDB import CardDB
from src.lib.config import mtgjson_legality_key
from src.obj.card import Card
from src.utils.logger import LOGGER, init_logger


def _normalized_names(values: list[str]) -> set[str]:
    if not values:
        LOGGER.error("_normalized_names: values must not be empty")
        raise ValueError("_normalized_names: values must not be empty")
    if not all(isinstance(value, str) and value.strip() for value in values):
        LOGGER.error("_normalized_names: values must contain only non-empty strings")
        raise ValueError("_normalized_names: values must contain only non-empty strings")
    return {value.casefold() for value in values}


def _ndcg_at_ten(actual: list[str], expected: list[str]) -> float:
    """Compute binary-relevance nDCG@10."""
    expected_set = _normalized_names(expected)
    dcg = sum(
        1.0 / math.log2(index + 2)
        for index, name in enumerate(actual[:10])
        if name.casefold() in expected_set
    )
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(10, len(expected_set))))
    return dcg / ideal


def _recall_at_ten(actual: list[str], expected: list[str]) -> float:
    expected_set = _normalized_names(expected)
    if not all(isinstance(value, str) and value.strip() for value in actual):
        LOGGER.error("_recall_at_ten: actual values must contain only non-empty strings")
        raise ValueError("_recall_at_ten: actual values must contain only non-empty strings")
    actual_set = {value.casefold() for value in actual}
    return len(actual_set & expected_set) / len(expected_set)


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        LOGGER.error("%s file not found: %s", label, path)
        raise FileNotFoundError(f"{label} file not found: {path}")
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        LOGGER.error("%s must be a JSON object", label)
        raise TypeError(f"{label} must be a JSON object")
    return value


def _assert_card_eligible(card: Card, colors: list[str], format_name: str) -> None:
    """Independent reference-model assertion for the zero-illegal gate."""
    allowed_colors = {color.upper() for color in colors}
    identity = {color.upper() for color in card.color_identity}
    if allowed_colors and not identity.issubset(allowed_colors):
        raise AssertionError(
            f"{card.canonical_name or card.name} has identity {sorted(identity)} "
            f"outside {sorted(allowed_colors)}"
        )
    legality_key = mtgjson_legality_key(format_name)
    legal_value = card.legalities[legality_key] if legality_key in card.legalities else ""
    if legal_value.casefold() != "legal":
        raise AssertionError(
            f"{card.canonical_name or card.name} is not legal in {format_name}: {legal_value!r}"
        )


def _load_deck_and_analyze(
    client: TestClient,
    *,
    name: str,
    format_name: str,
    colors: list[str],
    commander: str,
    cards: list[str],
    limit: int = 12,
) -> tuple[list[dict[str, Any]], float]:
    payload = {
        "name": name,
        "format": format_name,
        "colors": colors,
        "commander": commander,
        "cards": cards,
    }
    load_response = client.post("/api/deck", json=payload)
    if load_response.status_code != 200:
        LOGGER.error("_load_deck_and_analyze: deck load failed %s: %s", name, load_response.text)
        raise RuntimeError(f"Benchmark deck load failed for {name}: {load_response.text}")
    start = time.perf_counter()
    response = client.post("/api/recommendations", json={"limit": limit})
    latency_ms = (time.perf_counter() - start) * 1000
    if response.status_code != 200:
        LOGGER.error("_load_deck_and_analyze: analysis failed %s: %s", name, response.text)
        raise RuntimeError(f"Benchmark recommendation analysis failed for {name}: {response.text}")
    body = response.json()
    recommendations = body["recommendations"]
    if not isinstance(recommendations, list):
        LOGGER.error("_load_deck_and_analyze: recommendations is not a list for %s", name)
        raise TypeError(f"Benchmark recommendations is not a list for {name}")
    return recommendations, latency_ms


def _manual_review_passed(
    review_path: Path | None,
    expected_deck_names: set[str],
) -> tuple[bool, dict[str, Any] | None]:
    if review_path is None:
        return False, None
    review = _read_object(review_path, "Manual review")
    if review["approved"] is not True:
        return False, review
    reviewer = review["reviewer"]
    reviews = review["decks"]
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reviews, list):
        LOGGER.error("_manual_review_passed: review metadata is invalid")
        raise ValueError("Manual review must contain a reviewer and decks list")
    reviewed_names: set[str] = set()
    for deck_review in reviews:
        if not isinstance(deck_review, dict):
            LOGGER.error("_manual_review_passed: deck review must be an object")
            raise TypeError("Manual deck review must be an object")
        name = deck_review["name"]
        notes = deck_review["notes"]
        if (
            not isinstance(name, str)
            or deck_review["passed"] is not True
            or not isinstance(notes, str)
            or not notes.strip()
        ):
            return False, review
        reviewed_names.add(name)
    return reviewed_names == expected_deck_names, review


def collect_benchmark(
    cases_path: Path,
    report_path: Path,
    manual_review_path: Path | None = None,
) -> dict[str, Any]:
    """Run automated comparison and collect representative deck outputs."""
    cases = _read_object(cases_path, "Benchmark cases")
    queries = cases["queries"]
    combo_cases = cases["combo_completion"]
    manual_decks = cases["manual_decks"]
    if not isinstance(queries, list) or not queries:
        LOGGER.error("collect_benchmark: benchmark queries must be a non-empty list")
        raise ValueError("Benchmark queries must be a non-empty list")
    if not isinstance(combo_cases, list) or not combo_cases:
        LOGGER.error("collect_benchmark: combo cases must be a non-empty list")
        raise ValueError("Combo completion cases must be a non-empty list")
    if not isinstance(manual_decks, list) or not manual_decks:
        LOGGER.error("collect_benchmark: manual decks must be a non-empty list")
        raise ValueError("Manual benchmark decks must be a non-empty list")

    card_db = CardDB.inst()
    if not card_db.is_rag_ready():
        card_db.load_rag_sync()
    query_reports: list[dict[str, Any]] = []
    for case in queries:
        if not isinstance(case, dict):
            LOGGER.error("collect_benchmark: query case must be an object")
            raise TypeError("Benchmark query case must be an object")
        query = case["query"]
        scope = case["search_type"]
        expected = case["expected"]
        baseline = case["chroma_baseline"]
        if not isinstance(query, str) or scope not in ("general", "trigger", "effect"):
            LOGGER.error("collect_benchmark: query case has invalid query or scope")
            raise ValueError("Benchmark query case has invalid query or search_type")
        start = time.perf_counter()
        graph_names = [
            result["name"]
            for result in card_db.semantic_search_structured(query, scope, n_results=10)
        ]
        latency_ms = (time.perf_counter() - start) * 1000
        query_reports.append(
            {
                "query": query,
                "search_type": scope,
                "graph_results": graph_names,
                "chroma_results": baseline,
                "graph_ndcg_at_10": _ndcg_at_ten(graph_names, expected),
                "chroma_ndcg_at_10": _ndcg_at_ten(baseline, expected),
                "graph_recall_at_10": _recall_at_ten(graph_names, expected),
                "chroma_recall_at_10": _recall_at_ten(baseline, expected),
                "latency_ms": latency_ms,
            }
        )
    mean_graph_ndcg = sum(item["graph_ndcg_at_10"] for item in query_reports) / len(query_reports)
    mean_chroma_ndcg = sum(item["chroma_ndcg_at_10"] for item in query_reports) / len(query_reports)
    mean_graph_recall = sum(item["graph_recall_at_10"] for item in query_reports) / len(query_reports)
    mean_chroma_recall = sum(item["chroma_recall_at_10"] for item in query_reports) / len(query_reports)

    combo_reports: list[dict[str, Any]] = []
    manual_reports: list[dict[str, Any]] = []
    zero_illegal = True
    with TestClient(app) as client:
        for combo_case in combo_cases:
            recommendations, latency_ms = _load_deck_and_analyze(
                client,
                name=f"Combo holdout: {combo_case['expected']}",
                format_name=combo_case["format"],
                colors=combo_case["colors"],
                commander="",
                cards=combo_case["deck_cards"],
                limit=20,
            )
            names = [item["name"] for item in recommendations]
            expected_name = combo_case["expected"]
            combo_reports.append(
                {
                    "deck_cards": combo_case["deck_cards"],
                    "expected": expected_name,
                    "results": names,
                    "completed": expected_name in names,
                    "latency_ms": latency_ms,
                }
            )

        for deck_case in manual_decks:
            recommendations, latency_ms = _load_deck_and_analyze(
                client,
                name=deck_case["name"],
                format_name=deck_case["format"],
                colors=deck_case["colors"],
                commander=deck_case["commander"],
                cards=deck_case["cards"],
            )
            for recommendation in recommendations:
                try:
                    _assert_card_eligible(
                        card_db.resolve_primary_card(recommendation["name"]),
                        deck_case["colors"],
                        deck_case["format"],
                    )
                except AssertionError:
                    zero_illegal = False
                    raise
            manual_reports.append(
                {
                    "name": deck_case["name"],
                    "format": deck_case["format"],
                    "colors": deck_case["colors"],
                    "recommendations": recommendations,
                    "latency_ms": latency_ms,
                }
            )

    expected_manual_names = {str(deck["name"]) for deck in manual_decks}
    manual_passed, manual_review = _manual_review_passed(
        manual_review_path,
        expected_manual_names,
    )
    automated_passed = (
        mean_graph_ndcg >= mean_chroma_ndcg
        and mean_graph_recall >= mean_chroma_recall
        and all(item["completed"] for item in combo_reports)
        and zero_illegal
    )
    report = {
        "passed": automated_passed and manual_passed,
        "automated_passed": automated_passed,
        "manual_review_passed": manual_passed,
        "zero_illegal_recommendations": zero_illegal,
        "mean_graph_ndcg_at_10": mean_graph_ndcg,
        "mean_chroma_ndcg_at_10": mean_chroma_ndcg,
        "mean_graph_recall_at_10": mean_graph_recall,
        "mean_chroma_recall_at_10": mean_chroma_recall,
        "queries": query_reports,
        "combo_completion": combo_reports,
        "manual_decks": manual_reports,
        "manual_review": manual_review,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run_benchmark(
    cases_path: Path,
    report_path: Path,
    manual_review_path: Path,
) -> dict[str, Any]:
    """Run the fail-closed cutover gate and raise unless every gate passes."""
    report = collect_benchmark(cases_path, report_path, manual_review_path)
    if not report["automated_passed"]:
        LOGGER.error("run_benchmark: GraphRAG automated quality gates failed")
        raise ValueError("GraphRAG automated quality gates failed")
    if not report["manual_review_passed"]:
        LOGGER.error("run_benchmark: representative manual deck review failed")
        raise ValueError("Representative manual deck review failed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GraphRAG cutover quality gate")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manual-review", type=Path)
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    init_logger("graphrag_benchmark")
    cases_path = args.cases.resolve()
    report_path = args.report.resolve()
    manual_review_path = args.manual_review.resolve() if args.manual_review is not None else None
    if args.collect:
        collect_benchmark(cases_path, report_path, manual_review_path)
        return
    if manual_review_path is None:
        LOGGER.error("main: --manual-review is required unless --collect is used")
        raise ValueError("--manual-review is required unless --collect is used")
    run_benchmark(cases_path, report_path, manual_review_path)


if __name__ == "__main__":
    main()
