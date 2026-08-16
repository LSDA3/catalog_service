"""Tests of the boundaries, the precedence and the related products.

Here the six scenarios of A8.7 land with their declared counts — 34, 0, 97 and
132 — together with the cases that get implemented wrong when they are not
checked: `universal`, the missing values of `rating`, `gift_risk` modulated by
`buyer_knows_recipient`, and the three-level cascade of `alternative_to`.
"""

from __future__ import annotations

import asyncio
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
# The twelve boundaries
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

    without_criteria = selection.take_what_qualifies(
        catalog.all_products(), {}, gender_specific_types
    )
    with_her = selection.take_what_qualifies(
        catalog.all_products(), {"recipient": "her"}, gender_specific_types
    )
    assert {p.product_id for p in with_her} == {
        p.product_id
        for p in without_criteria
        if p.product_type not in gender_specific_types or "her" in p.recipient
    }

    # `recipient` itself only cuts for kids. For adults, the only hard exclusion
    # is the separate `gender_specific` rule above. Every product opened to
    # `anyone` therefore matches every adult recipient at precedence level 4.
    for requested in ("her", "him", "couple"):
        for product in without_criteria:
            if "anyone" in product.recipient:
                assert selection.precedence_key(product, {"recipient": requested})[3] == 0

    for product in without_criteria:
        if "anyone" in product.recipient and "kids" not in product.recipient:
            assert selection.precedence_key(product, {"recipient": "kids"})[3] == 1


# --------------------------------------------------------------------------
# The exact-match restriction
# --------------------------------------------------------------------------


def test_product_type_restricts_and_admits_no_other_object(catalog):
    universe = selection.restrict_to_exact_match(catalog.all_products(), "chef_knife")
    assert universe
    assert all(p.product_type == "chef_knife" for p in universe)


def test_without_product_type_nothing_is_restricted(catalog):
    assert len(selection.restrict_to_exact_match(catalog.all_products(), None)) == 150


# --------------------------------------------------------------------------
# A8.7 · the six scenarios
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
    # The query carries no `use_case`, and there `universal` goes first.
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
    # The out-of-stock console does not show up.
    assert all("Console" not in p.name for p in ordered)


# --------------------------------------------------------------------------
# The order by precedence · the cases that get implemented wrong
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
    """And only among those that arrive tied at that level.

    A product without a rating that already won at an earlier level stays ahead:
    this level only intervenes when the comparison has reached it.
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
            continue  # an earlier level separated them
        if previous.rating is None and next_.rating is not None:
            raise AssertionError(
                f"{previous.product_id} without a rating goes before {next_.product_id}"
            )
        compared += 1
    assert compared and keys_


def test_a_missing_rating_is_not_compared_as_zero(catalog):
    without_rating = next(p for p in catalog.all_products() if p.rating is None)
    key_ = selection.precedence_key(without_rating, {})
    level_six = key_[5]
    assert level_six[0] == 1  # unknown, behind the known one
    assert level_six[1] == 0.0  # and not a rating of zero


def test_the_rating_rules_and_reviews_only_break_ties(catalog, gender_specific_types):
    better_rating = next(p for p in catalog.all_products() if p.product_id == "GP-005")  # 4.8 · 163
    more_reviews = next(p for p in catalog.all_products() if p.product_id == "GP-001")  # 4.7 · 588
    assert selection._level_six(better_rating) < selection._level_six(more_reviews)

    # Category browsing with its default `rating` order starts at this level. It
    # does not run the full gift-recommendation precedence, where `universal` and
    # the other semantic criteria belong. Check every category so this test does
    # not pass merely because one shelf happens to have the same first page under
    # the two different ordering rules.
    import api

    for category in api.CATEGORIES:
        of_the_category = [
            p for p in catalog.all_products() if p.category == category
        ]
        inside = selection.take_what_qualifies(
            of_the_category, {}, gender_specific_types, require_standalone_gift=False
        )
        ordered = sorted(inside, key=lambda p: (selection._level_six(p), p.product_id))
        response = asyncio.run(api.get_products_by_category(category))
        assert [p.product_id for p in response.results] == [
            p.product_id for p in ordered[:8]
        ]


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
    
    nombres = set(Product.model_fields)
    for sospechoso in ("score", "product_score", "weight", "similarity", "rank", "position"):
        assert sospechoso not in nombres


# --------------------------------------------------------------------------
# Related products · the three levels
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
    """The sharpening stone arrives through `pairs_with`, and is no gift on its own."""
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

    # With no product anchor there is still no generic fourth level. A concrete
    # type can yield that type and its family; a family can yield that family.
    # Neither path is allowed to fill the limit with unrelated products.
    chosen = selection.related_products(
        catalog.all_products(),
        "alternative_to",
        None,
        {"product_type": "gift_card"},
        5,
        gender_specific_types,
        quality,
    )
    same_type = [p for p in catalog.all_products() if p.product_type == "gift_card"]
    family = {value for p in same_type for value in p.functional_family}
    assert chosen
    assert all(
        p.product_type == "gift_card" or set(p.functional_family) & family for p in chosen
    )

    chosen = selection.related_products(
        catalog.all_products(),
        "alternative_to",
        None,
        {"functional_family": ["gift_card"]},
        5,
        gender_specific_types,
        quality,
    )
    assert chosen
    assert all("gift_card" in p.functional_family for p in chosen)
