"""Which products get in, and in which order they come out.

Three separate mechanics, and they never mix:

1. **The exact-match restriction** (B2.6). When the customer asked for a concrete
   object and it resolved, `product_type` defines which products literally
   satisfy the request. It acts **before** the boundaries. It is not a cut and it
   does not order: it identifies.
2. **The twelve boundaries** (B2.7). The products that meet them are taken.
3. **The order by precedence** (B2.8). Eight levels, compared one after another:
   if a level separates, that settles it; if not, the next level is read.

**No derived numeric value is computed here.** Precedence is assigned to the
criterion, not to the product: no product accumulates anything, there is no
score, no weights and no percentages. The ordering key this module builds is a
lexicographic comparison — its positions are read in sequence and the first one
that differs decides — and not a quantity to be summed or compared as magnitude.
"""

from __future__ import annotations

from models import ExcludedProduct, Product

# --------------------------------------------------------------------------
# 1 · Exact-match restriction (B2.6)
# --------------------------------------------------------------------------


def restrict_to_exact_match(
    products: list[Product], product_type: str | None
) -> list[Product]:
    """The set of products that are the requested object.

    A `paring_knife` is not a worse chef's knife: it is another object. That is
    why it does not enter the set and does not show up in `excluded` either.
    """
    if not product_type:
        return list(products)
    return [product for product in products if product.product_type == product_type]


# --------------------------------------------------------------------------
# 2 · The twelve boundaries (B2.7)
# --------------------------------------------------------------------------

TARGET_PRICE_BAND = 0.20


def _price_qualifies(product: Product, criteria: dict) -> bool:
    if product.price is None:
        return not any(key in criteria for key in ("max_price", "min_price", "target_price"))
    if "max_price" in criteria and product.price > criteria["max_price"]:
        return False
    if "min_price" in criteria and product.price < criteria["min_price"]:
        return False
    if "target_price" in criteria:
        centre = criteria["target_price"]
        if not (
            centre * (1 - TARGET_PRICE_BAND) <= product.price <= centre * (1 + TARGET_PRICE_BAND)
        ):
            return False
    return True


def take_what_qualifies(
    products: list[Product],
    criteria: dict,
    gender_specific_types: set[str] | None = None,
    require_standalone_gift: bool = True,
) -> list[Product]:
    """The products that meet the twelve boundaries.

    Two are invariants of the service and do not depend on what the customer
    says: `in_stock` and `is_standalone_gift`. The other ten act only when the
    customer has declared them.

    `stocking_filler: true` **switches on the budget-filling mechanic**: only the
    marked products are taken. Absent and `false` do not cut — they are three
    distinct states.

    `is_standalone_gift` cuts **when recommending**, which is why it does so by
    default. It **does not cut with `relation=pairs_with`**, the path by which an
    accessory or a refill legitimately arrives as a complement: there, a product
    that does not stand on its own as a gift is exactly what is being looked for.
    `in_stock` has no exception and cuts everywhere in the service.
    """
    gender_specific_types = gender_specific_types or set()
    inside: list[Product] = []

    for product in products:
        if not product.in_stock:
            continue
        if require_standalone_gift and not product.is_standalone_gift:
            continue
        if not _price_qualifies(product, criteria):
            continue
        if "max_shipping_days" in criteria:
            if product.shipping_days is None:
                continue
            if product.shipping_days > criteria["max_shipping_days"]:
                continue
        if criteria.get("gift_wrap_required") is True and product.gift_wrap is not True:
            continue
        for field in ("brand", "color", "material"):
            if field in criteria and getattr(product, field) != criteria[field]:
                break
        else:
            if criteria.get("stocking_filler") is True and not product.stocking_filler:
                continue
            requested = criteria.get("recipient")
            if requested == "kids" and "kids" not in product.recipient:
                continue
            if (
                requested in {"her", "him", "couple"}
                and product.product_type in gender_specific_types
                and requested not in product.recipient
            ):
                continue
            inside.append(product)

    return inside


# --------------------------------------------------------------------------
# 3 · The order by precedence (B2.8)
# --------------------------------------------------------------------------

GIFT_RISK_ORDER = {"low": 0, "taste_dependent": 1, "high_commitment": 2}
DESCRIPTION_QUALITY_ORDER = {"ok": 0, "poor": 1}


