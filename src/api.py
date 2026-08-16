"""Las cinco operaciones, la frontera de acceso y la especificación OpenAPI.

**Cada operación se llama igual en todas partes**: la path_, el `operation_id` y
el esquema de su response llevan el mismo name_, el que fija la memoria. Un
name_ distinto en cualquiera de los tres sitios es un name_ que el agente ve y
que no está en ninguna decisión.

**Todo parámetro se declara.** La especificación es lo único que indigo.ai lee
para construir sus llamadas: un criterio que no esté declarado aquí no existe
para el agente, por mucho que el servicio sepa aplicarlo. Por eso no se leen
parámetros a mano de la petición, y por eso cada operación declara su forma de
response.

Las descripciones de B7 se escriben **literales**, en inglés, porque son
exactamente lo que el modelo lee al decidir qué capability usar.

Nada de lo que hay en este módulo decide qué products salen ni en qué orden:
eso vive entero en `selection.py`.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

import normalization
import selection
from models import (
    LIMITS_BY_OPERATION,
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

RAIZ = Path(__file__).resolve().parents[1]
CSV = RAIZ / "data" / "catalog.csv"
VOCABULARIOS = RAIZ / "data" / "vocabularies.yaml"
CAPA = RAIZ / "data" / "semantic_layer.json"

# --------------------------------------------------------------------------
# Arranque · el catálogo se carga una vez, no en cada petición
# --------------------------------------------------------------------------

catalog = InMemoryCatalog(CSV, VOCABULARIOS, CAPA)
GENDER_SPECIFIC_TYPES = normalization.gender_specific_product_types(VOCABULARIOS)
VOCABULARY = yaml.safe_load(VOCABULARIOS.read_text(encoding="utf-8"))
QUALITY_BY_PRODUCT = {
    p.product_id: catalog.off_contract(p.product_id)["description_quality"]
    for p in catalog.all_products()
}
CATEGORIES = catalog.categories()
SUBCATEGORIES = sorted({p.subcategory for p in catalog.all_products() if p.subcategory})
BRANDS = sorted({p.brand for p in catalog.all_products() if p.brand})

# --------------------------------------------------------------------------
# B6 · la frontera de acceso
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
    """Ventana deslizante de 60 segundos, en memoria del proceso.

    Solo se olvida lo que ya tiene más de un minuto, así que en ningún intervalo
    de 60 segundos caben más peticiones que el límite. Vuelve a cero en cada
    despliegue, y contaría por contenedor si hubiera más de uno.
    """
    now_ = time.monotonic()
    recent = _recent_requests[capability]
    while recent and now_ - recent[0] > WINDOW_IN_SECONDS:
        recent.popleft()
    if len(recent) >= LIMIT_BY_CREDENTIAL[capability]:
        return False
    recent.append(now_)
    return True


def _reject(code_: int, error_code: str, detail_: str) -> HTTPException:
    return HTTPException(
        status_code=code_,
        detail={"error_code": error_code, "detail": detail_, "incident_id": uuid.uuid4().hex},
    )


def _check(key_: str | None, expected: str) -> str:
    capability = _capability(key_)
    if capability is None:
        raise _reject(401, "unauthorized", "missing or unknown credential")
    if capability != expected:
        raise _reject(403, "forbidden", "this credential cannot use this operation")
    if not _within_rate_limit(capability):
        raise _reject(429, "rate_limited", "too many requests for this credential")
    return capability


async def catalog_credential(x_api_key: str | None = Header(default=None)) -> str:
    """Las cinco operaciones del catálogo. Es la que usa indigo.ai."""
    return _check(x_api_key, "catalog")


async def diagnostics_credential(x_api_key: str | None = Header(default=None)) -> str:
    """Solo la operadora del servicio."""
    return _check(x_api_key, "diagnostics")


# --------------------------------------------------------------------------
# Errores recuperables · HTTP 200 con `error_type`
# --------------------------------------------------------------------------


def recoverable(error_type: str, **extra: Any) -> JSONResponse:
    """Una petición prevista que no se puede ejecutar. Es contenido, no transporte.

    Se devuelve como `Response`, así que no pasa por el `response_model`: la
    especificación describe la forma del éxito, y `error_type` está descrito en
    la descripción de cada operación.
    """
    body = {"error_type": error_type}
    body.update({key_: value_ for key_, value_ in extra.items() if value_ is not None})
    return JSONResponse(status_code=200, content=body)


# --------------------------------------------------------------------------
# Vocabularios para la especificación
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

# `product_type` NO tiene `enum`: es el único vocabulary que crece con el
# inventario, y en el contrato viaja como text_ libre resuelto por aliases_.
PRODUCT_TYPE_DESCRIPTION = (
    "The concrete object the customer asked for, as free text. Resolved against a "
    "controlled but growing vocabulary of product types and their aliases: `gyuto` "
    "resolves to `chef_knife`. When it resolves, only products of exactly that type "
    "are returned. When it does not resolve, it is reported in `not_applied` and "
    "must not be claimed as satisfied. Never send it to narrow a vague intention."
)


def _resolve_product_type(requested: str | None) -> tuple[str | None, NotApplied | None]:
    """Resuelve el objeto requested por su name_ o por uno de sus aliases_."""
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
# La aplicación
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


@app.get(
    "/get_categories",
    operation_id="get_categories",
    response_model=GetCategoriesResponse,
    dependencies=[Depends(catalog_credential)],
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
    category: str = Query(description="The catalog category to browse."),
    max_price: float | None = Query(default=None, description="Upper price boundary, in EUR."),
    target_price: float | None = Query(
        default=None, description="Approximate price. Opens a band of ±20 % around it."
    ),
    min_price: float | None = Query(default=None, description="Lower price boundary, in EUR."),
    max_shipping_days: int | None = Query(
        default=None, description="Maximum acceptable delivery time, in days."
    ),
    sort: Literal["rating", "price_asc", "price_desc"] = Query(
        default="rating",
        description=(
            "Order of the page the customer is browsing. `rating` is the default; "
            "`price_asc` and `price_desc` answer questions such as what is the cheapest "
            "item in a category."
        ),
    ),
    limit: int = Query(default=8, description="Products per page, 1 to 8."),
    offset: int = Query(default=0, description="Where the page starts within `total`."),
) -> Any:
    minimum, maximum, _ = LIMITS_BY_OPERATION["get_products_by_category"]
    if not (minimum <= limit <= maximum):
        return recoverable("invalid_parameter", parameter="limit")
    if offset < 0:
        return recoverable("invalid_parameter", parameter="offset")
    if category not in CATEGORIES:
        return recoverable("invalid_parameter", parameter="category")

    criteria = _without_nulls(
        {
            "max_price": max_price,
            "target_price": target_price,
            "min_price": min_price,
            "max_shipping_days": max_shipping_days,
        }
    )
    of_the_category = [p for p in catalog.all_products() if p.category == category]
    # Navegar el estante no es ofrecer un regalo: aquí no se exige
    # `is_standalone_gift`. `in_stock` sí corta, como en todo el servicio (B2.7).
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
        ordered = selection.order_by_precedence(inside, criteria, QUALITY_BY_PRODUCT)

    return GetProductsByCategoryResponse(
        results=ordered[offset : offset + limit], total=len(ordered), offset=offset
    )


@app.get(
    "/find_products_by_criteria",
    operation_id="find_products_by_criteria",
    response_model=FindProductsByCriteriaResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(catalog_credential)],
    description=(
        "Primary cross-category product-discovery operation. Searches the whole "
        "catalog using any combination of customer constraints and preference signals, "
        "removes products that violate the applicable hard boundaries, and orders the "
        "remaining products by walking the declared precedence of criteria, from most "
        "to least decisive. Use when the customer describes what they want rather than "
        "browsing one named category. Not every criterion needs to be known. Results "
        "are ordered from most to least relevant; no numeric product score exists. "
        "Products in `excluded` do not satisfy the query and must never be presented "
        "as valid results. Criteria listed in `not_applied` were not applied and must "
        "not be claimed as satisfied."
    ),
)
async def find_products_by_criteria(
    max_price: float | None = Query(default=None, description="Upper price boundary, in EUR."),
    target_price: float | None = Query(
        default=None, description="Approximate price. Opens a band of ±20 % around it."
    ),
    min_price: float | None = Query(default=None, description="Lower price boundary, in EUR."),
    recipient: Literal["her", "him", "couple", "kids"] | None = Query(
        default=None,
        description=(
            "Who receives the gift. Only `kids` narrows the results: adult products "
            "match any adult recipient, because the catalog marks gender by commercial "
            "habit and not by a property of the object."
        ),
    ),
    relationship: str | None = Query(
        default=None,
        description=(
            "Relationship between buyer and recipient. It never removes a product: it "
            "only decides which of the surviving products comes first. Allowed values: "
            + ", ".join(SUITABLE_RELATIONSHIPS)
        ),
    ),
    occasion: str | None = Query(
        default=None, description="Event the gift is for. Allowed values: " + ", ".join(OCCASIONS)
    ),
    use_case: list[str] | None = Query(
        default=None,
        description=(
            "Situations in which the product is used. Accepts several values, which are "
            "alternatives and not accumulated points.\n" + _definitions_of("use_case")
        ),
    ),
    functional_family: list[str] | None = Query(
        default=None,
        description=(
            "The work the object does. Accepts several values, which are alternatives "
            "and not accumulated points.\n" + _definitions_of("functional_family")
        ),
    ),
    buyer_knows_recipient: bool | None = Query(
        default=None,
        description=(
            "Whether the buyer knows the recipient well. Absent and `false` behave the "
            "same and keep the precaution; `true` removes it. Absent is not `false`."
        ),
    ),
    product_type: str | None = Query(default=None, description=PRODUCT_TYPE_DESCRIPTION),
    category: str | None = Query(
        default=None, description="Catalog category. Allowed values: " + ", ".join(CATEGORIES)
    ),
    subcategory: str | None = Query(default=None, description="Catalog subcategory."),
    brand: str | None = Query(default=None, description="Exact brand. It does not admit degree."),
    color: str | None = Query(default=None, description="Exact colour. Blue is not almost blue."),
    material: str | None = Query(default=None, description="Exact material."),
    max_shipping_days: int | None = Query(
        default=None, description="Maximum acceptable delivery time, in days."
    ),
    gift_wrap_required: bool | None = Query(
        default=None,
        description=(
            "Send `true` only when the customer asked for gift wrapping. Absent is not "
            "`false`: absence is a state of its own and is never claimed as a preference."
        ),
    ),
    stocking_filler: bool | None = Query(
        default=None,
        description=(
            "Send `true` to look for a small addition that closes a remaining budget. "
            "Absent is not `false`."
        ),
    ),
    limit: int = Query(default=8, description="Products to return, 1 to 8."),
) -> Any:
    minimum, maximum, _ = LIMITS_BY_OPERATION["find_products_by_criteria"]
    if not (minimum <= limit <= maximum):
        return recoverable("invalid_parameter", parameter="limit")
    if max_price is not None and min_price is not None and min_price > max_price:
        return recoverable("conflicting_parameters", parameter="min_price")

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

    kind_, unresolved = _resolve_product_type(product_type)
    not_applied_ = [unresolved] if unresolved else []

    universe = selection.restrict_to_exact_match(catalog.all_products(), kind_)
    inside = selection.take_what_qualifies(universe, criteria, GENDER_SPECIFIC_TYPES)
    ordered = selection.order_by_precedence(inside, criteria, QUALITY_BY_PRODUCT)
    results_ = ordered[:limit]

    excluded_: list[ExcludedProduct] = []
    if len(results_) < limit:
        excluded_ = selection.above_budget(
            universe, criteria, GENDER_SPECIFIC_TYPES, QUALITY_BY_PRODUCT
        )

    understood = dict(criteria)
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
    relation: Literal["alternative_to", "pairs_with"] | None = Query(
        default=None,
        description=(
            "Which relation to walk. It is the only required parameter: without it the "
            "operation has no meaning."
        ),
    ),
    product_id: str | None = Query(
        default=None,
        description=(
            "The source product. It is not a privileged input, it is one more "
            "criterion — but `pairs_with` cannot start without it."
        ),
    ),
    product_type: str | None = Query(default=None, description=PRODUCT_TYPE_DESCRIPTION),
    functional_family: list[str] | None = Query(
        default=None, description="The work the object being substituted does."
    ),
    use_case: list[str] | None = Query(
        default=None, description="Situations in which the object being substituted is used."
    ),
    occasion: str | None = Query(default=None, description="Event the gift is for."),
    recipient: Literal["her", "him", "couple", "kids"] | None = Query(
        default=None, description="Who receives the gift."
    ),
    relationship: str | None = Query(
        default=None, description="Relationship between buyer and recipient."
    ),
    category: str | None = Query(default=None, description="Catalog category."),
    subcategory: str | None = Query(default=None, description="Catalog subcategory."),
    brand: str | None = Query(default=None, description="Exact brand."),
    color: str | None = Query(default=None, description="Exact colour."),
    material: str | None = Query(default=None, description="Exact material."),
    max_shipping_days: int | None = Query(
        default=None, description="Maximum acceptable delivery time, in days."
    ),
    gift_wrap_required: bool | None = Query(
        default=None, description="A hard boundary that must not be lost when walking a relation."
    ),
    max_price: float | None = Query(default=None, description="Upper price boundary, in EUR."),
    min_price: float | None = Query(
        default=None, description="Lower price boundary. This is how an upsell is requested."
    ),
    target_price: float | None = Query(
        default=None, description="Approximate price. Opens a band of ±20 % around it."
    ),
    buyer_knows_recipient: bool | None = Query(
        default=None, description="Whether the buyer knows the recipient well."
    ),
    limit: int = Query(default=3, description="Products to return, 1 to 5."),
) -> Any:
    if relation is None:
        return recoverable("invalid_parameter", parameter="relation")

    minimum, maximum, _ = LIMITS_BY_OPERATION["get_related_products"]
    if not (minimum <= limit <= maximum):
        return recoverable("invalid_parameter", parameter="limit")

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

    if relation == "pairs_with" and anchor is None:
        return recoverable("missing_anchor", relation=relation)

    boundaries = {"max_price", "min_price", "target_price", "max_shipping_days", "gift_wrap_required"}
    has_intention = bool(kind_) or any(key_ not in boundaries for key_ in criteria)
    if relation == "alternative_to" and anchor is None and not has_intention:
        return recoverable("missing_anchor", relation=relation)

    universe = selection.restrict_to_exact_match(
        catalog.all_products(), kind_ if anchor is None else None
    )
    chosen = selection.related_products(
        universe, relation, anchor, criteria, limit, GENDER_SPECIFIC_TYPES, QUALITY_BY_PRODUCT
    )

    results_ = []
    for product in chosen:
        kind = None
        if relation == "alternative_to":
            # Para una relación explícita es el value_ persistido; para una
            # derivada, `same_function` de forma determinista.
            explicit = (
                catalog.relation_type_of(anchor.product_id, product.product_id) if anchor else None
            )
            kind = explicit or "same_function"
        results_.append(RelatedProduct(**vars(product), relation_type=kind))

    understood = dict(criteria) if anchor is None else None
    if understood is not None and kind_:
        understood["product_type"] = kind_
    return GetRelatedProductsResponse(results=results_, query_understood=understood)


@app.get(
    "/get_product_details",
    operation_id="get_product_details",
    response_model=GetProductDetailsResponse,
    dependencies=[Depends(catalog_credential)],
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
    product_id: str = Query(description="The canonical identifier of the product."),
) -> Any:
    product = catalog.by_id(product_id)
    if product is None:
        return recoverable("product_not_found", product_id=product_id)
    return GetProductDetailsResponse(result=product)


# --------------------------------------------------------------------------
# Fuera del contrato
# --------------------------------------------------------------------------


@app.get(
    "/_diagnostics/load-report",
    include_in_schema=False,
    dependencies=[Depends(diagnostics_credential)],
)
async def load_report() -> dict:
    """El informe de calidad de la carga. No forma parte de la especificación."""
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
# La especificación
# --------------------------------------------------------------------------

_specification: dict | None = None


def openapi_specification() -> dict:
    """Publica la especificación con el esquema de seguridad declarado.

    Los vocabularios cerrados viajan ya en los `enum` y en las descripciones de
    cada parámetro, que es donde el modelo los lee. **`product_type` no lleva
    `enum` a propósito**: es el único vocabulary que crece con el inventario, y
    en el contrato es text_ libre resuelto por aliases_.
    """
    global _specification
    if _specification is not None:
        return _specification

    from fastapi.openapi.utils import get_openapi

    specification = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes
    )
    components = specification.setdefault("components", {})
    components["securitySchemes"] = {
        "CatalogApiKey": {"type": "apiKey", "in": "header", "name": HEADER_NAME}
    }
    specification["security"] = [{"CatalogApiKey": []}]
    _specification = specification
    return specification


app.openapi = openapi_specification  # type: ignore[method-assign]
