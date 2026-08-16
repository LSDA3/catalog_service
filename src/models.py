"""Las formas que devuelve el servicio.

Tres formas base (B4.2) y **un envelope por operación** (B4.8). No hay un
envelope común: cada operación lleva sus metadatos y ningún otro, porque un
esquema universal obligaría a que todas las operaciones declararan campos que no
usan y el agente tendría que adivinar cuáles vienen de verdad.

Se usan dataclasses de la biblioteca estándar: así estas formas se pueden cargar
y comprobar sin levantar el servicio ni instalar nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# B4.3 · Product, 26 campos
# --------------------------------------------------------------------------


@dataclass
class Product:
    """La forma única de las cuatro operaciones que devuelven mercancía.

    Los 26 campos, y ni uno más: `description_quality`, `tags` y `stock` **no
    viajan** (B4.6). Su efecto ya está aplicado —el primero en el orden, el
    tercero en `in_stock`— y el segundo no participa en el proceso.
    """

    # Identidad y contenido
    product_id: str
    name: str
    description: str

    # Condiciones de compra
    price: float | None
    shipping_days: int | None
    gift_wrap: bool | None
    brand: str
    color: str
    material: str
    in_stock: bool
    is_standalone_gift: bool

    # Clasificación
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

    # Relaciones comerciales
    stocking_filler: bool
    pairs_with: list[str]
    alternative_to: list[str]


CAMPOS_DE_PRODUCT = (
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


# --------------------------------------------------------------------------
# B4.4 · ExcludedProduct
# --------------------------------------------------------------------------


@dataclass
class ExcludedProduct:
    """Un candidato relevante que una frontera de la consulta dejó fuera.

    No lleva categorías ni descripción a propósito: un producto de `excluded` no
    se puede recomendar, así que el agente no tiene que escribir su razón, solo
    nombrarlo con honestidad.
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
    """El estado actual de una categoría, no solo su nombre.

    Una categoría con cero disponibles sigue apareciendo, con su cero: el mapa de
    la tienda no es el stock.
    """

    name: str
    available_count: int
    price_min: float | None
    price_max: float | None


# --------------------------------------------------------------------------
# B4.7 y B4.8 · un envelope por operación
# --------------------------------------------------------------------------


@dataclass
class NotApplied:
    """Un criterio que llegó y no pudo aplicarse.

    Es a los criterios lo que `ExcludedProduct` es a los productos: sin él, la
    ausencia de un criterio en `query_understood` no distingue que el cliente no
    lo dijera de que lo dijera y no lo entendiéramos.
    """

    parameter: str
    received: str
    reason: str


@dataclass
class RespuestaDeCategorias:
    """`get_categories`. Sin metadatos propios."""

    results: list[CategorySummary]
    currency: str = "EUR"


@dataclass
class RespuestaDeNavegacion:
    """`get_products_by_category`. La única paginada: `total` y `offset`."""

    results: list[Product]
    total: int
    offset: int
    currency: str = "EUR"


@dataclass
class RespuestaDeBusqueda:
    """`find_products_by_criteria`.

    La única que expone `not_applied`. `excluded` y `not_applied` se omiten
    cuando están vacíos, y por eso su valor por defecto es `None` y no una lista
    vacía: ausente y vacío no son lo mismo.
    """

    results: list[Product]
    query_understood: dict[str, object]
    excluded: list[ExcludedProduct] | None = None
    not_applied: list[NotApplied] | None = None
    currency: str = "EUR"


@dataclass
class ProductoRelacionado:
    """Un elemento de `get_related_products`.

    `relation_type` describe la relación con el punto de partida, no el
    producto, y por eso no forma parte de `Product`.
    """

    product: Product
    relation_type: str | None = None


@dataclass
class RespuestaDeRelacionados:
    """`get_related_products`. Expone `excluded`, nunca `not_applied`."""

    results: list[ProductoRelacionado]
    query_understood: dict[str, object] | None = None
    excluded: list[ExcludedProduct] | None = None
    currency: str = "EUR"


@dataclass
class RespuestaDeDetalle:
    """`get_product_details`. Un único `Product`, en `result`, no en una lista."""

    result: Product
    currency: str = "EUR"


# --------------------------------------------------------------------------
# Errores recuperables y fallos técnicos · vocabularios separados (B5)
# --------------------------------------------------------------------------

ERROR_TYPE = (
    "invalid_parameter",
    "conflicting_parameters",
    "missing_anchor",
    "product_not_found",
)


@dataclass
class RespuestaRecuperable:
    """Una petición prevista que no se puede ejecutar. Viaja con HTTP 200.

    Es contenido, no transporte: por eso entra por la rama Success del API Block
    y el agente la distingue por `error_type`.
    """

    error_type: str
    detail: str = ""
    parameter: str | None = None
    product_id: str | None = None
    relation: str | None = None


@dataclass
class FalloTecnico:
    """Un fallo real del servicio. Viaja con 5xx y con su propio vocabulario."""

    error_code: str
    incident_id: str
    retryable: bool = True


METADATOS_POR_OPERACION: dict[str, tuple[str, ...]] = {
    "get_categories": (),
    "get_products_by_category": ("total", "offset"),
    "find_products_by_criteria": ("query_understood", "excluded", "not_applied"),
    "get_related_products": ("relation_type", "query_understood", "excluded"),
    "get_product_details": (),
}

LIMITES_POR_OPERACION: dict[str, tuple[int, int, int]] = {
    # operación: (mínimo, máximo, por defecto)
    "get_products_by_category": (1, 8, 8),
    "find_products_by_criteria": (1, 8, 8),
    "get_related_products": (1, 5, 3),
}

MAXIMO_ABSOLUTO = 8