def _matches(product_values: list[str], requested) -> bool:
    """There is a match when there is an intersection.

    Matching two values instead of one puts nobody ahead: the values of the query
    are pertinent alternatives, not accumulable points.
    """
    if requested is None:
        return False
    requested_values = requested if isinstance(requested, (list, tuple, set)) else [requested]
    return bool(set(product_values) & set(requested_values))


def _level_one(product: Product, criteria: dict) -> tuple[int, int]:
    """`functional_family` + `use_case`, with the own precedence of `universal`.

    The level is settled first by how many of its two dimensions the product
    satisfies. `universal` breaks ties **within the same count**, never above it.
    """
    requested_family = criteria.get("functional_family")
    requested_situation = criteria.get("use_case")

    satisfied_dimensions = 0
    if requested_family and _matches(product.functional_family, requested_family):
        satisfied_dimensions += 1
    if requested_situation and _matches(product.use_case, requested_situation):
        satisfied_dimensions += 1

    if requested_situation:
        if _matches(product.use_case, requested_situation):
            universal_rank = 0
        elif "universal" in product.use_case:
            universal_rank = 1
        else:
            universal_rank = 2
    else:
        universal_rank = 0 if "universal" in product.use_case else 1

    return (-satisfied_dimensions, universal_rank)


def _level_six(product: Product) -> tuple[int, float, int, int]:
    """`rating` + `reviews_count`, in cascade and never combined in a formula.

    Known before unknown; among known values, descending. `null` is not replaced
    by zero, not compared as zero and not written as zero: what is compared is
    whether the datum exists.
    """
    rating_known = 0 if product.rating is not None else 1
    rating_value = -product.rating if product.rating is not None else 0.0
    reviews_known = 0 if product.reviews_count is not None else 1
    reviews_value = -product.reviews_count if product.reviews_count is not None else 0
    return (rating_known, rating_value, reviews_known, reviews_value)


def precedence_key(
    product: Product, criteria: dict, description_quality: str = "ok"
) -> tuple:
    """The position of the product along the chain, level by level.

    It is a comparison key, not a score: each position corresponds to a level of
    B2.8 and is read in sequence. The first one that differs decides, and the
    following ones cannot compensate for it.
    """
    level_1 = _level_one(product, criteria)
    level_2 = 0 if _matches(product.occasion, criteria.get("occasion")) else 1

    shelf_matches = 0
    if criteria.get("category") and product.category == criteria["category"]:
        shelf_matches += 1
    if criteria.get("subcategory") and product.subcategory == criteria["subcategory"]:
        shelf_matches += 1
    level_3 = -shelf_matches

    requested_recipient = criteria.get("recipient")
    if requested_recipient == "kids":
        level_4 = 0 if "kids" in product.recipient else 1
    elif requested_recipient:
        level_4 = (
            0
            if requested_recipient in product.recipient or "anyone" in product.recipient
            else 1
        )
    else:
        level_4 = 1

    requested_relationship = criteria.get("relationship")
    level_5 = (
        0
        if (requested_relationship and requested_relationship in product.suitable_relationships)
        else 1
    )

    level_6 = _level_six(product)

    if criteria.get("buyer_knows_recipient") is True:
        level_7 = 0  # the level is skipped and the comparison continues below
    else:
        level_7 = GIFT_RISK_ORDER.get(product.gift_risk, 0)

    level_8 = DESCRIPTION_QUALITY_ORDER.get(description_quality, 0)

    return (
        level_1,
        level_2,
        level_3,
        level_4,
        level_5,
        level_6,
        level_7,
        level_8,
        product.product_id,
    )


def order_by_precedence(
    products: list[Product],
    criteria: dict,
    quality_by_product: dict[str, str] | None = None,
) -> list[Product]:
    """Order the valid set by walking the chain from top to bottom.

    A tie that survives the eight levels is irrelevant to the recommendation: it
    is stabilised with `product_id` so the output is reproducible, and that is
    all it means. Never with the price.
    """
    quality_by_product = quality_by_product or {}
    return sorted(
        products,
        key=lambda product: precedence_key(
            product, criteria, quality_by_product.get(product.product_id, "ok")
        ),
    )


