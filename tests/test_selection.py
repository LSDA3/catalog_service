"""Pruebas de las fronteras, la precedencia y los related_products.

Aquí caen los seis escenarios de A8.7 con sus recuentos declarados —34, 0, 97 y
132— y los casos que se implementan mal si no se comprueban: `universal`, los
ausentes de `rating`, `gift_risk` modulado, y la cascada de tres levels de
`alternative_to`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import normalization  # noqa: E402
import selection  # noqa: E402
from models import Product  # noqa: E402
from repository import InMemoryCatalog  # noqa: E402

CSV = ROOT / "data" / "catalog.csv"
VOCABULARIES = ROOT / "data" / "vocabularies.yaml"
SEMANTIC_LAYER = ROOT / "data" / "semantic_layer.json"


@pytest.fixture(scope="module")
def catalog():
    return InMemoryCatalog(CSV, VOCABULARIES, SEMANTIC_LAYER)


@pytest.fixture(scope="module")
def gender_specific_types():
    return normalization.gender_specific_product_types(VOCABULARIES)


@pytest.fixture(scope="module")
def quality(catalog):
    return {
        p.product_id: catalog.off_contract(p.product_id)["description_quality"]
        for p in catalog.all_products()
    }


def search(catalog, gender_specific_types, quality, criteria, product_type=None):
    base = selection.restrict_to_exact_match(catalog.all_products(), product_type)
    inside = selection.take_what_qualifies(base, criteria, gender_specific_types)
    return inside, selection.order_by_precedence(inside, criteria, quality)


# --------------------------------------------------------------------------
# Las doce fronteras
# --------------------------------------------------------------------------


def test_the_two_invariant_cuts_leave_132(catalog, gender_specific_types):
    inside = selection.take_what_qualifies(catalog.all_products(), {}, gender_specific_types)
    assert len(inside) == 132


def test_nothing_out_of_stock_ever_gets_in(catalog, gender_specific_types):
    inside = selection.take_what_qualifies(catalog.all_products(), {}, gender_specific_types)
    assert all(p.in_stock for p in inside)


def test_gift_wrap_leaves_127_available(catalog, gender_specific_types):
    with_gift_wrap = [p for p in catalog.all_products() if p.gift_wrap is True]
    assert len(with_gift_wrap) == 137
    assert sum(1 for p in with_gift_wrap if p.in_stock) == 127


def test_max_shipping_days_of_three_leaves_98_available(catalog):
    available = [p for p in catalog.all_products() if p.in_stock]
    assert len(available) == 139
    in_time = [
        p for p in available if p.shipping_days is not None and p.shipping_days <= 3
    ]
    assert len(in_time) == 98


def test_target_price_opens_a_band_of_plus_minus_20(catalog, gender_specific_types):
    inside = selection.take_what_qualifies(catalog.all_products(), {"target_price": 50}, gender_specific_types)
    assert all(40 <= p.price <= 60 for p in inside)


def test_recipient_kids_is_the_only_recipient_value_that_cuts(catalog, gender_specific_types):
    inside = selection.take_what_qualifies(
        catalog.all_products(), {"recipient": "kids"}, gender_specific_types
    )
    assert all("kids" in p.recipient for p in inside)

    with_her = selection.take_what_qualifies(catalog.all_products(), {"recipient": "her"}, gender_specific_types)
    without_criteria = selection.take_what_qualifies(catalog.all_products(), {}, gender_specific_types)
    assert len(with_her) == len(without_criteria)


# --------------------------------------------------------------------------
# La restricción de coincidencia exacta
# --------------------------------------------------------------------------


def test_product_type_restricts_and_admits_no_other_object(catalog):
    universe = selection.restrict_to_exact_match(catalog.all_products(), "chef_knife")
    assert universe
    assert all(p.product_type == "chef_knife" for p in universe)


def test_without_product_type_nothing_is_restricted(catalog):
    assert len(selection.restrict_to_exact_match(catalog.all_products(), None)) == 150


# --------------------------------------------------------------------------
# A8.7 · los seis escenarios
# --------------------------------------------------------------------------


def test_scenario_1_the_sister(catalog, gender_specific_types, quality):
    criteria = {
        "target_price": 50,
        "occasion": "birthday",
        "recipient": "her",
        "relationship": "family",
        "max_shipping_days": 7,
    }
    inside, ordered = search(catalog, gender_specific_types, quality, criteria)
    assert len(inside) == 34
    # La consulta no lleva `use_case`, y ahí `universal` va delante.
    assert ordered[0].product_id == "EX-001"


def test_scenario_2_the_knife(catalog, gender_specific_types, quality):
    criteria = {"max_price": 100, "max_shipping_days": 7}
    inside, ordered = search(catalog, gender_specific_types, quality, criteria, product_type="chef_knife")
    assert inside == []

    universe = selection.restrict_to_exact_match(catalog.all_products(), "chef_knife")
    excluidos = selection.above_budget(universe, criteria, gender_specific_types, quality)
    assert len(excluidos) == 1
    assert excluidos[0].product_id == "KD-001"
    assert excluidos[0].exclusion_reason == "over_budget"
    assert excluidos[0].actual == 149.00
    assert excluidos[0].required == 100


def test_scenario_3_the_throw(catalog, gender_specific_types, quality):
    criteria = {
        "max_price": 100,
        "max_shipping_days": 7,
        "functional_family": ["soft_furnishing"],
    }
    inside, ordered = search(catalog, gender_specific_types, quality, criteria)
    assert len(inside) == 97
    assert ordered[0].product_id == "HL-010"


def test_scenario_5_something_retro(catalog, gender_specific_types, quality):
    criteria = {"use_case": ["tabletop_gaming"]}
    inside, ordered = search(catalog, gender_specific_types, quality, criteria)
    assert len(inside) == 132
    assert [p.product_id for p in ordered[:4]] == ["GP-005", "GP-001", "GP-009", "GP-003"]
    # La consola agotada no aparece.
    assert all("Console" not in p.name for p in ordered)


# --------------------------------------------------------------------------
# El ordered por precedencia · los casos que se implementan mal
# --------------------------------------------------------------------------


def test_universal_goes_first_when_there_is_no_use_case(catalog, gender_specific_types, quality):
    criteria = {"target_price": 50, "occasion": "birthday", "recipient": "her"}
    _, ordered = search(catalog, gender_specific_types, quality, criteria)
    assert "universal" in ordered[0].use_case


def test_universal_does_not_match_a_concrete_situation(catalog, gender_specific_types, quality):
    criteria = {"use_case": ["cooking"]}
    _, ordered = search(catalog, gender_specific_types, quality, criteria)
    universal_position = next(
        i for i, p in enumerate(ordered) if "universal" in p.use_case
    )
    cooking_ones = [i for i, p in enumerate(ordered) if "cooking" in p.use_case]
    neither_one = [
        i
        for i, p in enumerate(ordered)
        if "cooking" not in p.use_case and "universal" not in p.use_case
    ]
    assert max(cooking_ones) < universal_position
    assert universal_position < min(neither_one)


def test_a_known_rating_goes_before_a_missing_one(catalog, gender_specific_types, quality):
    """Y solo entre los que llegan empatados a ese level.

    Un product sin rating_value que ya ganó en un level previous sigue delante: este
    level solo interviene cuando la comparación ha llegado hasta aquí.
    """
    _, ordered = search(catalog, gender_specific_types, quality, {})
    keys_ = [selection.precedence_key(p, {}, quality[p.product_id]) for p in ordered]
    compared = 0
    for previous, next_ in zip(ordered, ordered[1:]):
        previous_key = selection.precedence_key(
            previous, {}, quality[previous.product_id]
        )
        next_key = selection.precedence_key(
            next_, {}, quality[next_.product_id]
        )
        if previous_key[:5] != next_key[:5]:
            continue  # los separó un level previous
        if previous.rating is None and next_.rating is not None:
            raise AssertionError(
                f"{previous.product_id} sin rating_value va delante de {next_.product_id}"
            )
        compared += 1
    assert compared and keys_


def test_a_missing_rating_is_not_compared_as_zero(catalog):
    without_rating = next(p for p in catalog.all_products() if p.rating is None)
    key_ = selection.precedence_key(without_rating, {})
    nivel_seis = key_[5]
    assert nivel_seis[0] == 1  # desconocido, detrás del conocido
    assert nivel_seis[1] == 0.0  # y no una rating_value de cero


def test_the_rating_rules_and_reviews_only_break_ties(catalog):
    better_rating = next(p for p in catalog.all_products() if p.product_id == "GP-005")  # 4.8 · 163
    more_reviews = next(p for p in catalog.all_products() if p.product_id == "GP-001")  # 4.7 · 588
    assert selection._level_six(better_rating) < selection._level_six(more_reviews)


def test_buyer_knows_recipient_skips_the_gift_risk_level(catalog):
    risky = next(p for p in catalog.all_products() if p.gift_risk == "high_commitment")
    with_caution = selection.precedence_key(risky, {})
    without_caution = selection.precedence_key(
        risky, {"buyer_knows_recipient": True}
    )
    assert with_caution[6] == 2
    assert without_caution[6] == 0


def test_absent_and_false_order_the_same_in_gift_risk(catalog):
    risky = next(p for p in catalog.all_products() if p.gift_risk == "high_commitment")
    absent = selection.precedence_key(risky, {})
    declared = selection.precedence_key(
        risky, {"buyer_knows_recipient": False}
    )
    assert absent == declared


def test_matching_two_values_does_not_beat_matching_one(catalog):
    products = catalog.all_products()
    one_ = next(p for p in products if "tabletop_gaming" in p.use_case)
    criteria = {"use_case": ["tabletop_gaming", "cooking"]}
    key_ = selection.precedence_key(one_, criteria)
    assert key_[0] == (-1, 0)


def test_the_final_tie_is_stabilised_with_product_id(catalog, gender_specific_types, quality):
    _, first_ = search(catalog, gender_specific_types, quality, {})
    _, second_ = search(catalog, gender_specific_types, quality, {})
    assert [p.product_id for p in first_] == [p.product_id for p in second_]


def test_no_response_carries_a_score(catalog):
    from dataclasses import fields

    nombres = {f.name for f in fields(Product)}
    for sospechoso in ("score", "product_score", "weight", "similarity", "rank", "position"):
        assert sospechoso not in nombres


# --------------------------------------------------------------------------
# Relacionados · los tres levels
# --------------------------------------------------------------------------


def test_a_lower_level_never_overtakes_a_higher_one(catalog, gender_specific_types, quality):
    anchor = next(p for p in catalog.all_products() if p.alternative_to)
    chosen = selection.related_products(
        catalog.all_products(), "alternative_to", anchor, {}, 5, gender_specific_types, quality
    )
    explicit_ones = [p for p in chosen if p.product_id in anchor.alternative_to]
    others = [p for p in chosen if p.product_id not in anchor.alternative_to]
    if explicit_ones and others:
        assert chosen.index(explicit_ones[-1]) < chosen.index(others[0])


def test_the_anchor_is_never_returned_to_itself(catalog, gender_specific_types, quality):
    for relation in ("alternative_to", "pairs_with"):
        for anchor in catalog.all_products()[:40]:
            chosen = selection.related_products(
                catalog.all_products(), relation, anchor, {}, 5, gender_specific_types, quality
            )
            assert anchor.product_id not in {p.product_id for p in chosen}


def test_pairs_with_has_no_three_levels(catalog, gender_specific_types, quality):
    anchor = next(p for p in catalog.all_products() if p.pairs_with)
    chosen = selection.related_products(
        catalog.all_products(), "pairs_with", anchor, {}, 5, gender_specific_types, quality
    )
    assert chosen
    assert all(p.product_id in anchor.pairs_with for p in chosen)


def test_the_complement_need_not_stand_alone_as_a_gift(
    catalog, gender_specific_types, quality
):
    """La piedra de afilar llega por `pairs_with`, y no es un regalo por sí sola."""
    knife = catalog.by_id("KD-001")
    chosen = selection.related_products(
        catalog.all_products(), "pairs_with", knife, {}, 5, gender_specific_types, quality
    )
    assert "KD-003" in {p.product_id for p in chosen}
    assert any(not p.is_standalone_gift for p in chosen)


def test_but_when_recommending_it_must_stand_alone(catalog, gender_specific_types):
    inside = selection.take_what_qualifies(catalog.all_products(), {}, gender_specific_types)
    assert all(p.is_standalone_gift for p in inside)


def test_in_stock_has_no_exception_not_even_for_complements(
    catalog, gender_specific_types, quality
):
    for anchor in catalog.all_products():
        chosen = selection.related_products(
            catalog.all_products(), "pairs_with", anchor, {}, 5, gender_specific_types, quality
        )
        assert all(p.in_stock for p in chosen)


def test_pairs_with_without_anchor_returns_nothing(catalog, gender_specific_types, quality):
    assert (
        selection.related_products(catalog.all_products(), "pairs_with", None, {}, 5, gender_specific_types, quality)
        == []
    )


def test_related_products_respect_the_boundaries(catalog, gender_specific_types, quality):
    anchor = next(p for p in catalog.all_products() if p.alternative_to)
    chosen = selection.related_products(
        catalog.all_products(), "alternative_to", anchor, {"max_price": 50}, 5, gender_specific_types, quality
    )
    assert all(p.price is None or p.price <= 50 for p in chosen)


def test_related_products_do_not_exceed_the_limit(catalog, gender_specific_types, quality):
    anchor = next(p for p in catalog.all_products() if p.functional_family)
    for limit_ in (1, 3, 5):
        chosen = selection.related_products(
            catalog.all_products(), "alternative_to", anchor, {}, limit_, gender_specific_types, quality
        )
        assert len(chosen) <= limit_
