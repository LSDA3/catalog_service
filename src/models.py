"""The shapes the service returns.

Three base shapes (B4.2) and **one envelope per operation** (B4.8). There is no
common envelope: each operation carries its own metadata and nothing else,
because a universal schema would force every operation to declare fields it does
not use, and the agent would have to guess which ones really travel.

**Every shape is named after its operation, literally.** The class name is the
schema name in the specification, so indigo.ai reads it: a different name here
would make the contract talk about something that is not in the memory.

Plain dataclasses are used, so these shapes can be loaded and checked without
starting the service.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# B4.3 · Product, 26 fields
# --------------------------------------------------------------------------


@dataclass
class Product:
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
    color: str
    material: str
    in_stock: bool
    is_standalone_gift: bool

    # Classification
    category: str
    secondary_categories: list[str]
    subcategory: str
    product_type: str
    functional_family: list[str]
    use_case: list[str]
    occasion: list[str]
    recipient: list[str]
    suitable_relationships: list[str]
    gift_risk: str
    rating: float | None
    reviews_count: int | None

    # Commercial relations
    stocking_filler: bool
    pairs_with: list[str]
    alternative_to: list[str]


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


@dataclass
class ExcludedProduct:
    """A relevant candidate that a boundary of the query left out.

    It carries no categories and no description on purpose: a product in
    `excluded` cannot be recommended, so the agent does not have to write a
    reason for it — only to name it honestly.
    """

    product_id: str
    name: str
    price: float | None
    exclusion_reason: str
    actual: float | int | None = None
    required: float | int | None = None


# --------------------------------------------------------------------------
# B4.5 · CategorySummary
# --------------------------------------------------------------------------


@dataclass
class CategorySummary:
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


@dataclass
class NotApplied:
    """A criterion that arrived and could not be applied.

    It is to criteria what `ExcludedProduct` is to products: without it, the
    absence of a criterion in `query_understood` does not distinguish the
    customer not saying it from us not understanding it.
    """

    parameter: str
    received: str
    reason: str


@dataclass
class GetCategoriesResponse:
    """`get_categories`. No metadata of its own."""

    results: list[CategorySummary]
    currency: str = "EUR"


@dataclass
class GetProductsByCategoryResponse:
    """`get_products_by_category`. The only paginated one: `total` and `offset`."""

    results: list[Product]
    total: int
    offset: int
    currency: str = "EUR"


@dataclass
class FindProductsByCriteriaResponse:
    """`find_products_by_criteria`.

    The only one that exposes `not_applied`. `excluded` and `not_applied` are
    omitted when empty, which is why their default is `None` and not an empty
    list: absent and empty are not the same thing.
    """

    results: list[Product]
    query_understood: dict[str, object]
    excluded: list[ExcludedProduct] | None = None
    not_applied: list[NotApplied] | None = None
    currency: str = "EUR"


@dataclass
class RelatedProduct(Product):
    """An element of `get_related_products`: a `Product` **plus** its relation.

    `relation_type` describes the relation with the starting point, not the
    product, which is why it is not part of `Product`: it is added only here,
    where there is a starting point to talk about.
    """

    relation_type: str | None = None


@dataclass
class GetRelatedProductsResponse:
    """`get_related_products`. Exposes `excluded`, never `not_applied`."""

    results: list[RelatedProduct]
    query_understood: dict[str, object] | None = None
    excluded: list[ExcludedProduct] | None = None
    currency: str = "EUR"


@dataclass
class GetProductDetailsResponse:
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


@dataclass
class RecoverableError:
    """A foreseen request that cannot be executed. It travels with HTTP 200.

    It is content, not transport: that is why it enters through the Success
    branch of the API Block and the agent tells it apart by `error_type`.
    """

    error_type: str
    detail: str = ""
    parameter: str | None = None
    product_id: str | None = None
    relation: str | None = None


@dataclass
class TechnicalFailure:
    """A real failure of the service. It travels with 5xx and its own vocabulary."""

    error_code: str
    incident_id: str
    retryable: bool = True


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
