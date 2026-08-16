"""Las cinco operaciones, la frontera de acceso y la especificación OpenAPI.

Las descripciones de B7 se escriben **literales**, en inglés, porque son
exactamente lo que el modelo lee al decidir qué capacidad usar. Cambiarlas aquí
cambia el comportamiento del agente sin tocar el agente.

Nada de lo que hay en este módulo decide qué productos salen ni en qué orden:
eso vive entero en `selection.py`. Aquí se traduce la petición, se comprueba el
acceso y se le da forma a la respuesta.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import normalization
import selection
from models import (
    LIMITES_POR_OPERACION,
    CategorySummary,
    ExcludedProduct,
    NotApplied,
    Product,
)
from repository import CatalogoEnMemoria

RAIZ = Path(__file__).resolve().parents[1]
CSV = RAIZ / "data" / "catalog.csv"
VOCABULARIOS = RAIZ / "data" / "vocabularies.yaml"
CAPA = RAIZ / "data" / "semantic_layer.json"

# --------------------------------------------------------------------------
# Arranque · el catálogo se carga una vez, no en cada petición
# --------------------------------------------------------------------------

catalogo = CatalogoEnMemoria(CSV, VOCABULARIOS, CAPA)
EXCLUSIVOS_DE_GENERO = normalization.tipos_exclusivos_de_genero(VOCABULARIOS)
VOCABULARIO = yaml.safe_load(VOCABULARIOS.read_text(encoding="utf-8"))
CALIDAD_POR_PRODUCTO = {
    p.product_id: catalogo.fuera_del_contrato(p.product_id)["description_quality"]
    for p in catalogo.todos()
}

# --------------------------------------------------------------------------
# B6 · la frontera de acceso
# --------------------------------------------------------------------------

CABECERA = "X-Api-Key"
LIMITE_POR_CREDENCIAL = {"catalog": 60, "diagnostics": 10}
VENTANA_EN_SEGUNDOS = 60

_peticiones_recientes: dict[str, deque[float]] = {
    "catalog": deque(),
    "diagnostics": deque(),
}


def _capacidad(clave: str | None) -> str | None:
    """Qué puede hacer esta credencial. Dos credenciales, dos capacidades."""
    if not clave:
        return None
    if clave == os.environ.get("CATALOG_API_KEY"):
        return "catalog"
    if clave == os.environ.get("DIAGNOSTICS_API_KEY"):
        return "diagnostics"
    return None


def _dentro_del_limite(capacidad: str) -> bool:
    """Ventana deslizante de 60 segundos, en memoria del proceso.

    Solo se olvida lo que ya tiene más de un minuto, así que en ningún intervalo
    de 60 segundos caben más peticiones que el límite. Un contador que se
    reiniciara al empezar cada minuto de reloj permitiría el doble a caballo del
    cambio de minuto.

    Vuelve a cero en cada despliegue, y contaría por contenedor si hubiera más de
    uno. El límite protege de una credencial filtrada mientras alguien la rota, y
    para eso no necesita ser exacto: necesita existir.
    """
    ahora = time.monotonic()
    recientes = _peticiones_recientes[capacidad]
    while recientes and ahora - recientes[0] > VENTANA_EN_SEGUNDOS:
        recientes.popleft()
    if len(recientes) >= LIMITE_POR_CREDENCIAL[capacidad]:
        return False
    recientes.append(ahora)
    return True


def _rechazar(codigo: int, error_code: str, detalle: str) -> HTTPException:
    return HTTPException(
        status_code=codigo,
        detail={
            "error_code": error_code,
            "detail": detalle,
            "incident_id": uuid.uuid4().hex,
        },
    )


async def credencial_de_catalogo(x_api_key: str | None = Header(default=None)) -> str:
    """Las cinco operaciones del catálogo. Es la que usa indigo.ai."""
    capacidad = _capacidad(x_api_key)
    if capacidad is None:
        raise _rechazar(401, "unauthorized", "missing or unknown credential")
    if capacidad != "catalog":
        raise _rechazar(403, "forbidden", "this credential cannot use catalog operations")
    if not _dentro_del_limite(capacidad):
        raise _rechazar(429, "rate_limited", "too many requests for this credential")
    return capacidad


async def credencial_de_diagnostico(x_api_key: str | None = Header(default=None)) -> str:
    """Solo la operadora del servicio."""
    capacidad = _capacidad(x_api_key)
    if capacidad is None:
        raise _rechazar(401, "unauthorized", "missing or unknown credential")
    if capacidad != "diagnostics":
        raise _rechazar(403, "forbidden", "this credential cannot use diagnostics")
    if not _dentro_del_limite(capacidad):
        raise _rechazar(429, "rate_limited", "too many requests for this credential")
    return capacidad


# --------------------------------------------------------------------------
# Errores recuperables · HTTP 200 con `error_type`
# --------------------------------------------------------------------------


def recuperable(error_type: str, **extra: Any) -> JSONResponse:
    """Una petición prevista que no se puede ejecutar. Es contenido, no transporte."""
    cuerpo = {"error_type": error_type}
    cuerpo.update({clave: valor for clave, valor in extra.items() if valor is not None})
    return JSONResponse(status_code=200, content=cuerpo)


# --------------------------------------------------------------------------
# Traducción de la petición a criterios
# --------------------------------------------------------------------------

CRITERIOS_DE_NEGOCIO = (
    "product_type",
    "category",
    "subcategory",
    "brand",
    "color",
    "material",
    "functional_family",
    "use_case",
    "occasion",
    "recipient",
    "relationship",
    "gender_specific",
    "max_price",
    "min_price",
    "target_price",
    "max_shipping_days",
    "gift_wrap_required",
    "buyer_knows_recipient",
    "stocking_filler",
)


def _query_understood(criterios: dict) -> dict:
    """Solo los criterios entendidos y aplicados, ya normalizados.

    No reproduce el `Map` de la conversación ni devuelve campos nulos que no
    participaron: hace visible qué ejecutó de verdad el servicio.
    """
    return {clave: valor for clave, valor in criterios.items() if valor is not None}


def _resolver_product_type(pedido: str | None) -> tuple[str | None, NotApplied | None]:
    """Resuelve el objeto pedido por su nombre o por un alias del vocabulario."""
    if not pedido:
        return None, None
    tipos = VOCABULARIO["product_type"]
    if pedido in tipos:
        return pedido, None
    for canonico, definicion in tipos.items():
        alias = definicion.get("aliases", []) if isinstance(definicion, dict) else []
        if pedido.lower() in [a.lower() for a in alias]:
            return canonico, None
    return None, NotApplied(parameter="product_type", received=pedido, reason="unresolved")


def _producto_como_dict(producto: Product) -> dict:
    return producto.__dict__


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
    "/categories",
    operation_id="get_categories",
    dependencies=[Depends(credencial_de_catalogo)],
    description=(
        "Returns the current normalized product categories in the catalog, including "
        "the number of available products and the current available price range for "
        "each category. Use when the customer wants to know what kinds of products the "
        "shop carries, or wants to start browsing by category. This operation returns "
        "category summaries, not product recommendations."
    ),
)
async def get_categories() -> dict:
    resumenes = []
    for nombre in catalogo.categorias():
        disponibles = [
            p for p in catalogo.todos() if p.category == nombre and p.in_stock
        ]
        precios = [p.price for p in disponibles if p.price is not None]
        resumenes.append(
            CategorySummary(
                name=nombre,
                available_count=len(disponibles),
                price_min=min(precios) if precios else None,
                price_max=max(precios) if precios else None,
            ).__dict__
        )
    return {"currency": "EUR", "results": resumenes}


@app.get(
    "/products/by-category",
    operation_id="get_products_by_category",
    dependencies=[Depends(credencial_de_catalogo)],
    description=(
        "Browses products from one explicitly requested catalog category. Returns up "
        "to 8 products per page and supports continued navigation with `offset`. Use "
        "when the customer wants to browse what is available inside a category they "
        "have named. Always carry over any budget and delivery limits the customer has "
        "already stated. Do not use for a broader gift-discovery request in which "
        "category is only one of several preferences; use `find_products_by_criteria` "
        "instead."
    ),
)
async def get_products_by_category(
    category: str,
    limit: int = Query(default=8),
    offset: int = Query(default=0),
    max_price: float | None = None,
    min_price: float | None = None,
    max_shipping_days: int | None = None,
) -> Any:
    minimo, maximo, _ = LIMITES_POR_OPERACION["get_products_by_category"]
    if not (minimo <= limit <= maximo) or offset < 0:
        return recuperable("invalid_parameter", parameter="limit" if limit else "offset")
    if category not in catalogo.categorias():
        return recuperable("invalid_parameter", parameter="category")

    criterios = _query_understood(
        {
            "max_price": max_price,
            "min_price": min_price,
            "max_shipping_days": max_shipping_days,
        }
    )
    de_la_categoria = [p for p in catalogo.todos() if p.category == category]
    # Navegar el estante no es ofrecer un regalo: aquí no se exige
    # `is_standalone_gift`. `in_stock` sí corta, como en todo el servicio (B2.7).
    dentro = selection.coger_lo_que_cumple(
        de_la_categoria, criterios, EXCLUSIVOS_DE_GENERO, exigir_regalo_autonomo=False
    )
    ordenados = selection.ordenar_por_precedencia(dentro, criterios, CALIDAD_POR_PRODUCTO)

    pagina = ordenados[offset : offset + limit]
    return {
        "currency": "EUR",
        "total": len(ordenados),
        "offset": offset,
        "results": [_producto_como_dict(p) for p in pagina],
    }


@app.get(
    "/products/search",
    operation_id="find_products_by_criteria",
    dependencies=[Depends(credencial_de_catalogo)],
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
async def find_products_by_criteria(request: Request, limit: int = Query(default=8)) -> Any:
    minimo, maximo, _ = LIMITES_POR_OPERACION["find_products_by_criteria"]
    if not (minimo <= limit <= maximo):
        return recuperable("invalid_parameter", parameter="limit")

    recibidos = dict(request.query_params)
    criterios: dict[str, Any] = {}
    no_aplicados: list[NotApplied] = []

    for clave in CRITERIOS_DE_NEGOCIO:
        if clave not in recibidos:
            continue
        valor: Any = recibidos[clave]
        if clave in ("max_price", "min_price", "target_price"):
            valor = float(valor)
        elif clave == "max_shipping_days":
            valor = int(valor)
        elif clave in ("gift_wrap_required", "buyer_knows_recipient", "stocking_filler"):
            valor = valor.lower() == "true"
        elif clave in ("functional_family", "use_case"):
            valor = [parte for parte in valor.split(",") if parte]
        criterios[clave] = valor

    if "max_price" in criterios and "min_price" in criterios:
        if criterios["min_price"] > criterios["max_price"]:
            return recuperable("conflicting_parameters", parameter="min_price")

    tipo, sin_resolver = _resolver_product_type(criterios.pop("product_type", None))
    if sin_resolver is not None:
        no_aplicados.append(sin_resolver)

    conjunto = selection.restringir_por_coincidencia_exacta(catalogo.todos(), tipo)
    dentro = selection.coger_lo_que_cumple(conjunto, criterios, EXCLUSIVOS_DE_GENERO)
    ordenados = selection.ordenar_por_precedencia(dentro, criterios, CALIDAD_POR_PRODUCTO)
    resultados = ordenados[:limit]

    excluidos: list[ExcludedProduct] = []
    if len(resultados) < limit:
        excluidos = selection.por_encima_del_presupuesto(
            conjunto, criterios, EXCLUSIVOS_DE_GENERO, CALIDAD_POR_PRODUCTO
        )

    entendido = _query_understood(criterios)
    if tipo:
        entendido["product_type"] = tipo

    respuesta: dict[str, Any] = {
        "currency": "EUR",
        "query_understood": entendido,
        "results": [_producto_como_dict(p) for p in resultados],
    }
    # `excluded` y `not_applied` se omiten cuando están vacíos: que existan
    # significa "presta atención a esto".
    if excluidos:
        respuesta["excluded"] = [e.__dict__ for e in excluidos]
    if no_aplicados:
        respuesta["not_applied"] = [n.__dict__ for n in no_aplicados]
    return respuesta


@app.get(
    "/products/related",
    operation_id="get_related_products",
    dependencies=[Depends(credencial_de_catalogo)],
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
    request: Request,
    relation: str | None = None,
    product_id: str | None = None,
    limit: int = Query(default=3),
) -> Any:
    if relation not in selection.RELACIONES:
        return recuperable("invalid_parameter", parameter="relation")

    minimo, maximo, _ = LIMITES_POR_OPERACION["get_related_products"]
    if not (minimo <= limit <= maximo):
        return recuperable("invalid_parameter", parameter="limit")

    ancla = catalogo.por_id(product_id) if product_id else None
    if product_id and ancla is None:
        return recuperable("product_not_found", product_id=product_id)

    recibidos = dict(request.query_params)
    criterios: dict[str, Any] = {}
    for clave in CRITERIOS_DE_NEGOCIO:
        if clave == "stocking_filler" or clave not in recibidos:
            continue
        valor: Any = recibidos[clave]
        if clave in ("max_price", "min_price", "target_price"):
            valor = float(valor)
        elif clave == "max_shipping_days":
            valor = int(valor)
        elif clave in ("gift_wrap_required", "buyer_knows_recipient"):
            valor = valor.lower() == "true"
        elif clave in ("functional_family", "use_case"):
            valor = [parte for parte in valor.split(",") if parte]
        criterios[clave] = valor

    if relation == "pairs_with" and ancla is None:
        return recuperable("missing_anchor", relation=relation)

    criterios_semanticos = {
        clave: valor
        for clave, valor in criterios.items()
        if clave
        not in ("max_price", "min_price", "target_price", "max_shipping_days")
    }
    if relation == "alternative_to" and ancla is None and not criterios_semanticos:
        return recuperable("missing_anchor", relation=relation)

    elegidos = selection.relacionados(
        catalogo.todos(),
        relation,
        ancla,
        criterios,
        limit,
        EXCLUSIVOS_DE_GENERO,
        CALIDAD_POR_PRODUCTO,
    )

    resultados = []
    for producto in elegidos:
        elemento = _producto_como_dict(producto)
        if relation == "alternative_to":
            # Para una relación explícita es el valor persistido; para una
            # derivada, `same_function` de forma determinista.
            explicita = ancla and catalogo.tipo_de_relacion(
                ancla.product_id, producto.product_id
            )
            elemento = dict(elemento, relation_type=explicita or "same_function")
        resultados.append(elemento)

    respuesta: dict[str, Any] = {"currency": "EUR", "results": resultados}
    if ancla is None:
        respuesta["query_understood"] = _query_understood(criterios)
    return respuesta


@app.get(
    "/products/{product_id}",
    operation_id="get_product_details",
    dependencies=[Depends(credencial_de_catalogo)],
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
async def get_product_details(product_id: str) -> Any:
    producto = catalogo.por_id(product_id)
    if producto is None:
        return recuperable("product_not_found", product_id=product_id)
    return {"currency": "EUR", "result": _producto_como_dict(producto)}


# --------------------------------------------------------------------------
# Fuera del contrato
# --------------------------------------------------------------------------


@app.get(
    "/_diagnostics/load-report",
    include_in_schema=False,
    dependencies=[Depends(credencial_de_diagnostico)],
)
async def load_report() -> dict:
    """El informe de calidad de la carga. No forma parte de la especificación."""
    productos = catalogo.todos()
    return {
        "products": len(productos),
        "available": sum(1 for p in productos if p.in_stock),
        "without_price": sum(1 for p in productos if p.price is None),
        "without_rating": sum(1 for p in productos if p.rating is None),
        "without_occasion": sum(1 for p in productos if not p.occasion),
        "poor_description": sum(
            1 for valor in CALIDAD_POR_PRODUCTO.values() if valor == "poor"
        ),
        "merged": {
            product_id: datos["alt_product_ids"]
            for product_id, datos in (
                (p.product_id, catalogo.fuera_del_contrato(p.product_id)) for p in productos
            )
            if datos["alt_product_ids"]
        },
    }


# --------------------------------------------------------------------------
# La especificación · los `enum` llevan las definiciones del vocabulario
# --------------------------------------------------------------------------


def _definiciones(campo: str) -> str:
    valores = VOCABULARIO[campo]
    lineas = []
    for clave, definicion in valores.items():
        texto = definicion.get("definicion", "") if isinstance(definicion, dict) else ""
        lineas.append(f"`{clave}`: {texto}".strip())
    return "\n".join(lineas)


def openapi_con_vocabulario() -> dict:
    """Añade a la especificación las `definicion` de `vocabularies.yaml`.

    Un `enum` de treinta valores sin definiciones obliga al modelo a adivinar qué
    significa cada uno. Las definiciones ya están escritas: se publican.
    """
    especificacion = app.openapi()
    componentes = especificacion.setdefault("components", {}).setdefault("schemas", {})
    for campo in ("product_type", "use_case", "functional_family", "gift_risk",
                  "suitable_relationships"):
        componentes[campo] = {
            "type": "string",
            "enum": sorted(VOCABULARIO[campo]),
            "description": _definiciones(campo),
        }
    especificacion.setdefault("components", {})["securitySchemes"] = {
        "CatalogApiKey": {"type": "apiKey", "in": "header", "name": CABECERA}
    }
    especificacion["security"] = [{"CatalogApiKey": []}]
    return especificacion


app.openapi = openapi_con_vocabulario  # type: ignore[method-assign]
