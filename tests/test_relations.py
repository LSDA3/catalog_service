"""Tests of the relations block: how they are built and how they are stored.

They protect the rules that make a relation admissible and a small set of links
that the catalog itself states unambiguously. They deliberately do not freeze a
complete LLM-produced mesh: semantic neighbours that satisfy the same written
criterion are not made invalid merely because a previous reconstruction selected
a different one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import loader  # noqa: E402
import relate  # noqa: E402

CSV = ROOT / "data" / "catalog.csv"
VOCABULARIES = ROOT / "data" / "vocabularies.yaml"
SEMANTIC_LAYER = ROOT / "data" / "semantic_layer.json"

CANONICAL = {"KD-001", "KD-002", "KD-003", "HL-009", "HL-010", "HL-025"}


@pytest.fixture(scope="module")
def entries() -> dict[str, dict]:
    layer = json.loads(SEMANTIC_LAYER.read_text(encoding="utf-8"))
    return layer["products"] if "products" in layer else layer


@pytest.fixture(scope="module")
def loaded():
    return loader.load(CSV, VOCABULARIES, SEMANTIC_LAYER)


# --------------------------------------------------------------------------
# A4.7 · `pairs_with` keeps its direction and is not reordered
# --------------------------------------------------------------------------


def test_pairs_with_keeps_the_direction_it_arrives_with():
    """KD-002 is the accessory of KD-001, so the edge lives under KD-002."""
    result = relate.normalize_relations(
        {"KD-002": {"pairs_with": ["KD-001"], "alternative_to": []}}, CANONICAL
    )
    assert result["KD-002"]["pairs_with"] == ["KD-001"]
    assert result["KD-001"]["pairs_with"] == []


def test_pairs_with_is_not_reordered_by_product_id():
    """The smaller identifier can legitimately hold the edge when it is the accessory."""
    result = relate.normalize_relations(
        {"KD-001": {"pairs_with": ["KD-002"], "alternative_to": []}}, CANONICAL
    )
    assert result["KD-001"]["pairs_with"] == ["KD-002"]
    assert result["KD-002"]["pairs_with"] == []


def test_an_exact_repetition_of_pairs_with_is_dropped():
    result = relate.normalize_relations(
        {"KD-003": {"pairs_with": ["KD-001", "KD-001", "KD-002"], "alternative_to": []}},
        CANONICAL,
    )
    assert result["KD-003"]["pairs_with"] == ["KD-001", "KD-002"]


def test_pairs_with_in_both_directions_stops_the_build():
    """Which of the two is the accessory cannot be decided here, so it is not."""
    with pytest.raises(relate.InvalidRelations, match="both directions"):
        relate.normalize_relations(
            {
                "KD-001": {"pairs_with": ["KD-002"], "alternative_to": []},
                "KD-002": {"pairs_with": ["KD-001"], "alternative_to": []},
            },
            CANONICAL,
        )


# --------------------------------------------------------------------------
# A4.8 · `alternative_to` under the smaller identifier, with its nature
# --------------------------------------------------------------------------


def test_alternative_to_is_stored_under_the_smaller_identifier():
    result = relate.normalize_relations(
        {
            "HL-010": {
                "pairs_with": [],
                "alternative_to": [
                    {"product_id": "HL-009", "relation_type": "equivalent"}
                ],
            }
        },
        CANONICAL,
    )
    assert result["HL-009"]["alternative_to"] == [
        {"product_id": "HL-010", "relation_type": "equivalent"}
    ]
    assert result["HL-010"]["alternative_to"] == []


def test_the_same_pair_from_both_ends_is_written_once():
    result = relate.normalize_relations(
        {
            "HL-009": {
                "pairs_with": [],
                "alternative_to": [
                    {"product_id": "HL-010", "relation_type": "equivalent"}
                ],
            },
            "HL-010": {
                "pairs_with": [],
                "alternative_to": [
                    {"product_id": "HL-009", "relation_type": "equivalent"}
                ],
            },
        },
        CANONICAL,
    )
    assert result["HL-009"]["alternative_to"] == [
        {"product_id": "HL-010", "relation_type": "equivalent"}
    ]
    assert result["HL-010"]["alternative_to"] == []


def test_the_same_pair_with_two_different_natures_stops_the_build():
    with pytest.raises(relate.InvalidRelations, match="arrives as"):
        relate.normalize_relations(
            {
                "HL-009": {
                    "pairs_with": [],
                    "alternative_to": [
                        {"product_id": "HL-010", "relation_type": "equivalent"}
                    ],
                },
                "HL-010": {
                    "pairs_with": [],
                    "alternative_to": [
                        {"product_id": "HL-009", "relation_type": "same_function"}
                    ],
                },
            },
            CANONICAL,
        )


# --------------------------------------------------------------------------
# Nothing invalid becomes valid on the way through
# --------------------------------------------------------------------------


def test_an_invalid_relation_type_stops_the_build():
    with pytest.raises(relate.InvalidRelations, match="is not valid"):
        relate.normalize_relations(
            {
                "HL-009": {
                    "pairs_with": [],
                    "alternative_to": [
                        {"product_id": "HL-010", "relation_type": "cheaper_version"}
                    ],
                }
            },
            CANONICAL,
        )


def test_a_missing_relation_type_stops_the_build():
    with pytest.raises(relate.InvalidRelations, match="is not valid"):
        relate.normalize_relations(
            {"HL-009": {"pairs_with": [], "alternative_to": [{"product_id": "HL-010"}]}},
            CANONICAL,
        )


@pytest.mark.parametrize("field", ["pairs_with", "alternative_to"])
def test_a_reference_to_a_product_that_does_not_exist_stops_the_build(field):
    link = "ZZ-999" if field == "pairs_with" else {
        "product_id": "ZZ-999", "relation_type": "same_function"
    }
    with pytest.raises(relate.InvalidRelations, match="not canonical"):
        relate.normalize_relations({"KD-001": {field: [link]}}, CANONICAL)


@pytest.mark.parametrize("field", ["pairs_with", "alternative_to"])
def test_a_product_related_to_itself_stops_the_build(field):
    link = "KD-001" if field == "pairs_with" else {
        "product_id": "KD-001", "relation_type": "same_function"
    }
    with pytest.raises(relate.InvalidRelations, match="itself"):
        relate.normalize_relations({"KD-001": {field: [link]}}, CANONICAL)


def test_relations_proposed_for_a_product_that_is_not_canonical_stop_the_build():
    with pytest.raises(relate.InvalidRelations, match="not canonical"):
        relate.normalize_relations(
            {"KD-024": {"pairs_with": ["KD-001"], "alternative_to": []}}, CANONICAL
        )


def test_every_problem_is_reported_at_once():
    with pytest.raises(relate.InvalidRelations) as refused:
        relate.normalize_relations(
            {
                "KD-001": {"pairs_with": ["ZZ-999"], "alternative_to": []},
                "KD-002": {"pairs_with": ["KD-002"], "alternative_to": []},
            },
            CANONICAL,
        )
    assert "ZZ-999" in str(refused.value)
    assert "itself" in str(refused.value)


def test_a_product_with_no_relations_gets_two_empty_lists():
    result = relate.normalize_relations({}, CANONICAL)
    assert set(result) == CANONICAL
    for entry in result.values():
        assert entry == {"pairs_with": [], "alternative_to": []}


# --------------------------------------------------------------------------
# B0.5 · derived `same_function` is not persisted as the same fact again
# --------------------------------------------------------------------------


def test_no_persisted_same_function_joins_two_products_of_the_same_type(entries):
    """Shared product_type already yields same_function at runtime."""
    for product_id, entry in entries.items():
        for link in entry.get("alternative_to") or []:
            if link["relation_type"] != "same_function":
                continue
            other = link["product_id"]
            assert entries[product_id]["product_type"] != entries[other]["product_type"], (
                f"{product_id} · {other}: same_function persisted while sharing "
                "product_type, which runtime already derives"
            )

    with pytest.raises(relate.InvalidRelations, match="already derived"):
        relate.normalize_relations(
            {
                "KD-001": {
                    "pairs_with": [],
                    "alternative_to": [
                        {"product_id": "KD-002", "relation_type": "same_function"}
                    ],
                }
            },
            CANONICAL,
            {"KD-001": "knife", "KD-002": "knife"},
        )


def test_shared_product_type_does_not_block_an_explicit_equivalent():
    """Runtime would derive same_function, so equivalent still adds information."""
    result = relate.normalize_relations(
        {
            "HL-009": {
                "pairs_with": [],
                "alternative_to": [
                    {"product_id": "HL-010", "relation_type": "equivalent"}
                ],
            }
        },
        CANONICAL,
        {"HL-009": "throw", "HL-010": "throw"},
    )
    assert result["HL-009"]["alternative_to"] == [
        {"product_id": "HL-010", "relation_type": "equivalent"}
    ]


# --------------------------------------------------------------------------
# Acceptance · facts the current catalog states unambiguously
# --------------------------------------------------------------------------


def test_catalog_declared_equivalent_is_preserved(entries):
    assert {
        "product_id": "HL-010",
        "relation_type": "equivalent",
    } in entries["HL-009"]["alternative_to"]


def test_catalog_declared_pairs_with_are_preserved(entries):
    assert "KD-001" in entries["KD-002"]["pairs_with"]
    assert "TG-006" in entries["TG-007"]["pairs_with"]
    assert "TG-012" in entries["TG-013"]["pairs_with"]
    assert "TG-019" in entries["TG-020"]["pairs_with"]
