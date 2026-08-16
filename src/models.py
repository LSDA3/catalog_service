"""The shapes the service returns.

Three base shapes (B4.2) and **one envelope per operation** (B4.8). There is no
common envelope: each operation carries its own metadata and nothing else,
because a universal schema would force every operation to declare fields it does
not use, and the agent would have to guess which ones really travel.

**Every shape is named after its operation, literally.** The class name is the
schema name in the specification, so indigo.ai reads it: a different name here
would make the contract talk about something that is not in the memory.

The closed vocabularies travel as real `enum` values, built from
`vocabularies.yaml`, because B7.10 requires the specification to carry the same
values and the same definitions the classifier read. `product_type` is the only
exception: 145 values do not fit in a usable `enum`, so it is free text resolved
by aliases.
"""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

VOCABULARIES = Path(__file__).resolve().parents[1] / "data" / "vocabularies.yaml"
_VOCABULARY = yaml.safe_load(VOCABULARIES.read_text(encoding="utf-8"))


def _enum_of(field: str, class_name: str) -> type[enum.Enum]:
    """Build the `enum` of a closed vocabulary from its single source.

    The specification does not keep copies of values, definitions or aliases: a
    vocabulary change reaches the contract from the same file the rest of the
    system reads (B7.10).
    """
    return enum.Enum(class_name, {key: key for key in sorted(_VOCABULARY[field])}, type=str)


def definitions_of(field: str) -> str:
    """The `definicion` of every value, as the description of the `enum`.

    The classifier labelled the catalog reading these sentences; if the agent
    reads different ones, the two speak similar languages and neither notices.
    """
    lines = []
    for key, definition in _VOCABULARY[field].items():
        text = definition.get("definicion", "") if isinstance(definition, dict) else ""
        lines.append(f"`{key}`: {text}".strip())
    return "\n".join(lines)


UseCase = _enum_of("use_case", "UseCase")
FunctionalFamily = _enum_of("functional_family", "FunctionalFamily")
GiftRisk = _enum_of("gift_risk", "GiftRisk")
SuitableRelationship = _enum_of("suitable_relationships", "SuitableRelationship")

# --------------------------------------------------------------------------
# B4.3 · Product, 26 fields
# --------------------------------------------------------------------------


class Product(BaseModel):
    """The single shape of the four operations that return merchandise.

    The 26 fields and not one more: `description_quality`, `tags` and `stock`
    **do not travel** (B4.6). The effect of the first is already applied in the
    ordering, the third is expressed by `in_stock`, and the second takes no part
    in the process.
    """

    # Identity and content
    product_id: str
    name: str
    description: str

    # Purchase conditions
    price: float | None
    shipping_days: int | None
    gift_wrap: bool | None
    brand: str
    color: str | None
    material: str | None
    in_stock: bool = Field(
        description=(
            "Real availability. Discovery and browsing never return unavailable "
            "products; a direct lookup of a specifically requested product may "
            "return `false`."
        )
    )
    is_standalone_gift: bool = Field(
        description=(
            "Whether the product can serve as the main gift rather than only as an "
            "accessory or complement."
        )
    )

    # Classification
    category: str
    secondary_categories: list[str]
    subcategory: str
    product_type: str
    functional_family: list[FunctionalFamily]
    use_case: list[UseCase]
    occasion: list[str]
    recipient: list[str]
    suitable_relationships: list[SuitableRelationship]
    gift_risk: GiftRisk = Field(
        description=(
            "How well the recipient's taste must be known to recommend this product "
            "confidently. Use it to shape the wording of the recommendation and to "
            "warn when appropriate. It is not a quality score."
        )
    )
    rating: float | None
    reviews_count: int | None

    # Commercial relations
    stocking_filler: bool = Field(
        description=(
            "Whether the product qualifies as a small standalone additional gift "
            "under the catalog's definition."
        )
    )
    pairs_with: list[str] = Field(
        description=(
            "Identifiers of products explicitly related as complements. Their "
            "presence means the relationship exists; call `get_related_products` to "
            "retrieve the products themselves."
        )
    )
    alternative_to: list[str] = Field(
        description=(
            "Identifiers of products with an explicitly declared alternative "
            "relationship. Broader same-function alternatives are resolved by "
            "`get_related_products`, not listed here."
        )
    )


