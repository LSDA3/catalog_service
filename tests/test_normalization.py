"""Tests of the canonicalization, against the real catalog.

Cover the part of the Phase 3 gate that depends only on `normalization.py`: test
1 as far as the load counts go, test 2 in full, and test 6 as far as not
inventing missing values goes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import normalization as n  # noqa: E402

CSV = ROOT / "data" / "catalog.csv"
VOCABULARIES = ROOT / "data" / "vocabularies.yaml"
SEMANTIC_LAYER = ROOT / "data" / "semantic_layer.json"


@pytest.fixture(scope="module")
def tipos() -> dict[str, str]:
    capa = json.loads(SEMANTIC_LAYER.read_text(encoding="utf-8"))
    products = capa["products"] if isinstance(capa, dict) and "products" in capa else capa
    if isinstance(products, list):
        return {p["product_id"]: p["product_type"] for p in products}
    return {key_: value_["product_type"] for key_, value_ in products.items()}


@pytest.fixture(scope="module")
def catalog(tipos):
    return n.canonicalize(CSV, VOCABULARIES, tipos)


# --------------------------------------------------------------------------
# Test 2 · canonicalization
# --------------------------------------------------------------------------


def test_the_152_rows_give_150_products(catalog):
    canonicos, _ = catalog
    assert len(canonicos) == 150


def test_the_two_merges_are_the_written_ones(catalog):
    canonicos, _ = catalog
    fusionados = {p.product_id: p.alt_product_ids for p in canonicos if p.alt_product_ids}
    assert fusionados == {"HL-021": ["KD-024"], "HL-024": ["KD-023"]}


def test_the_canonical_id_is_the_lexicographically_smaller_one(catalog):
    canonicos, _ = catalog
    for product in canonicos:
        for absorbido in product.alt_product_ids:
            assert product.product_id < absorbido


def test_the_category_of_the_absorbed_one_becomes_secondary(catalog):
    canonicos, _ = catalog
    by_id = {p.product_id: p for p in canonicos}
    assert by_id["HL-021"].secondary_categories == ["Kitchen & Dining"]
    assert by_id["HL-024"].secondary_categories == ["Kitchen & Dining"]


def test_an_absorbed_identifier_resolves_to_the_canonical_one(catalog):
    canonicos, _ = catalog
    assert n.resolve_identifier("KD-024", canonicos).product_id == "HL-021"
    assert n.resolve_identifier("KD-023", canonicos).product_id == "HL-024"


def test_an_unknown_identifier_does_not_resolve(catalog):
    canonicos, _ = catalog
    assert n.resolve_identifier("KD-999", canonicos) is None


# --------------------------------------------------------------------------
# Test 1 · load counts
# --------------------------------------------------------------------------


def test_there_are_139_available(catalog):
    canonicos, _ = catalog
    assert sum(1 for p in canonicos if p.in_stock) == 139


def test_the_17_category_values_give_11_categories(catalog):
    canonicos, _ = catalog
    assert len({p.category for p in canonicos}) == 11


def test_category_normalization_treats_and_as_ampersand():
    assert n.normalize_category(" Home & Living") == "Home & Living"
    assert n.normalize_category("Home and Living") == "Home & Living"
    assert n.normalize_category("home & living") == "Home & Living"
    assert n.normalize_category("Tech and Gadgets") == "Tech & Gadgets"


# --------------------------------------------------------------------------
# Opening up recipient
# --------------------------------------------------------------------------


def test_140_products_carry_anyone(catalog):
    canonicos, _ = catalog
    assert sum(1 for p in canonicos if "anyone" in p.recipient) == 140


def test_the_ten_exclusive_ones_are_the_written_ones(catalog):
    canonicos, _ = catalog
    without_anyone = sorted(p.product_id for p in canonicos if "anyone" not in p.recipient)
    assert without_anyone == [
        "BW-004",
        "BW-006",
        "JW-003",
        "JW-004",
        "KI-001",
        "KI-002",
        "KI-003",
        "KI-004",
        "KI-005",
        "KI-006",
    ]


def test_the_original_value_is_kept(catalog):
    canonicos, _ = catalog
    by_id = {p.product_id: p for p in canonicos}
    teclado = by_id["TG-012"]
    assert "anyone" in teclado.recipient
    assert "him" in teclado.recipient


def test_kids_never_carries_anyone(catalog):
    canonicos, _ = catalog
    for product in canonicos:
        if "kids" in product.recipient:
            assert product.recipient == ["kids"]


# --------------------------------------------------------------------------
# Test 6 · missing values are not invented
# --------------------------------------------------------------------------


def test_the_five_missing_ratings_stay_missing(catalog):
    canonicos, _ = catalog
    without_rating = [p for p in canonicos if p.rating is None]
    assert len(without_rating) == 5
    assert all(p.rating != 0 for p in without_rating)


def test_the_three_missing_occasions_stay_empty(catalog):
    canonicos, _ = catalog
    assert sum(1 for p in canonicos if not p.occasion) == 3


def test_the_two_poor_descriptions_are_the_written_ones(catalog):
    canonicos, _ = catalog
    poor_ones = sorted(p.product_id for p in canonicos if p.description_quality == "poor")
    assert poor_ones == ["BS-015", "HL-013"]


# --------------------------------------------------------------------------
# Format normalization · the table of A2.2
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entra, sale",
    [
        ("49", 49.00),
        ("€49.95", 49.95),
        ("49.95 €", 49.95),
        ("EUR 49.95", 49.95),
        ("49,95", 49.95),
        ("1.234,56", 1234.56),
    ],
)
def test_the_price_normalizes_what_is_not_ambiguous(entra, sale):
    assert n.normalize_price(entra, 2) == sale


def test_an_empty_price_leaves_the_product_without_price():
    assert n.normalize_price("", 2) is None


@pytest.mark.parametrize("entra", ["1,234", "-5", "0"])
def test_the_price_stops_at_the_impossible_or_the_ambiguous(entra):
    with pytest.raises(n.AmbiguousCatalog):
        n.normalize_price(entra, 2)


@pytest.mark.parametrize(
    "entra, cantidad, disponible",
    [("7", 7, True), ("0", 0, False), ("yes", None, True), ("Y", None, True)],
)
def test_stock_separates_quantity_from_availability(entra, cantidad, disponible):
    assert n.normalize_stock(entra, 2) == (cantidad, disponible)


def test_unreadable_stock_stops_the_start_up():
    with pytest.raises(n.AmbiguousCatalog):
        n.normalize_stock("bastantes", 2)


# --------------------------------------------------------------------------
# Determinism · what holds up the coverage gate
# --------------------------------------------------------------------------


def test_two_runs_give_the_same_set_of_identifiers(tipos):
    first_, _ = n.canonicalize(CSV, VOCABULARIES, tipos)
    second_, _ = n.canonicalize(CSV, VOCABULARIES, tipos)
    assert [p.product_id for p in first_] == [p.product_id for p in second_]


def test_the_canonical_set_matches_the_semantic_layer(catalog, tipos):
    canonicos, _ = catalog
    assert {p.product_id for p in canonicos} == set(tipos)
