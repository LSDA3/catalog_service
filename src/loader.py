"""Builds the canonical in-memory model when the service starts.

It does three things and nothing else:

1. Calls `normalization.py` on the CSV and receives the canonical products.
2. Reads `semantic_layer.json` and joins each entry with its canonical product.
3. Builds the in-memory model the rest of the service consumes.

**It transforms nothing on its own.** If a CSV cleaning rule shows up here it is
duplicated: its place is `normalization.py`. Two implementations that drift apart
produce two different catalogs, and the coverage gate of A3.4 would stop meaning
anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import normalization
from models import Product


class IncompleteSemanticLayer(Exception):
    """The derived artifact does not cover exactly the canonical catalog.

    It is the invariant of A3.4 seen from start-up: if the two sets do not match,
    the service cannot answer for the missing products and cannot invent a
    classification for them either.
    """


def _resolve_both_ends(entries: dict[str, dict], field: str) -> dict[str, list[str]]:
    """Return the relation field resolved from both ends.

    A relation is persisted **once**, and each field is written under a different
    rule: `pairs_with` from the accessory towards the main product (A4.7), and
    `alternative_to` under the smaller `product_id` of the pair (A4.8). Making
    the other end aware of it is the loader's job, not the file's: duplicating it
    in the artifact would open the door to the two sides drifting apart.

    Resolving is symmetric, so this function does not care which end holds the
    write. The direction still matters in the file, because it is what says which
    of the two products is the accessory.
    """
    resolved: dict[str, list[str]] = {key: [] for key in entries}
    for product_id, entry in entries.items():
        for link in entry.get(field) or []:
            other = link["product_id"] if isinstance(link, dict) else link
            if other not in resolved:
                continue
            if other not in resolved[product_id]:
                resolved[product_id].append(other)
            if product_id not in resolved[other]:
                resolved[other].append(product_id)
    return {key: sorted(values) for key, values in resolved.items()}


def _relation_types(entries: dict[str, dict]) -> dict[tuple[str, str], str]:
    """The `relation_type` of every `alternative_to` link, in both directions.

    It travels with the relation, not with the product (B4.3), so it is kept
    apart and does not enter `Product`.
    """
    types: dict[tuple[str, str], str] = {}
    for product_id, entry in entries.items():
        for link in entry.get("alternative_to") or []:
            if not isinstance(link, dict):
                continue
            other = link["product_id"]
            kind = link.get("relation_type", "same_function")
            types[(product_id, other)] = kind
            types[(other, product_id)] = kind
    return types


def load(
    csv_path: str | Path,
    vocabularies_path: str | Path,
    semantic_layer_path: str | Path,
) -> tuple[list[Product], dict[str, dict], dict[tuple[str, str], str]]:
    """Return the products, the fields that do not travel and the relation types.

    The second one holds what the service needs and is **not part of `Product`**
    (B4.6): `description_quality`, whose effect is already applied in the
    ordering; `tags`, which takes no part; `stock`, whose quantity adds no
    behaviour; and `alt_product_ids`, which resolves absorbed identifiers.
    """
    layer = json.loads(Path(semantic_layer_path).read_text(encoding="utf-8"))
    entries = layer["products"] if isinstance(layer, dict) and "products" in layer else layer
    if isinstance(entries, list):
        entries = {entry["product_id"]: entry for entry in entries}

    product_types_by_id = {
        product_id: entry["product_type"] for product_id, entry in entries.items()
    }

    canonical, _warnings = normalization.canonicalize(
        csv_path, vocabularies_path, product_types_by_id
    )

    identifiers_in_csv = {product.product_id for product in canonical}
    if identifiers_in_csv != set(entries):
        missing = sorted(identifiers_in_csv - set(entries))
        orphans = sorted(set(entries) - identifiers_in_csv)
        raise IncompleteSemanticLayer(
            f"without semantic entry: {missing or 'none'}; "
            f"without a product in the catalog: {orphans or 'none'}"
        )

    pairs_with = _resolve_both_ends(entries, "pairs_with")
    alternative_to = _resolve_both_ends(entries, "alternative_to")
    relation_types = _relation_types(entries)

    products: list[Product] = []
    off_contract: dict[str, dict] = {}

    for canonical_product in canonical:
        entry = entries[canonical_product.product_id]
        products.append(
            Product(
                product_id=canonical_product.product_id,
                name=canonical_product.name,
                description=canonical_product.description,
                price=canonical_product.price,
                shipping_days=canonical_product.shipping_days,
                gift_wrap=canonical_product.gift_wrap,
                brand=canonical_product.brand,
                color=canonical_product.color,
                material=canonical_product.material,
                in_stock=canonical_product.in_stock,
                is_standalone_gift=bool(entry.get("is_standalone_gift")),
                category=canonical_product.category,
                secondary_categories=canonical_product.secondary_categories,
                subcategory=canonical_product.subcategory,
                product_type=entry["product_type"],
                functional_family=list(entry.get("functional_family") or []),
                use_case=list(entry.get("use_case") or []),
                occasion=canonical_product.occasion,
                recipient=canonical_product.recipient,
                suitable_relationships=list(entry.get("suitable_relationships") or []),
                gift_risk=entry.get("gift_risk", ""),
                rating=canonical_product.rating,
                reviews_count=canonical_product.reviews_count,
                stocking_filler=bool(entry.get("stocking_filler")),
                pairs_with=pairs_with[canonical_product.product_id],
                alternative_to=alternative_to[canonical_product.product_id],
            )
        )
        off_contract[canonical_product.product_id] = {
            "description_quality": canonical_product.description_quality,
            "tags": canonical_product.tags,
            "stock": canonical_product.stock,
            "alt_product_ids": canonical_product.alt_product_ids,
        }

    return products, off_contract, relation_types
