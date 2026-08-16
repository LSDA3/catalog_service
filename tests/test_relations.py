"""Tests of the relations block: how they are built and how they are stored.

They cover the failure a real pipeline run produced — 56 relations, 89 products
participating and several `equivalent` where the catalog sustains one — and the
four rules that failure broke:

1. `pairs_with` is stored from the accessory towards the main product (A4.7) and
   is **never** reordered by `product_id`.
2. `alternative_to` is stored under the smaller `product_id`, with its
   `relation_type` (A4.8).
3. What the service derives at run time — same `product_type`, same
   `functional_family` (B0.5) — is **not** persisted. The rule is semantic, not
   a quota: a relation is written when the catalog sustains the link, and no
   number of them is right or wrong in itself.
4. An invalid answer from the model stops the build. It is not quietly turned
   into a valid one, because that would hide from `validate_semantic.py` exactly
   what it exists to catch.
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
    """KD-002 is the accessory of KD-001, so the edge lives under KD-002.

    This is the bug that produced the broken mesh: the previous version sorted
    the pair and stored it under the smaller identifier, which reverses the only
    thing this field says.
    """
    result = relate.normalize_relations(
        {"KD-002": {"pairs_with": ["KD-001"], "alternative_to": []}}, CANONICAL
    )
    assert result["KD-002"]["pairs_with"] == ["KD-001"]
    assert result["KD-001"]["pairs_with"] == []


def test_pairs_with_is_not_reordered_by_product_id():
    """The smaller identifier holding the edge is legitimate too, when it is the
    accessory. Neither direction is normalised away."""
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
    """Picking the safer label would be deciding the nature of the relation, and
    that is a reading of the catalog, not a shape."""
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
    """It used to become `same_function`, which is exactly what stopped the gate
    from ever seeing it."""
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
    """The build stops once, with the whole list, not one error per run."""
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
# B0.5 · what the service derives at run time is not persisted
# --------------------------------------------------------------------------


def test_no_persisted_same_function_joins_two_products_of_the_same_type(entries):
    """Level 2 of `get_related_products` already relates two products of the same
    `product_type`, and always as `same_function`. Persisting that pair adds
    nothing and displaces a better candidate, because an explicit relation wins
    over a derived one."""
    for product_id, entry in entries.items():
        for link in entry.get("alternative_to") or []:
            if link["relation_type"] != "same_function":
                continue
            other = link["product_id"]
            assert entries[product_id]["product_type"] != entries[other]["product_type"], (
                f"{product_id} · {other}: persisted as same_function while sharing "
                "product_type, which the service already derives"
            )


# --------------------------------------------------------------------------
# Acceptance · the mesh of the current catalog
#
# These numbers describe **this** catalog, and they are here to prove that the
# construction reproduces the artifact we validated. They are not a rule about
# how many relations a catalog may have: no such rule exists, and inventing one
# would be a quota, which is the opposite of what decides a relation — whether
# the catalog sustains it.
# --------------------------------------------------------------------------


def test_the_alternative_to_relations_are_the_nine_of_the_catalog(entries):
    persisted = [
        (product_id, link["product_id"], link["relation_type"])
        for product_id, entry in sorted(entries.items())
        for link in entry.get("alternative_to") or []
    ]
    assert persisted == [
        ("BS-001", "BS-002", "same_function"),
        ("BS-007", "BS-008", "same_function"),
        ("BW-009", "BW-010", "same_function"),
        ("HL-009", "HL-010", "equivalent"),
        ("HL-009", "HL-025", "same_function"),
        ("HL-019", "HL-020", "same_function"),
        ("KD-001", "KD-002", "same_function"),
        ("KD-011", "KD-012", "same_function"),
        ("TG-001", "TG-002", "same_function"),
    ]


def test_nine_relations_over_eight_products(entries):
    holders = {
        product_id
        for product_id, entry in entries.items()
        if entry.get("alternative_to")
    }
    relations = sum(len(entry.get("alternative_to") or []) for entry in entries.values())
    assert (relations, len(holders)) == (9, 8)


def test_only_hl_009_and_hl_010_are_equivalent(entries):
    equivalent = [
        (product_id, link["product_id"])
        for product_id, entry in sorted(entries.items())
        for link in entry.get("alternative_to") or []
        if link["relation_type"] == "equivalent"
    ]
    assert equivalent == [("HL-009", "HL-010")]


def test_the_other_eight_are_same_function(entries):
    same_function = [
        link
        for entry in entries.values()
        for link in entry.get("alternative_to") or []
        if link["relation_type"] == "same_function"
    ]
    assert len(same_function) == 8


def test_the_pairs_with_edges_are_the_twelve_of_the_catalog(entries):
    """Written from the accessory towards the main product, one by one.

    Every one of the twelve happens to live under the larger identifier, because
    in this catalog the accessories were added after the product they serve. That
    is why sorting the pair reversed all twelve at once.
    """
    written = {
        product_id: entry["pairs_with"]
        for product_id, entry in sorted(entries.items())
        if entry.get("pairs_with")
    }
    assert written == {
        "BS-003": ["BS-001"],
        "BS-006": ["BS-004"],
        "BW-010": ["BW-009"],
        "KD-002": ["KD-001"],
        "KD-003": ["KD-001", "KD-002"],
        "KD-006": ["KD-004", "KD-005"],
        "KD-022": ["KD-021"],
        "TG-007": ["TG-006"],
        "TG-013": ["TG-012"],
        "TG-020": ["TG-019"],
    }


def test_thirty_two_products_participate_counting_both_ends(loaded):
    products, _, _ = loaded
    participating = {p.product_id for p in products if p.pairs_with or p.alternative_to}
    assert len(participating) == 32
