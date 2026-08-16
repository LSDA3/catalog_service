"""Deterministic canonicalization of the catalog.

This is the single implementation of the transformation described in A2.2. It is
consumed by `loader.py` at runtime and by `enrich.py`, `relate.py` and
`validate_semantic.py` during construction. Nobody rewrites it: two
implementations that drift apart produce two different catalogs, and the coverage
gate of A3.4 would stop meaning anything.

It invents nothing. What is not there is not there: a missing price leaves the
product without a price, a missing rating stays missing and never counts as zero.
Faced with a genuinely ambiguous value it stops, naming row, column and value.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class AmbiguousCatalog(Exception):
    """The file carries a value that cannot be read without assuming.

    Stopping the start-up is preferred to getting 80 % right in silence (A2.2).
    """

    def __init__(self, row: int, column: str, value: str, reason: str) -> None:
        super().__init__(f"row {row}, column {column!r}, value {value!r}: {reason}")
        self.row = row
        self.column = column
        self.value = value
        self.reason = reason


@dataclass(frozen=True)
class QualityWarning:
    """A fact observed in the file that is worth reporting (A2.3)."""

    kind: str
    product_id: str
    detail: str


@dataclass
class CanonicalProduct:
    """One product of the catalog after the deterministic transformation."""

    product_id: str
    alt_product_ids: list[str] = field(default_factory=list)
    name: str = ""
    category: str = ""
    secondary_categories: list[str] = field(default_factory=list)
    subcategory: str = ""
    brand: str = ""
    price: float | None = None
    stock: int | None = None
    in_stock: bool = False
    recipient: list[str] = field(default_factory=list)
    occasion: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    color: str = ""
    material: str = ""
    gift_wrap: bool | None = None
    shipping_days: int | None = None
    description: str = ""
    description_quality: str = "ok"
    rating: float | None = None
    reviews_count: int | None = None


# --------------------------------------------------------------------------
# Value normalization
# --------------------------------------------------------------------------

_CURRENCY = re.compile(r"(?i)(^\s*(eur|€)\s*|\s*(eur|€)\s*$)")
_ONLY_DIGITS = re.compile(r"^\d+$")
_DECIMAL_DOT = re.compile(r"^\d+\.\d+$")
_DECIMAL_COMMA = re.compile(r"^\d+,\d{2}$")
_EUROPEAN = re.compile(r"^\d{1,3}(\.\d{3})+,\d{2}$")
_AMBIGUOUS_THOUSANDS = re.compile(r"^\d+,\d{3}$")


def normalize_price(raw: str, row: int) -> float | None:
    """Return the price in euros, or `None` when the file does not carry one.

    Removing a currency symbol or turning a decimal comma into a dot is
    normalizing a format. Filling in what is not there would be inventing (A2.2).
    """
    value = (raw or "").strip()
    if not value:
        return None

    value = _CURRENCY.sub("", value).strip()

    if _ONLY_DIGITS.match(value) or _DECIMAL_DOT.match(value):
        number = float(value)
    elif _DECIMAL_COMMA.match(value):
        number = float(value.replace(",", "."))
    elif _EUROPEAN.match(value):
        number = float(value.replace(".", "").replace(",", "."))
    elif _AMBIGUOUS_THOUSANDS.match(value):
        raise AmbiguousCatalog(
            row, "price_eur", raw, "cannot tell a thousands separator from a decimal one"
        )
    else:
        raise AmbiguousCatalog(row, "price_eur", raw, "unrecognised price format")

    if number <= 0:
        raise AmbiguousCatalog(
            row, "price_eur", raw, "a gift catalog has no null or negative prices"
        )
    return round(number, 2)


_IN_STOCK_WORDS = {"yes", "y", "true", "available", "in stock"}
_OUT_OF_STOCK_WORDS = {"no", "n", "false", "unavailable", "out of stock", "sold out"}


def normalize_stock(raw: str, row: int) -> tuple[int | None, bool]:
    """Return the quantity and the availability.

    A `yes` establishes with certainty that there is stock but not how much, so
    the quantity stays `None` and availability becomes `True` (A2.2).
    """
    value = (raw or "").strip()
    if not value:
        raise AmbiguousCatalog(row, "stock", raw, "no stock declared")

    if _ONLY_DIGITS.match(value):
        quantity = int(value)
        return quantity, quantity > 0

    plain = value.lower()
    if plain in _IN_STOCK_WORDS:
        return None, True
    if plain in _OUT_OF_STOCK_WORDS:
        return None, False

    raise AmbiguousCatalog(row, "stock", raw, "states neither quantity nor availability")


def normalize_category(raw: str) -> str:
    """Trim spaces, unify casing and treat `and` as `&`.

    17 literal values in the file correspond to 11 real categories.
    """
    value = " ".join((raw or "").split())
    value = re.sub(r"(?i)\band\b", "&", value)
    value = " ".join(value.split())
    return " ".join(word.capitalize() if word != "&" else "&" for word in value.split())


def _list_of(raw: str, separator: str = "|") -> list[str]:
    return [part.strip() for part in (raw or "").split(separator) if part.strip()]


def _boolean(raw: str, row: int, column: str) -> bool | None:
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value in _IN_STOCK_WORDS:
        return True
    if value in _OUT_OF_STOCK_WORDS:
        return False
    raise AmbiguousCatalog(row, column, raw, "not a recognisable boolean value")


def _integer(raw: str, row: int, column: str) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    if not _ONLY_DIGITS.match(value):
        raise AmbiguousCatalog(row, column, raw, "not an integer")
    return int(value)


def _decimal_number(raw: str, row: int, column: str) -> float | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError as error:
        raise AmbiguousCatalog(row, column, raw, "not a number") from error


# --------------------------------------------------------------------------
# Description quality
# --------------------------------------------------------------------------

_MINIMUM_DESCRIPTION_LENGTH = 25


def description_quality_of(description: str) -> str:
    """`poor` when the description does not allow building a reason (A2.2)."""
    clean = " ".join((description or "").split())
    return "poor" if len(clean) < _MINIMUM_DESCRIPTION_LENGTH else "ok"


# --------------------------------------------------------------------------
# Opening up recipient
# --------------------------------------------------------------------------


def open_recipient(
    original: list[str], product_type: str | None, gender_specific_types: set[str]
) -> list[str]:
    """Add `anyone` to every product that can carry it (A2.2).

    The original value is kept: the mechanical keyboard stays `him` **and**
    `anyone`. Only products marked `kids` and those the vocabulary declares
    gender specific are left without `anyone`.
    """
    values = list(original)
    if "kids" in values:
        return ["kids"]
    if product_type in gender_specific_types:
        return values
    if "anyone" not in values:
        values.append("anyone")
    return values


def gender_specific_product_types(vocabularies_path: Path | str) -> set[str]:
    """The product types the vocabulary marks as exclusive to one gender."""
    vocabulary = yaml.safe_load(Path(vocabularies_path).read_text(encoding="utf-8"))
    types = vocabulary.get("product_type", {})
    return {
        key
        for key, definition in types.items()
        if isinstance(definition, dict) and definition.get("gender_specific")
    }


# --------------------------------------------------------------------------
# Merging duplicates
# --------------------------------------------------------------------------


def _fingerprint(row: dict[str, str]) -> tuple[str, str, str]:
    """Normalized name plus price plus description, which is the rule of A2.2."""
    name = " ".join((row.get("name") or "").split()).lower()
    price = (row.get("price_eur") or "").strip()
    description = " ".join((row.get("description") or "").split()).lower()
    return name, price, description


# --------------------------------------------------------------------------
# The full canonicalization
# --------------------------------------------------------------------------


def canonicalize(
    csv_path: str | Path,
    vocabularies_path: str | Path,
    product_types_by_id: dict[str, str] | None = None,
) -> tuple[list[CanonicalProduct], list[QualityWarning]]:
    """Read the CSV and return the canonical products and the quality warnings.

    `product_types_by_id` maps each `product_id` to its `product_type`. It is
    supplied by whoever has the semantic layer at hand; without it, opening up
    `recipient` cannot recognise the gender specific types.
    """
    gender_specific_types = gender_specific_product_types(Path(vocabularies_path))
    product_types_by_id = product_types_by_id or {}

    with Path(csv_path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    warnings: list[QualityWarning] = []
    groups: dict[tuple[str, str, str], list[tuple[int, dict[str, str]]]] = {}
    for index, row in enumerate(rows, start=2):  # row 1 is the header
        groups.setdefault(_fingerprint(row), []).append((index, row))

    canonical: list[CanonicalProduct] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda pair: pair[1]["product_id"])
        row_number, main = ordered[0]
        absorbed = ordered[1:]

        price = normalize_price(main.get("price_eur", ""), row_number)
        quantity, available = normalize_stock(main.get("stock", ""), row_number)
        category = normalize_category(main.get("category", ""))
        product_id = main["product_id"]

        recipient = open_recipient(
            _list_of(main.get("recipient", "")),
            product_types_by_id.get(product_id),
            gender_specific_types,
        )
        description = (main.get("description") or "").strip()

        product = CanonicalProduct(
            product_id=product_id,
            alt_product_ids=[row["product_id"] for _, row in absorbed],
            name=(main.get("name") or "").strip(),
            category=category,
            secondary_categories=sorted(
                {
                    normalize_category(row.get("category", ""))
                    for _, row in absorbed
                    if normalize_category(row.get("category", "")) != category
                }
            ),
            subcategory=(main.get("subcategory") or "").strip(),
            brand=(main.get("brand") or "").strip(),
            price=price,
            stock=quantity,
            in_stock=available,
            recipient=recipient,
            occasion=_list_of(main.get("occasion", "")),
            tags=_list_of(main.get("tags", "")),
            color=(main.get("color") or "").strip(),
            material=(main.get("material") or "").strip(),
            gift_wrap=_boolean(main.get("gift_wrap", ""), row_number, "gift_wrap"),
            shipping_days=_integer(main.get("shipping_days", ""), row_number, "shipping_days"),
            description=description,
            description_quality=description_quality_of(description),
            rating=_decimal_number(main.get("rating", ""), row_number, "rating"),
            reviews_count=_integer(main.get("reviews_count", ""), row_number, "reviews_count"),
        )
        canonical.append(product)

        for _, row in absorbed:
            warnings.append(
                QualityWarning("duplicate", product_id, f"absorbs {row['product_id']}")
            )
        if price is None:
            warnings.append(QualityWarning("no_price", product_id, "the file carries no price"))
        if product.rating is None:
            warnings.append(QualityWarning("no_rating", product_id, "the file carries no rating"))
        if not product.occasion:
            warnings.append(
                QualityWarning("no_occasion", product_id, "the file carries no occasion")
            )
        if product.description_quality == "poor":
            warnings.append(
                QualityWarning("poor_description", product_id, "no reason can be built from it")
            )

    canonical.sort(key=lambda product: product.product_id)
    return canonical, warnings


def resolve_identifier(
    identifier: str, canonical: list[CanonicalProduct]
) -> CanonicalProduct | None:
    """Return the canonical product of a `product_id` or of an `alt_product_id`.

    An absorbed identifier is not a product that does not exist: it resolves to
    the canonical one and never produces `product_not_found`.
    """
    for product in canonical:
        if identifier == product.product_id or identifier in product.alt_product_ids:
            return product
    return None
