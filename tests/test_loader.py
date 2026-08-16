"""Tests of `models.py` and `loader.py`, against the real data.

Cover test 1 of the Phase 3 gate — the load counts —, test 3 — a single
canonicalization — and the shape part of tests 16 and 17.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import loader  # noqa: E402
import models  # noqa: E402

CSV = ROOT / "data" / "catalog.csv"
VOCABULARIES = ROOT / "data" / "vocabularies.yaml"
SEMANTIC_LAYER = ROOT / "data" / "semantic_layer.json"


@pytest.fixture(scope="module")
def loaded():
    return loader.load(CSV, VOCABULARIES, SEMANTIC_LAYER)


# --------------------------------------------------------------------------
# Test 1 · load counts
# --------------------------------------------------------------------------


def test_the_150_products_are_loaded(loaded):
    products, _, _ = loaded
    assert len(products) == 150


def test_there_are_145_product_type(loaded):
    products, _, _ = loaded
    assert len({p.product_type for p in products}) == 145


def test_there_are_30_use_case(loaded):
    products, _, _ = loaded
    assert len({v for p in products for v in p.use_case}) == 30


def test_there_are_31_functional_family(loaded):
    products, _, _ = loaded
    assert len({v for p in products for v in p.functional_family}) == 31


def test_the_gift_risk_split_is_130_15_5(loaded):
    products, _, _ = loaded
    split_ = {value_: 0 for value_ in ("low", "taste_dependent", "high_commitment")}
    for product in products:
        split_[product.gift_risk] += 1
    assert split_ == {"low": 130, "taste_dependent": 15, "high_commitment": 5}


def test_there_are_5_stocking_filler(loaded):
    products, _, _ = loaded
    assert sum(1 for p in products if p.stocking_filler) == 5


# --------------------------------------------------------------------------
# Test 3 · a single canonicalization
# --------------------------------------------------------------------------


def test_catalog_and_semantic_layer_cover_the_same_set(loaded):
    products, _, _ = loaded
    layer = json.loads(SEMANTIC_LAYER.read_text(encoding="utf-8"))
    entries = layer["products"] if isinstance(layer, dict) and "products" in layer else layer
    keys_ = set(entries) if isinstance(entries, dict) else {e["product_id"] for e in entries}
    assert {p.product_id for p in products} == keys_


def test_no_product_is_left_unclassified(loaded):
    products, _, _ = loaded
    for product in products:
        assert product.product_type
        assert product.functional_family
        assert product.use_case
        assert product.gift_risk


def test_no_value_falls_outside_the_vocabulary(loaded):
    import yaml

    products, _, _ = loaded
    vocabulary = yaml.safe_load(VOCABULARIES.read_text(encoding="utf-8"))
    for field in ("product_type", "functional_family", "use_case", "gift_risk"):
        allowed = set(vocabulary[field])
        for product in products:
            value_ = getattr(product, field)
            values_ = value_ if isinstance(value_, list) else [value_]
            assert set(values_) <= allowed, (product.product_id, field, values_)


# --------------------------------------------------------------------------
# B4.3 · the shape of Product
# --------------------------------------------------------------------------


def test_product_has_exactly_26_fields():
    assert len(models.Product.model_fields) == 26
    assert tuple(models.Product.model_fields) == models.PRODUCT_FIELDS


@pytest.mark.parametrize("field", ["description_quality", "tags", "stock", "alt_product_ids"])
def test_the_fields_that_do_not_travel_are_not_in_product(field):
    assert field not in set(models.Product.model_fields)


def test_the_loader_does_keep_the_fields_that_do_not_travel(loaded):
    _, off_contract, _ = loaded
    assert len(off_contract) == 150
    for datos in off_contract.values():
        assert set(datos) == {"description_quality", "tags", "stock", "alt_product_ids"}


# --------------------------------------------------------------------------
# Relations · written once in the file, resolved from both ends
# --------------------------------------------------------------------------


def test_relations_are_resolved_from_both_ends(loaded):
    products, _, _ = loaded
    by_id = {p.product_id: p for p in products}
    for product in products:
        for other in product.pairs_with:
            assert product.product_id in by_id[other].pairs_with
        for other in product.alternative_to:
            assert product.product_id in by_id[other].alternative_to


def test_no_product_relates_to_itself(loaded):
    products, _, _ = loaded
    for product in products:
        assert product.product_id not in product.pairs_with
        assert product.product_id not in product.alternative_to


def test_relation_type_does_not_travel_in_product(loaded):
    products, _, relation_types = loaded
    assert "relation_type" not in set(models.Product.model_fields)
    assert relation_types
    for kind in relation_types.values():
        assert kind in {"equivalent", "same_function"}


def test_there_is_only_one_equivalent_relation(loaded):
    _, _, relation_types = loaded
    equivalent_pairs = {
        tuple(sorted(par)) for par, kind in relation_types.items() if kind == "equivalent"
    }
    assert equivalent_pairs == {("HL-009", "HL-010")}


# --------------------------------------------------------------------------
# B4.8 · metadata belongs to each operation, it is not common
# --------------------------------------------------------------------------


def test_each_operation_declares_its_metadata():
    assert models.METADATA_BY_OPERATION == {
        "get_categories": (),
        "get_products_by_category": ("total", "offset"),
        "find_products_by_criteria": ("query_understood", "excluded", "not_applied"),
        "get_related_products": ("relation_type", "query_understood", "excluded"),
        "get_product_details": (),
    }


def test_not_applied_only_exists_in_the_search():
    with_not_applied = {
        operation
        for operation, metadata in models.METADATA_BY_OPERATION.items()
        if "not_applied" in metadata
    }
    assert with_not_applied == {"find_products_by_criteria"}


def test_excluded_only_exists_in_search_and_related():
    with_excluded = {
        operation
        for operation, metadata in models.METADATA_BY_OPERATION.items()
        if "excluded" in metadata
    }
    assert with_excluded == {"find_products_by_criteria", "get_related_products"}


def test_total_and_offset_only_exist_in_the_navigation():
    with_pagination = {
        operation
        for operation, metadata in models.METADATA_BY_OPERATION.items()
        if "total" in metadata or "offset" in metadata
    }
    assert with_pagination == {"get_products_by_category"}


def test_no_operation_goes_above_eight_products():
    for minimum, maximum, default_ in models.LIMITS_BY_OPERATION.values():
        assert 1 <= minimum <= default_ <= maximum <= models.ABSOLUTE_MAXIMUM


def test_related_returns_three_by_default():
    assert models.LIMITS_BY_OPERATION["get_related_products"] == (1, 5, 3)


def test_excluded_and_not_applied_are_omitted_when_empty():
    response = models.FindProductsByCriteriaResponse(results=[], query_understood={})
    assert response.excluded is None
    assert response.not_applied is None


# --------------------------------------------------------------------------
# The gate of A3.4, seen from start-up
# --------------------------------------------------------------------------


def test_an_incomplete_semantic_layer_stops_the_start_up(tmp_path):
    layer = json.loads(SEMANTIC_LAYER.read_text(encoding="utf-8"))
    entries = layer["products"] if isinstance(layer, dict) and "products" in layer else layer
    if isinstance(entries, dict):
        entries.pop(next(iter(entries)))
    else:
        entries.pop(0)
    trimmed = tmp_path / "semantic_layer.json"
    trimmed.write_text(json.dumps(layer), encoding="utf-8")

    with pytest.raises(loader.IncompleteSemanticLayer):
        loader.load(CSV, VOCABULARIES, trimmed)