PRODUCT_FIELDS = (
    "product_id",
    "name",
    "description",
    "price",
    "shipping_days",
    "gift_wrap",
    "brand",
    "color",
    "material",
    "in_stock",
    "is_standalone_gift",
    "category",
    "secondary_categories",
    "subcategory",
    "product_type",
    "functional_family",
    "use_case",
    "occasion",
    "recipient",
    "suitable_relationships",
    "gift_risk",
    "rating",
    "reviews_count",
    "stocking_filler",
    "pairs_with",
    "alternative_to",
)

OFF_CONTRACT_FIELDS = ("description_quality", "tags", "stock", "alt_product_ids")


# --------------------------------------------------------------------------
# B4.4 · ExcludedProduct
# --------------------------------------------------------------------------


class ExcludedProduct(BaseModel):
    """A relevant candidate that a boundary of the query left out.

    It carries no categories and no description on purpose: a product in
    `excluded` cannot be recommended, so the agent does not have to write a
    reason for it — only to name it honestly.
    """

    product_id: str
    name: str
    price: float | None
    exclusion_reason: str = Field(
        description=(
            "The boundary that prevented this product from entering `results`. Always "
            "present on every excluded product."
        )
    )
    actual: float | int | None = Field(
        default=None,
        description=(
            "The product's real value, when the boundary is comparable — for example "
            "a price of 149 against a maximum of 100."
        ),
    )
    required: float | int | None = Field(
        default=None, description="The value the query demanded, when the boundary is comparable."
    )


# --------------------------------------------------------------------------
# B4.5 · CategorySummary
# --------------------------------------------------------------------------


class CategorySummary(BaseModel):
    """The current state of a category, not just its name.

    A category with zero available products still appears, with its zero: the
    map of the shop is not the stock.
    """

    name: str
    available_count: int
    price_min: float | None
    price_max: float | None


# --------------------------------------------------------------------------
# B4.7 and B4.8 · one envelope per operation
# --------------------------------------------------------------------------


class NotApplied(BaseModel):
    """A criterion that arrived and could not be applied.

    It is to criteria what `ExcludedProduct` is to products: without it, the
    absence of a criterion in `query_understood` does not distinguish the
    customer not saying it from us not understanding it.
    """

    parameter: str
    received: str
    reason: str


class GetCategoriesResponse(BaseModel):
    """`get_categories`. No metadata of its own."""

    results: list[CategorySummary]
    currency: str = "EUR"


class GetProductsByCategoryResponse(BaseModel):
    """`get_products_by_category`. The only paginated one: `total` and `offset`."""

    results: list[Product] = Field(
        description=(
            "Products that satisfy every hard boundary applied to the query, ordered "
            "from most to least relevant. Array order is the entire result of the "
            "ordering; no numeric product score exists."
        )
    )
    total: int = Field(
        description=(
            "Number of products in the browsed category that match every boundary "
            "applied in this call, before `limit` and `offset`. Together with "
            "`offset` it tells you whether more pages remain of that same set."
        )
    )
    offset: int = Field(
        description=(
            "Position from which the current page was taken. Add the page size to "
            "continue through the same category."
        )
    )
    currency: str = "EUR"