# --------------------------------------------------------------------------
# The `excluded` channel (B1.6)
# --------------------------------------------------------------------------

EXCLUDED_CAP = 2


def above_budget(
    products: list[Product],
    criteria: dict,
    gender_specific_types: set[str] | None = None,
    quality_by_product: dict[str, str] | None = None,
) -> list[ExcludedProduct]:
    """Up to two relevant candidates the price boundary left out.

    They are chosen **by the order of precedence, not by being the cheapest**:
    choosing by price produces absurd answers. And they meet everything else —
    only the price keeps them out.
    """
    if "max_price" not in criteria:
        return []

    without_price = {key: value for key, value in criteria.items() if key != "max_price"}
    candidates = [
        product
        for product in take_what_qualifies(products, without_price, gender_specific_types)
        if product.price is not None and product.price > criteria["max_price"]
    ]
    ordered = order_by_precedence(candidates, criteria, quality_by_product)

    return [
        ExcludedProduct(
            product_id=product.product_id,
            name=product.name,
            price=product.price,
            exclusion_reason="over_budget",
            actual=product.price,
            required=criteria["max_price"],
        )
        for product in ordered[:EXCLUDED_CAP]
    ]


# --------------------------------------------------------------------------
# The related-products logic (B0)
# --------------------------------------------------------------------------

RELATIONS = ("alternative_to", "pairs_with")


def _alternative_levels(
    anchor: Product | None, products: list[Product], criteria: dict
) -> list[list[Product]]:
    """The three levels: where each candidate comes from, and in which order.

    A candidate from a lower level never overtakes one from a higher level: the
    upper level is exhausted first and only then is the limit filled.
    """
    if anchor is None:
        # With no source product, the accumulated intention supplies the same
        # categories that would otherwise be read from that product. There is no
        # generic remainder: if neither the requested type nor a functional family
        # defines a relation level, this function has no related candidates to add.
        requested_type = criteria.get("product_type")
        if not requested_type:
            if not criteria.get("functional_family"):
                return []
            same_family = [
                p
                for p in products
                if _matches(p.functional_family, criteria.get("functional_family"))
            ]
            return [same_family]
        same_type = [p for p in products if p.product_type == requested_type]
        family = {
            value
            for p in same_type
            for value in p.functional_family
        }
        same_family = [
            p
            for p in products
            if p.product_type != requested_type and set(p.functional_family) & family
        ]
        return [same_type, same_family]

    explicit = [p for p in products if p.product_id in anchor.alternative_to]
    already_seen = {p.product_id for p in explicit} | {anchor.product_id}

    same_type = [
        p
        for p in products
        if p.product_type == anchor.product_type and p.product_id not in already_seen
    ]
    already_seen |= {p.product_id for p in same_type}

    same_family = [
        p
        for p in products
        if set(p.functional_family) & set(anchor.functional_family)
        and p.product_id not in already_seen
    ]

    return [explicit, same_type, same_family]


def related_products(
    products: list[Product],
    relation: str,
    anchor: Product | None,
    criteria: dict,
    limit: int,
    gender_specific_types: set[str] | None = None,
    quality_by_product: dict[str, str] | None = None,
) -> list[Product]:
    """Walk the levels of the relation applying boundaries and precedence.

    The whole rule in one line: relation, then boundaries, then precedence within
    the level, then the next level, then `product_id`. No ordering logic of its
    own is created for related products: the two pieces that already exist are
    reused.
    """
    if relation == "pairs_with":
        if anchor is None:
            return []
        levels = [[p for p in products if p.product_id in anchor.pairs_with]]
    else:
        levels = _alternative_levels(anchor, products, criteria)

    # A complement does not have to stand on its own as a gift: it is the path by
    # which the ink sampler, the sharpening stone or the case arrive.
    require_standalone_gift = relation != "pairs_with"

    chosen: list[Product] = []
    for level in levels:
        if len(chosen) >= limit:
            break
        candidates = [p for p in level if anchor is None or p.product_id != anchor.product_id]
        inside = take_what_qualifies(
            candidates, criteria, gender_specific_types, require_standalone_gift
        )
        for product in order_by_precedence(inside, criteria, quality_by_product):
            if len(chosen) >= limit:
                break
            chosen.append(product)

    return chosen
