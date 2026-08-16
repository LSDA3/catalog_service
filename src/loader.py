"""Construye el modelo canónico en memoria al arrancar el servicio.

Hace tres cosas, y ninguna más:

1. Llama a `normalization.py` sobre el CSV y recibe los productos canónicos.
2. Lee `semantic_layer.json` y une cada entrada con su producto canónico.
3. Construye el modelo en memoria que consume el resto del servicio.

**No transforma nada por su cuenta.** Si aquí aparece una regla de limpieza del
CSV, está duplicada: su sitio es `normalization.py`. Dos implementaciones que se
separen producen dos catálogos distintos, y la puerta de cobertura de A3.4
dejaría de significar nada.
"""

from __future__ import annotations

import json
from pathlib import Path

import normalization
from models import Product


class CapaSemanticaIncompleta(Exception):
    """El artefacto derivado no cubre exactamente el catálogo canónico.

    Es el invariante de A3.4 visto desde el arranque: si los dos conjuntos no
    coinciden, el servicio no puede responder por los productos que faltan y
    tampoco puede inventarles clasificación.
    """


def _resolver_relaciones_inversas(
    entradas: dict[str, dict], campo: str
) -> dict[str, list[str]]:
    """Devuelve el campo de relación resuelto desde los dos extremos.

    Una relación se persiste **una sola vez**, bajo el `product_id` menor. Que el
    otro extremo la conozca es trabajo del loader, no del fichero: duplicarla en
    el artefacto abriría la puerta a que los dos lados dejaran de coincidir.
    """
    resuelto: dict[str, list[str]] = {clave: [] for clave in entradas}
    for product_id, entrada in entradas.items():
        for vinculo in entrada.get(campo) or []:
            otro = vinculo["product_id"] if isinstance(vinculo, dict) else vinculo
            if otro not in resuelto:
                continue
            if otro not in resuelto[product_id]:
                resuelto[product_id].append(otro)
            if product_id not in resuelto[otro]:
                resuelto[otro].append(product_id)
    return {clave: sorted(valores) for clave, valores in resuelto.items()}


def _tipo_de_relacion(entradas: dict[str, dict]) -> dict[tuple[str, str], str]:
    """El `relation_type` de cada vínculo `alternative_to`, en los dos sentidos.

    Viaja con la relación, no con el producto (B4.3), así que se guarda aparte y
    no entra en `Product`.
    """
    tipos: dict[tuple[str, str], str] = {}
    for product_id, entrada in entradas.items():
        for vinculo in entrada.get("alternative_to") or []:
            if not isinstance(vinculo, dict):
                continue
            otro = vinculo["product_id"]
            clase = vinculo.get("relation_type", "same_function")
            tipos[(product_id, otro)] = clase
            tipos[(otro, product_id)] = clase
    return tipos


def cargar(
    ruta_csv: str | Path,
    ruta_vocabularios: str | Path,
    ruta_capa_semantica: str | Path,
) -> tuple[list[Product], dict[str, dict], dict[tuple[str, str], str]]:
    """Devuelve los productos, los campos que no viajan y los tipos de relación.

    Lo segundo son los datos que el servicio necesita y que **no forman parte de
    `Product`** (B4.6): `description_quality`, que ya actuó en el orden; `tags`,
    que no participa; `stock`, cuya cantidad no aporta conducta; y
    `alt_product_ids`, que resuelve identificadores absorbidos.
    """
    capa = json.loads(Path(ruta_capa_semantica).read_text(encoding="utf-8"))
    entradas = capa["products"] if isinstance(capa, dict) and "products" in capa else capa
    if isinstance(entradas, list):
        entradas = {entrada["product_id"]: entrada for entrada in entradas}

    tipos_por_producto = {
        product_id: entrada["product_type"] for product_id, entrada in entradas.items()
    }

    canonicos, _avisos = normalization.canonicalizar(
        ruta_csv, ruta_vocabularios, tipos_por_producto
    )

    identificadores_del_csv = {producto.product_id for producto in canonicos}
    if identificadores_del_csv != set(entradas):
        faltan = sorted(identificadores_del_csv - set(entradas))
        sobran = sorted(set(entradas) - identificadores_del_csv)
        raise CapaSemanticaIncompleta(
            f"sin entrada semántica: {faltan or 'ninguno'}; "
            f"sin producto en el catálogo: {sobran or 'ninguno'}"
        )

    pairs_with = _resolver_relaciones_inversas(entradas, "pairs_with")
    alternative_to = _resolver_relaciones_inversas(entradas, "alternative_to")
    tipos_de_relacion = _tipo_de_relacion(entradas)

    productos: list[Product] = []
    fuera_del_contrato: dict[str, dict] = {}

    for canonico in canonicos:
        entrada = entradas[canonico.product_id]
        productos.append(
            Product(
                product_id=canonico.product_id,
                name=canonico.name,
                description=canonico.description,
                price=canonico.price,
                shipping_days=canonico.shipping_days,
                gift_wrap=canonico.gift_wrap,
                brand=canonico.brand,
                color=canonico.color,
                material=canonico.material,
                in_stock=canonico.in_stock,
                is_standalone_gift=bool(entrada.get("is_standalone_gift")),
                category=canonico.category,
                secondary_categories=canonico.secondary_categories,
                subcategory=canonico.subcategory,
                product_type=entrada["product_type"],
                functional_family=list(entrada.get("functional_family") or []),
                use_case=list(entrada.get("use_case") or []),
                occasion=canonico.occasion,
                recipient=canonico.recipient,
                suitable_relationships=list(entrada.get("suitable_relationships") or []),
                gift_risk=entrada.get("gift_risk", ""),
                rating=canonico.rating,
                reviews_count=canonico.reviews_count,
                stocking_filler=bool(entrada.get("stocking_filler")),
                pairs_with=pairs_with[canonico.product_id],
                alternative_to=alternative_to[canonico.product_id],
            )
        )
        fuera_del_contrato[canonico.product_id] = {
            "description_quality": canonico.description_quality,
            "tags": canonico.tags,
            "stock": canonico.stock,
            "alt_product_ids": canonico.alt_product_ids,
        }

    return productos, fuera_del_contrato, tipos_de_relacion