class FindProductsByCriteriaResponse(BaseModel):
    """`find_products_by_criteria`.

    The only one that exposes `not_applied`. `excluded` and `not_applied` are
    omitted when empty, which is why their default is `None` and not an empty
    list: absent and empty are not the same thing.
    """

    results: list[Product] = Field(
        description=(
            "Products that satisfy every hard boundary applied to the query, ordered "
            "from most to least relevant. Array order is the entire result of the "
            "ordering; no numeric product score exists."
        )
    )
    query_understood: dict[str, object] = Field(
        description=(
            "The normalized criteria the service actually understood and applied. "
            "Criteria reported in `not_applied` do not appear here."
        )
    )
    excluded: list[ExcludedProduct] | None = Field(
        default=None,
        description=(
            "Relevant products that do not satisfy one or more query boundaries and "
            "are therefore not valid results. Never present an excluded product as if "
            "it satisfied the customer's request."
        ),
    )
    not_applied: list[NotApplied] | None = Field(
        default=None,
        description=(
            "Input criteria that could not be applied, while the rest of the query "
            "was still executed. Do not claim that the returned products satisfy "
            "these criteria."
        ),
    )
    currency: str = "EUR"


class RelatedProduct(Product):
    """An element of `get_related_products`: a `Product` **plus** its relation.

    `relation_type` describes the relation with the starting point, not the
    product, which is why it is not part of `Product`: it is added only here,
    where there is a starting point to talk about.
    """

    relation_type: Literal["equivalent", "same_function"] | None = Field(
        default=None,
        description=(
            "Nature of an `alternative_to` relationship. `equivalent` means the two "
            "products are versions of the same object or commercial concept, and is "
            "only used when the catalog provides enough evidence; `same_function` "
            "means another object that serves the same need."
        ),
    )


class GetRelatedProductsResponse(BaseModel):
    """`get_related_products`. Exposes `excluded`, never `not_applied`."""

    results: list[RelatedProduct]
    query_understood: dict[str, object] | None = None
    excluded: list[ExcludedProduct] | None = None
    currency: str = "EUR"


class GetProductDetailsResponse(BaseModel):
    """`get_product_details`. A single `Product`, in `result`, not in a list."""

    result: Product
    currency: str = "EUR"


# --------------------------------------------------------------------------
# Recoverable errors and technical failures · separate vocabularies (B5)
# --------------------------------------------------------------------------

ERROR_TYPE = (
    "invalid_parameter",
    "conflicting_parameters",
    "missing_anchor",
    "product_not_found",
)


class RecoverableError(BaseModel):
    """A foreseen request that cannot be executed. It travels with HTTP 200.

    It is content, not transport: that is why it enters through the Success
    branch of the API Block and the agent tells it apart by `error_type`.
    """

    error_type: Literal[
        "invalid_parameter",
        "conflicting_parameters",
        "missing_anchor",
        "product_not_found",
    ] = Field(
        description=(
            "Stable code identifying a recoverable problem with the request that "
            "prevented the catalog operation from executing. Use it to determine how "
            "the next call must be corrected."
        )
    )
    parameter: str | list[str] | None = None
    received: Any | None = None
    product_id: str | None = None
    relation: Literal["alternative_to", "pairs_with"] | None = None


class TechnicalFailure(BaseModel):
    """A real failure of the service. It travels outside the success path."""

    error_code: Literal[
        "service_unavailable",
        "unauthorized",
        "forbidden",
        "rate_limited",
    ] = Field(
        description=(
            "Stable code for a service-level failure. A technical failure does not "
            "mean the catalog contains no matching products."
        )
    )
    incident_id: str = Field(
        description=(
            "Opaque identifier for server-side troubleshooting. It carries no catalog "
            "or customer meaning and should not be shown to the customer."
        )
    )
    retryable: bool = Field(
        description=(
            "Whether repeating the same request may succeed without changing the "
            "customer's criteria."
        )
    )


METADATA_BY_OPERATION: dict[str, tuple[str, ...]] = {
    "get_categories": (),
    "get_products_by_category": ("total", "offset"),
    "find_products_by_criteria": ("query_understood", "excluded", "not_applied"),
    "get_related_products": ("relation_type", "query_understood", "excluded"),
    "get_product_details": (),
}

LIMITS_BY_OPERATION: dict[str, tuple[int, int, int]] = {
    # operation: (minimum, maximum, default)
    "get_products_by_category": (1, 8, 8),
    "find_products_by_criteria": (1, 8, 8),
    "get_related_products": (1, 5, 3),
}

ABSOLUTE_MAXIMUM = 8
