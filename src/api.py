"""The five operations, the access boundary and the OpenAPI specification.

**Every operation is named the SAME everywhere**: the route, the `operation_id`
and the schema of its response carry the name the memory fixes. A different name
in any of the three places is a name the agent sees and that no decision holds.

**Every parameter is declared.** The specification is the only thing indigo.ai
reads to build its calls: a criterion that is not declared here does not exist
for the agent, no matter how well the service knows how to apply it. That is why
no parameters are read by hand from the request, and why every operation declares
the shape of its response.

The descriptions of B7 are written **literally**, in English, because they are
exactly what the model reads when deciding which capability to use.

Nothing in this module decides which products come out or in which order: that
lives entirely in `selection.py`. Here the request is translated, the access is
checked and the response is shaped.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

import normalization
import selection
from models import (
    LIMITS_BY_OPERATION,
    TechnicalFailure,
    RecoverableError,
    FunctionalFamily,
    SuitableRelationship,
    UseCase,
    definitions_of,
    CategorySummary,
    ExcludedProduct,
    NotApplied,
    Product,
    RelatedProduct,
    FindProductsByCriteriaResponse,
    GetCategoriesResponse,
    GetProductDetailsResponse,
    GetProductsByCategoryResponse,
    GetRelatedProductsResponse,
)
from repository import InMemoryCatalog

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data" / "catalog.csv"
VOCABULARIES = ROOT / "data" / "vocabularies.yaml"
SEMANTIC_LAYER = ROOT / "data" / "semantic_layer.json"

# --------------------------------------------------------------------------
# Start-up · the catalog is loaded once, not on every request
# --------------------------------------------------------------------------

catalog = InMemoryCatalog(CSV, VOCABULARIES, SEMANTIC_LAYER)
GENDER_SPECIFIC_TYPES = normalization.gender_specific_product_types(VOCABULARIES)
VOCABULARY = yaml.safe_load(VOCABULARIES.read_text(encoding="utf-8"))
QUALITY_BY_PRODUCT = {
    p.product_id: catalog.off_contract(p.product_id)["description_quality"]
    for p in catalog.all_products()
}
CATEGORIES = catalog.categories()
SUBCATEGORIES = sorted({p.subcategory for p in catalog.all_products() if p.subcategory})
BRANDS = sorted({p.brand for p in catalog.all_products() if p.brand})

# --------------------------------------------------------------------------
# B6 · the access boundary
# --------------------------------------------------------------------------

HEADER_NAME = "X-Api-Key"
LIMIT_BY_CREDENTIAL = {"catalog": 60, "diagnostics": 10}
WINDOW_IN_SECONDS = 60

_recent_requests: dict[str, deque[float]] = {"catalog": deque(), "diagnostics": deque()}


def _capability(key_: str | None) -> str | None:
    if not key_:
        return None
    if key_ == os.environ.get("CATALOG_API_KEY"):
        return "catalog"
    if key_ == os.environ.get("DIAGNOSTICS_API_KEY"):
        return "diagnostics"
    return None


def _within_rate_limit(capability: str) -> bool:
    """A sliding window of 60 seconds, in the memory of the process.

    Only what is already older than a minute is forgotten, so no interval of 60
    seconds ever holds more requests than the limit. It goes back to zero on every
    deployment, and would count per container if there were more than one.
    """
    now_ = time.monotonic()
    recent = _recent_requests[capability]
    while recent and now_ - recent[0] > WINDOW_IN_SECONDS:
        recent.popleft()
    if len(recent) >= LIMIT_BY_CREDENTIAL[capability]:
        return False
    recent.append(now_)
    return True


def _reject(code_: int, error_code: str, retryable: bool) -> HTTPException:
    return HTTPException(
        status_code=code_,
        detail={
            "error_code": error_code,
            "incident_id": uuid.uuid4().hex,
            "retryable": retryable,
        },
    )


def _check(key_: str | None, expected: str) -> str:
    capability = _capability(key_)
    if capability is None:
        raise _reject(401, "unauthorized", False)
    if capability != expected:
        raise _reject(403, "forbidden", False)
    if not _within_rate_limit(capability):
        raise _reject(429, "rate_limited", True)
    return capability


async def catalog_credential(
    x_api_key: str | None = Depends(
        APIKeyHeader(name=HEADER_NAME, scheme_name="CatalogApiKey", auto_error=False)
    ),
) -> str:
    """The five catalog operations. This is the one indigo.ai uses."""
    return _check(x_api_key, "catalog")


async def diagnostics_credential(
    x_api_key: str | None = Depends(
        APIKeyHeader(name=HEADER_NAME, scheme_name="CatalogApiKey", auto_error=False)
    ),
) -> str:
    """The operator of the service, and nobody else."""
    return _check(x_api_key, "diagnostics")


# --------------------------------------------------------------------------
# Recoverable errors · HTTP 200 with `error_type`
# --------------------------------------------------------------------------


def recoverable(error_type: str, **extra: Any) -> JSONResponse:
    """A foreseen request that cannot be executed. It is content, not transport."""
    body = {"error_type": error_type}
    body.update({key_: value_ for key_, value_ in extra.items() if value_ is not None})
    return JSONResponse(status_code=200, content=body)


# --------------------------------------------------------------------------
# Vocabularies for the specification
# --------------------------------------------------------------------------


def _values_of(field: str) -> list[str]:
    return sorted(VOCABULARY[field])


def _definitions_of(field: str) -> str:
    lines = []
    for key_, definition in VOCABULARY[field].items():
        text_ = definition.get("definicion", "") if isinstance(definition, dict) else ""
        lines.append(f"`{key_}`: {text_}".strip())
    return "\n".join(lines)


USE_CASE = _values_of("use_case")
FUNCTIONAL_FAMILY = _values_of("functional_family")
SUITABLE_RELATIONSHIPS = _values_of("suitable_relationships")
OCCASIONS = sorted({value_ for p in catalog.all_products() for value_ in p.occasion})

# `product_type` has NO `enum`: it is the only vocabulary that grows with the
# inventory, and in the contract it travels as free text resolved by aliases.
PRODUCT_TYPE_DESCRIPTION = (
    "Concrete type of object explicitly or unambiguously requested by the customer, "
    "as free text. The service resolves canonical product types and known aliases "
    "deterministically. Do not guess a product type from a broader function, activity "
    "or category: send it only when the customer named the object."
)


def _resolve_product_type(requested: str | None) -> tuple[str | None, NotApplied | None]:
    """Resolve the requested object by its name or by one of its aliases."""
    if not requested:
        return None, None
    types_ = VOCABULARY["product_type"]
    if requested in types_:
        return requested, None
    for canonical, definition in types_.items():
        aliases_ = definition.get("aliases", []) if isinstance(definition, dict) else []
        if requested.lower() in [a.lower() for a in aliases_]:
            return canonical, None
    return None, NotApplied(parameter="product_type", received=requested, reason="unresolved")


def _without_nulls(criteria: dict) -> dict:
    return {key_: value_ for key_, value_ in criteria.items() if value_ is not None}


# --------------------------------------------------------------------------
# B7.10 · the shared criteria, defined once
# --------------------------------------------------------------------------
#
# A criterion that appears in more than one operation is declared here and reused,
# with the same schema and the same description. What each operation adds is the
# context of the operation, never a second definition of the criterion.

MaxPrice = Annotated[float | None, Query(description="Upper price boundary, in EUR.")]
MinPrice = Annotated[float | None, Query(description="Lower price boundary, in EUR.")]
TargetPrice = Annotated[
    float | None,
    Query(description="Approximate price. It opens a band of ±20 % around it."),
]
MaxShippingDays = Annotated[
    int | None,
    Query(ge=0, description="Maximum acceptable delivery time, in days."),
]
ProductType = Annotated[str | None, Query(description=PRODUCT_TYPE_DESCRIPTION)]
Category = Annotated[
    str | None, Query(description="Catalog category. Allowed values: " + ", ".join(CATEGORIES))
]
Subcategory = Annotated[str | None, Query(description="Catalog subcategory.")]
Brand = Annotated[str | None, Query(description="Exact brand. It does not admit degree.")]
Color = Annotated[
    str | None,
    Query(
        description=(
            "Colour stated by the customer. A broad term defined in the catalog "
            "vocabulary expands to every catalog colour it covers; otherwise an exact "
            "catalog value is matched case-insensitively. Keep the customer's precision "
            "and do not invent a more specific colour.\n" + _definitions_of("color")
        )
    ),
]
Material = Annotated[
    str | None,
    Query(
        description=(
            "Material stated by the customer. A broad term defined in the catalog "
            "vocabulary expands to every catalog material it covers; otherwise an exact "
            "catalog value is matched case-insensitively. Keep the customer's precision "
            "and do not invent a more specific material.\n" + _definitions_of("material")
        )
    ),
]
UseCaseCriterion = Annotated[
    list[UseCase] | None,
    Query(
        description=(
            "Situations in which the product is used. It accepts several values, "
            "which are alternatives and not accumulated points.\n"
            + definitions_of("use_case")
        )
    ),
]
FunctionalFamilyCriterion = Annotated[
    list[FunctionalFamily] | None,
    Query(
        description=(
            "The work the object does. It accepts several values, which are "
            "alternatives and not accumulated points.\n" + definitions_of("functional_family")
        )
    ),
]
Occasion = Annotated[
    str | None,
    Query(description="Event the gift is for. Allowed values: " + ", ".join(OCCASIONS)),
]
Recipient = Annotated[
    Literal["her", "him", "couple", "kids"] | None,
    Query(
        description=(
            "Who receives the gift. `kids` is a hard boundary and never matches "
            "`anyone`. For `her`, `him` and `couple`, `anyone` matches at the recipient "
            "precedence level; only a product type explicitly marked `gender_specific` "
            "can exclude an incompatible adult recipient."
        )
    ),
]
Relationship = Annotated[
    SuitableRelationship | None,
    Query(
        description=(
            "Relationship between buyer and recipient. It never removes a product: it "
            "only decides which of the surviving products comes first.\n"
            + definitions_of("suitable_relationships")
        )
    ),
]
GiftWrapRequired = Annotated[
    bool | None,
    Query(
        description=(
            "Send `true` only when the customer asked for gift wrapping. Absent is not "
            "`false`: absence is a state of its own and is never claimed as a preference."
        )
    ),
]
BuyerKnowsRecipient = Annotated[
    bool | None,
    Query(
        description=(
            "Whether the buyer knows the recipient well. Absent and `false` behave the "
            "same and keep the precaution; `true` removes it. Absent is not `false`."
        )
    ),
]


# --------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------

app = FastAPI(
    title="Catalog Service",
    version="1.0.0",
    description=(
        "Read-only catalog service for a gift shop. Every call is a pure function of "
        "its parameters: the service keeps no state between calls and no conversation "
        "context. Ordering is produced by walking a declared precedence of criteria; "
        "no numeric product score exists anywhere in this contract."
    ),
)

app.add_exception_handler(
    HTTPException,
    lambda request, error: JSONResponse(
        status_code=error.status_code,
        content=(
            error.detail
            if isinstance(error.detail, dict) and "error_code" in error.detail
            else {"detail": error.detail}
        ),
        headers=error.headers,
    ),
)
app.add_exception_handler(
    Exception,
    lambda request, error: JSONResponse(
        status_code=503,
        content={
            "error_code": "service_unavailable",
            "incident_id": uuid.uuid4().hex,
            "retryable": False,
        },
    ),
)


# --------------------------------------------------------------------------
# B5.3 · foreseeable validation errors are not transport failures
# --------------------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def _validation_becomes_recoverable(request: Request, error: RequestValidationError):
    """Turn the automatic validation of FastAPI into the recoverable contract."""
    first = error.errors()[0] if error.errors() else {}
    location = [part for part in first.get("loc", []) if part not in ("query", "path", "body")]
    return recoverable(
        "invalid_parameter",
        parameter=str(location[0]) if location else None,
        received=first.get("input"),
    )


ACCESS_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Missing or unknown credential.", "model": TechnicalFailure},
    403: {"description": "Valid credential without access to this surface.", "model": TechnicalFailure},
    429: {"description": "Too many requests for this credential.", "model": TechnicalFailure},
    503: {"description": "The catalog could not be queried.", "model": TechnicalFailure},
}


@app.get(
    "/get_categories",
    operation_id="get_categories",
    response_model=GetCategoriesResponse,
    dependencies=[Depends(catalog_credential)],
    responses=ACCESS_RESPONSES,
    description=(
        "Returns the current normalized product categories in the catalog, including "
        "the number of available products and the current available price range for "
        "each category. Use when the customer wants to know what kinds of products the "
        "shop carries, or wants to start browsing by category. This operation returns "
        "category summaries, not product recommendations."
    ),
)
async def get_categories() -> GetCategoriesResponse:
    summaries = []
    for name_ in CATEGORIES:
        available = [p for p in catalog.all_products() if p.category == name_ and p.in_stock]
        prices = [p.price for p in available if p.price is not None]
        summaries.append(
            CategorySummary(
                name=name_,
                available_count=len(available),
                price_min=min(prices) if prices else None,
                price_max=max(prices) if prices else None,
            )
        )
    return GetCategoriesResponse(results=summaries)


@app.get(
    "/get_products_by_category",
    operation_id="get_products_by_category",
    response_model=GetProductsByCategoryResponse,
    dependencies=[Depends(catalog_credential)],
    responses=ACCESS_RESPONSES,
    description=(
        "Browses products from one explicitly requested catalog category. Returns up "
        "to 8 products per page and supports continued navigation with `offset`. Use "
        "when the customer wants to browse what is available inside a category they "
        "have named. Always carry over any budget and delivery limits the customer has "
        "already stated. Do not use for a broader gift-discovery request in which "
        "category is only one of several preferences; use `find_products_by_criteria` "
        "instead. When browsing, `sort` is the customer's own choice of order and does "
        "not affect how recommendations are ranked anywhere else."
    ),
)
async def get_products_by_category(
    category: Annotated[str, Query(description="The catalog category to browse.")],
    max_price: MaxPrice = None,
    target_price: TargetPrice = None,
    min_price: MinPrice = None,
    max_shipping_days: MaxShippingDays = None,
    sort: Annotated[
        Literal["rating", "price_asc", "price_desc"],
        Query(
            description=(
                "Order of the page the customer is browsing. `rating` is the default; "
                "`price_asc` and `price_desc` answer questions such as what is the "
                "cheapest item in a category."
            )
        ),
    ] = "rating",
    limit: Annotated[int, Query(ge=1, le=8, description="Products per page, 1 to 8.")] = 8,
    offset: Annotated[int, Query(ge=0, description="Where the page starts within `total`.")] = 0,
) -> Any:
    minimum, maximum, _ = LIMITS_BY_OPERATION["get_products_by_category"]
    if not (minimum <= limit <= maximum):
        return recoverable("invalid_parameter", parameter="limit", received=limit)
    if offset < 0:
        return recoverable("invalid_parameter", parameter="offset", received=offset)
    if category not in CATEGORIES:
        return recoverable("invalid_parameter", parameter="category", received=category)

    criteria = _without_nulls(
        {
            "max_price": max_price,
            "target_price": target_price,
            "min_price": min_price,
            "max_shipping_days": max_shipping_days,
        }
    )
    of_the_category = [p for p in catalog.all_products() if p.category == category]
    # Browsing the shelf is not offering a gift: `is_standalone_gift` is not
    # required here. `in_stock` does cut, as everywhere in the service (B2.7).
    inside = selection.take_what_qualifies(
        of_the_category, criteria, GENDER_SPECIFIC_TYPES, require_standalone_gift=False
    )

    if sort == "price_asc":
        ordered = sorted(inside, key=lambda p: (p.price is None, p.price, p.product_id))
    elif sort == "price_desc":
        ordered = sorted(
            inside, key=lambda p: (p.price is None, -(p.price or 0), p.product_id)
        )
    else:
        ordered = sorted(inside, key=lambda p: (selection._level_six(p), p.product_id))

    return GetProductsByCategoryResponse(
        results=ordered[offset : offset + limit], total=len(ordered), offset=offset
    )


@app.get(
    "/find_products_by_criteria",
    operation_id="find_products_by_criteria",
    response_model=FindProductsByCriteriaResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(catalog_credential)],
    responses=ACCESS_RESPONSES,
    description=(
        "Primary cross-category product-discovery operation. Searches the whole "
        "catalog using any combination of customer constraints and preference signals, "
        "removes products that violate the applicable hard boundaries, and orders the "
        "remaining products by walking the declared precedence of criteria, from most "
        "to least decisive. Use when the customer describes what they want rather than "
        "browsing one named category. Not every criterion needs to be known. Results "
        "are ordered from most to least relevant; no numeric product score exists. "
        "When `product_type` resolves in this search, results are exact matches for "
        "that product type; other product types are never returned as if they satisfied "
        "the exact request. If the value cannot be resolved it is returned in "
        "`not_applied`, and the remaining valid criteria are still applied. Products "
        "in `excluded` do not satisfy the query and must never be presented as valid "
        "results. Criteria listed in `not_applied` were not applied and must not be "
        "claimed as satisfied."
    ),
)
async def find_products_by_criteria(
    max_price: MaxPrice = None,
    target_price: TargetPrice = None,
    min_price: MinPrice = None,
    recipient: Recipient = None,
    relationship: Relationship = None,
    occasion: Occasion = None,
    use_case: UseCaseCriterion = None,
    functional_family: FunctionalFamilyCriterion = None,
    buyer_knows_recipient: BuyerKnowsRecipient = None,
    product_type: ProductType = None,
    category: Category = None,
    subcategory: Subcategory = None,
    brand: Brand = None,
    color: Color = None,
    material: Material = None,
    max_shipping_days: MaxShippingDays = None,
    gift_wrap_required: GiftWrapRequired = None,
    stocking_filler: Annotated[
        bool | None,
        Query(
            description=(
                "Send `true` to look for a small addition that closes a remaining "
                "budget. Absent is not `false`."
            )
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=8, description="Products to return, 1 to 8.")] = 8,
) -> Any:
    minimum, maximum, _ = LIMITS_BY_OPERATION["find_products_by_criteria"]
    if not (minimum <= limit <= maximum):
        return recoverable("invalid_parameter", parameter="limit", received=limit)
    if max_price is not None and min_price is not None and min_price > max_price:
        return recoverable(
            "conflicting_parameters",
            parameter=["min_price", "max_price"],
            received={"min_price": min_price, "max_price": max_price},
        )

    criteria = _without_nulls(
        {
            "max_price": max_price,
            "target_price": target_price,
            "min_price": min_price,
            "recipient": recipient,
            "relationship": relationship,
            "occasion": occasion,
            "use_case": use_case,
            "functional_family": functional_family,
            "buyer_knows_recipient": buyer_knows_recipient,
            "category": category,
            "subcategory": subcategory,
            "brand": brand,
            "color": color,
            "material": material,
            "max_shipping_days": max_shipping_days,
            "gift_wrap_required": gift_wrap_required,
            "stocking_filler": stocking_filler,
        }
    )
    understood = dict(criteria)

    kind_, unresolved = _resolve_product_type(product_type)
    not_applied_ = [unresolved] if unresolved else []

    for field, requested in (("color", color), ("material", material)):
        if requested is None:
            continue
        definition = VOCABULARY.get(field, {}).get(requested.lower())
        if isinstance(definition, dict) and definition.get("cubre"):
            criteria[field] = list(definition["cubre"])
            continue
        values_ = sorted(
            {
                getattr(product, field)
                for product in catalog.all_products()
                if getattr(product, field) is not None
                and getattr(product, field).lower() == requested.lower()
            }
        )
        if values_:
            criteria[field] = values_
        else:
            criteria.pop(field, None)
            understood.pop(field, None)
            not_applied_.append(
                NotApplied(parameter=field, received=requested, reason="unresolved")
            )

    universe = selection.restrict_to_exact_match(catalog.all_products(), kind_)
    inside = selection.take_what_qualifies(universe, criteria, GENDER_SPECIFIC_TYPES)
    ordered = selection.order_by_precedence(inside, criteria, QUALITY_BY_PRODUCT)
    results_ = ordered[:limit]

    excluded_: list[ExcludedProduct] = []
    if kind_ and not results_ and len(universe) == 1 and not universe[0].in_stock:
        product = universe[0]
        excluded_.append(
            ExcludedProduct(
                product_id=product.product_id,
                name=product.name,
                price=product.price,
                exclusion_reason="out_of_stock",
            )
        )
    elif len(results_) < limit:
        excluded_ = selection.above_budget(
            universe, criteria, GENDER_SPECIFIC_TYPES, QUALITY_BY_PRODUCT
        )

    if kind_:
        understood["product_type"] = kind_

    return FindProductsByCriteriaResponse(
        results=results_,
        query_understood=understood,
        excluded=excluded_ or None,
        not_applied=not_applied_ or None,
    )


@app.get(
    "/get_related_products",
    operation_id="get_related_products",
    response_model=GetRelatedProductsResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(catalog_credential)],
    responses=ACCESS_RESPONSES,
    description=(
        "Returns products related to a product the customer has in mind, or to a "
        "sufficiently described product intention. Use `pairs_with` for complementary "
        "products and `alternative_to` for substitutes. `pairs_with` requires a "
        "concrete `product_id`. `alternative_to` may start from a `product_id` or, "
        "when no source product exists, from the semantic criteria describing what "
        "should be substituted. Each returned product declares its `relation_type`. "
        "Do not use this operation for initial gift discovery. In this operation the "
        "shared criteria describe what is being substituted or complemented, not what "
        "is being searched for from scratch. Price and delivery boundaries constrain "
        "which related product is acceptable; they do not by themselves describe what "
        "the customer wants replaced. A request with only a price boundary and no "
        "product or concept returns `missing_anchor`. Candidates are taken level by "
        "level: explicit `alternative_to` first, then same `product_type`, then same "
        "`functional_family`. Within a level, surviving candidates are ordered by the "
        "same criteria precedence used by the search, using only the criteria present "
        "in this call, and remaining ties are stabilised by `product_id`. A candidate "
        "from a lower level never overtakes one from a higher level. No numeric "
        "product score exists here either."
    ),
)
async def get_related_products(
    relation: Annotated[
        Literal["alternative_to", "pairs_with"],
        Query(
            description=(
                "Which relation to walk. It is the only required parameter: without it "
                "the operation has no meaning."
            )
        ),
    ],
    product_id: Annotated[
        str | None,
        Query(
            description=(
                "The source product. It is not a privileged input, it is one more "
                "criterion — but `pairs_with` cannot start without it."
            )
        ),
    ] = None,
    product_type: ProductType = None,
    functional_family: FunctionalFamilyCriterion = None,
    use_case: UseCaseCriterion = None,
    occasion: Occasion = None,
    recipient: Recipient = None,
    relationship: Relationship = None,
    category: Category = None,
    subcategory: Subcategory = None,
    brand: Brand = None,
    color: Color = None,
    material: Material = None,
    max_shipping_days: MaxShippingDays = None,
    gift_wrap_required: GiftWrapRequired = None,
    max_price: MaxPrice = None,
    min_price: MinPrice = None,
    target_price: TargetPrice = None,
    buyer_knows_recipient: BuyerKnowsRecipient = None,
    limit: Annotated[int, Query(ge=1, le=5, description="Products to return, 1 to 5.")] = 3,
) -> Any:
    minimum, maximum, _ = LIMITS_BY_OPERATION["get_related_products"]
    if not (minimum <= limit <= maximum):
        return recoverable("invalid_parameter", parameter="limit", received=limit)

    anchor = catalog.by_id(product_id) if product_id else None
    if product_id and anchor is None:
        return recoverable("product_not_found", product_id=product_id)

    kind_, unresolved = _resolve_product_type(product_type)

    criteria = _without_nulls(
        {
            "functional_family": functional_family,
            "use_case": use_case,
            "occasion": occasion,
            "recipient": recipient,
            "relationship": relationship,
            "category": category,
            "subcategory": subcategory,
            "brand": brand,
            "color": color,
            "material": material,
            "max_shipping_days": max_shipping_days,
            "gift_wrap_required": gift_wrap_required,
            "max_price": max_price,
            "min_price": min_price,
            "target_price": target_price,
            "buyer_knows_recipient": buyer_knows_recipient,
        }
    )
    understood = dict(criteria) if anchor is None else None

    for field, requested in (("color", color), ("material", material)):
        if requested is None:
            continue
        definition = VOCABULARY.get(field, {}).get(requested.lower())
        if isinstance(definition, dict) and definition.get("cubre"):
            criteria[field] = list(definition["cubre"])
            continue
        values_ = sorted(
            {
                getattr(product, field)
                for product in catalog.all_products()
                if getattr(product, field) is not None
                and getattr(product, field).lower() == requested.lower()
            }
        )
        if values_:
            criteria[field] = values_
        else:
            return recoverable("invalid_parameter", parameter=field, received=requested)

    if relation == "pairs_with" and anchor is None:
        return recoverable("missing_anchor", relation=relation)

    boundaries = {"max_price", "min_price", "target_price", "max_shipping_days", "gift_wrap_required"}
    has_intention = bool(kind_) or any(key_ not in boundaries for key_ in criteria)
    if relation == "alternative_to" and anchor is None and not has_intention:
        return recoverable("missing_anchor", relation=relation)

    # The exact-match restriction of `find_products_by_criteria` **does not
    # propagate here** (v34 → v35). In this operation `product_type` is the anchor
    # of the relation — the object to be substituted — and the answer may well be
    # another type: that is precisely what a substitute is.
    if anchor is None and kind_:
        criteria["product_type"] = kind_
        if understood is not None:
            understood["product_type"] = kind_
    chosen = selection.related_products(
        catalog.all_products(),
        relation,
        anchor,
        criteria,
        limit,
        GENDER_SPECIFIC_TYPES,
        QUALITY_BY_PRODUCT,
    )

    results_ = []
    for product in chosen:
        kind = None
        if relation == "alternative_to":
            # For an explicit relation it is the persisted value; for a derived
            # one, `same_function`, deterministically.
            explicit = (
                catalog.relation_type_of(anchor.product_id, product.product_id) if anchor else None
            )
            kind = explicit or "same_function"
        results_.append(RelatedProduct(**vars(product), relation_type=kind))

    excluded_: list[ExcludedProduct] = []
    if max_price is not None:
        without_price = {key_: value_ for key_, value_ in criteria.items() if key_ != "max_price"}
        if relation == "pairs_with":
            levels = [
                [
                    product
                    for product in catalog.all_products()
                    if anchor is not None and product.product_id in anchor.pairs_with
                ]
            ]
        else:
            levels = selection._alternative_levels(anchor, catalog.all_products(), criteria)

        for level in levels:
            if len(excluded_) >= selection.EXCLUDED_CAP:
                break
            candidates = [
                product
                for product in level
                if anchor is None or product.product_id != anchor.product_id
            ]
            inside = selection.take_what_qualifies(
                candidates,
                without_price,
                GENDER_SPECIFIC_TYPES,
                require_standalone_gift=relation != "pairs_with",
            )
            ordered = selection.order_by_precedence(
                [
                    product
                    for product in inside
                    if product.price is not None and product.price > max_price
                ],
                criteria,
                QUALITY_BY_PRODUCT,
            )
            for product in ordered:
                if len(excluded_) >= selection.EXCLUDED_CAP:
                    break
                excluded_.append(
                    ExcludedProduct(
                        product_id=product.product_id,
                        name=product.name,
                        price=product.price,
                        exclusion_reason="over_budget",
                        actual=product.price,
                        required=max_price,
                    )
                )

    return GetRelatedProductsResponse(
        results=results_,
        query_understood=understood,
        excluded=excluded_ or None,
    )


@app.get(
    "/get_product_details",
    operation_id="get_product_details",
    response_model=GetProductDetailsResponse,
    dependencies=[Depends(catalog_credential)],
    responses=ACCESS_RESPONSES,
    description=(
        "Returns the complete catalog representation of one identified product. Use "
        "when the customer explicitly refers to a known product, or when a direct "
        "lookup by `product_id` is required. A direct lookup may reveal the real "
        "availability state of a product that normal discovery would not return. Do "
        "not call this operation merely to enrich a product already returned by "
        "another operation, because every product-returning operation uses the same "
        "complete Product schema."
    ),
)
async def get_product_details(
    product_id: Annotated[
        str, Query(description="The canonical identifier of the product.")
    ],
) -> Any:
    product = catalog.by_id(product_id)
    if product is None:
        return recoverable("product_not_found", product_id=product_id)
    return GetProductDetailsResponse(result=product)


# --------------------------------------------------------------------------
# Outside the contract
# --------------------------------------------------------------------------


@app.get(
    "/_diagnostics/load-report",
    include_in_schema=False,
    dependencies=[Depends(diagnostics_credential)],
)
async def load_report() -> dict:
    """The load quality report. It is not part of the specification."""
    products = catalog.all_products()
    return {
        "products": len(products),
        "available": sum(1 for p in products if p.in_stock),
        "without_price": sum(1 for p in products if p.price is None),
        "without_rating": sum(1 for p in products if p.rating is None),
        "without_occasion": sum(1 for p in products if not p.occasion),
        "poor_description": sum(1 for v in QUALITY_BY_PRODUCT.values() if v == "poor"),
        "merged": {
            p.product_id: catalog.off_contract(p.product_id)["alt_product_ids"]
            for p in products
            if catalog.off_contract(p.product_id)["alt_product_ids"]
        },
    }


# --------------------------------------------------------------------------
# The specification
# --------------------------------------------------------------------------

_specification: dict | None = None


def openapi_specification() -> dict:
    """Publish exactly the contract indigo.ai imports."""
    global _specification
    if _specification is not None:
        return _specification

    from fastapi.openapi.utils import get_openapi

    specification = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes
    )
    components = specification.setdefault("components", {})
    components.setdefault("schemas", {})["RecoverableError"] = RecoverableError.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )

    # 422 does not exist in this contract: every foreseeable validation error
    # travels as HTTP 200 with `error_type` (B5.3). Operations that can produce
    # such a response publish both shapes under the same 200 status.
    for path_ in specification.get("paths", {}).values():
        for method_ in path_.values():
            method_.get("responses", {}).pop("422", None)
            if method_.get("operationId") in {
                "get_products_by_category",
                "find_products_by_criteria",
                "get_related_products",
                "get_product_details",
            }:
                method_["responses"]["200"]["content"]["application/json"]["schema"] = {
                    "oneOf": [
                        method_["responses"]["200"]["content"]["application/json"]["schema"],
                        {"$ref": "#/components/schemas/RecoverableError"},
                    ]
                }
    components.get("schemas", {}).pop("HTTPValidationError", None)
    components.get("schemas", {}).pop("ValidationError", None)

    _specification = specification
    return specification


app.openapi = openapi_specification  # type: ignore[method-assign]