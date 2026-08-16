"""Canonicalización determinista del catálogo.

Esta es la única implementación de la transformación descrita en A2.2.
La consumen `loader.py` en tiempo de ejecución y `enrich.py`, `relate.py` y
`validate_semantic.py` en construcción. Nadie la reescribe: dos implementaciones
que se separen producen dos catálogos distintos, y la puerta de cobertura de
A3.4 dejaría de significar nada.

No inventa datos. Lo que no está, no está: un precio ausente deja el producto
sin precio, una nota ausente se queda ausente y nunca vale cero. Ante un valor
genuinamente ambiguo se detiene indicando fila, columna y valor.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class CatalogoAmbiguo(Exception):
    """El fichero trae un valor que no se puede leer sin suponer.

    Se prefiere detener el arranque a acertar el 80 % en silencio (A2.2).
    """

    def __init__(self, fila: int, columna: str, valor: str, motivo: str) -> None:
        super().__init__(
            f"fila {fila}, columna «{columna}», valor {valor!r}: {motivo}"
        )
        self.fila = fila
        self.columna = columna
        self.valor = valor
        self.motivo = motivo


# --------------------------------------------------------------------------
# Aviso de calidad
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Aviso:
    """Un hecho observado en el fichero que conviene reportar (A2.3)."""

    clase: str
    product_id: str
    detalle: str


# --------------------------------------------------------------------------
# El producto canónico
# --------------------------------------------------------------------------


@dataclass
class ProductoCanonico:
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
# Normalización de valores
# --------------------------------------------------------------------------

_DIVISA = re.compile(r"(?i)(^\s*(eur|€)\s*|\s*(eur|€)\s*$)")
_SOLO_DIGITOS = re.compile(r"^\d+$")
_DECIMAL_PUNTO = re.compile(r"^\d+\.\d+$")
_DECIMAL_COMA = re.compile(r"^\d+,\d{2}$")
_EUROPEO = re.compile(r"^\d{1,3}(\.\d{3})+,\d{2}$")
_MILES_AMBIGUO = re.compile(r"^\d+,\d{3}$")


def normalizar_precio(bruto: str, fila: int) -> float | None:
    """Devuelve el precio en euros, o `None` si el fichero no lo trae.

    Retirar el símbolo de la divisa o cambiar la coma decimal por un punto es
    normalizar un formato. Rellenar lo que no está sería inventar (A2.2).
    """
    valor = (bruto or "").strip()
    if not valor:
        return None

    valor = _DIVISA.sub("", valor).strip()

    if _SOLO_DIGITOS.match(valor) or _DECIMAL_PUNTO.match(valor):
        numero = float(valor)
    elif _DECIMAL_COMA.match(valor):
        numero = float(valor.replace(",", "."))
    elif _EUROPEO.match(valor):
        numero = float(valor.replace(".", "").replace(",", "."))
    elif _MILES_AMBIGUO.match(valor):
        raise CatalogoAmbiguo(
            fila, "price_eur", bruto, "no se puede saber si es separador de miles o decimal"
        )
    else:
        raise CatalogoAmbiguo(fila, "price_eur", bruto, "formato de precio no reconocido")

    if numero <= 0:
        raise CatalogoAmbiguo(
            fila, "price_eur", bruto, "un catálogo de regalos no tiene precios nulos ni negativos"
        )
    return round(numero, 2)


_SI_HAY = {"yes", "y", "true", "available", "in stock", "sí", "si"}
_NO_HAY = {"no", "n", "false", "unavailable", "out of stock", "sold out"}


def normalizar_stock(bruto: str, fila: int) -> tuple[int | None, bool]:
    """Devuelve la cantidad y la disponibilidad.

    Un `yes` establece con certeza que hay existencias pero no cuántas, así que
    la cantidad queda a `None` y la disponibilidad a `True` (A2.2).
    """
    valor = (bruto or "").strip()
    if not valor:
        raise CatalogoAmbiguo(fila, "stock", bruto, "sin existencias declaradas")

    if _SOLO_DIGITOS.match(valor):
        cantidad = int(valor)
        return cantidad, cantidad > 0

    plano = valor.lower()
    if plano in _SI_HAY:
        return None, True
    if plano in _NO_HAY:
        return None, False

    raise CatalogoAmbiguo(fila, "stock", bruto, "no dice ni cantidad ni disponibilidad")


def normalizar_categoria(bruto: str) -> str:
    """Recorta espacios, unifica mayúsculas y equipara `and` con `&`.

    17 valores literales del fichero corresponden a 11 categorías reales.
    """
    valor = " ".join((bruto or "").split())
    valor = re.sub(r"(?i)\band\b", "&", valor)
    valor = " ".join(valor.split())
    return " ".join(palabra.capitalize() if palabra != "&" else "&" for palabra in valor.split())


def _lista(bruto: str, separador: str = "|") -> list[str]:
    return [parte.strip() for parte in (bruto or "").split(separador) if parte.strip()]


def _booleano(bruto: str, fila: int, columna: str) -> bool | None:
    valor = (bruto or "").strip().lower()
    if not valor:
        return None
    if valor in _SI_HAY:
        return True
    if valor in _NO_HAY:
        return False
    raise CatalogoAmbiguo(fila, columna, bruto, "no es un valor booleano reconocible")


def _entero(bruto: str, fila: int, columna: str) -> int | None:
    valor = (bruto or "").strip()
    if not valor:
        return None
    if not _SOLO_DIGITOS.match(valor):
        raise CatalogoAmbiguo(fila, columna, bruto, "no es un entero")
    return int(valor)


def _decimal(bruto: str, fila: int, columna: str) -> float | None:
    valor = (bruto or "").strip()
    if not valor:
        return None
    try:
        return float(valor.replace(",", "."))
    except ValueError as error:
        raise CatalogoAmbiguo(fila, columna, bruto, "no es un número") from error


# --------------------------------------------------------------------------
# Calidad de la descripción
# --------------------------------------------------------------------------

_MINIMO_DESCRIPCION = 25


def calidad_de_descripcion(descripcion: str) -> str:
    """`poor` cuando la descripción no permite construir una razón (A2.2)."""
    limpia = " ".join((descripcion or "").split())
    if len(limpia) < _MINIMO_DESCRIPCION:
        return "poor"
    return "ok"


# --------------------------------------------------------------------------
# Apertura de recipient
# --------------------------------------------------------------------------


def abrir_recipient(original: list[str], product_type: str | None, exclusivos: set[str]) -> list[str]:
    """Añade `anyone` a todo producto que pueda llevarlo (A2.2).

    Conserva el valor original: el teclado mecánico queda como `him` **y**
    `anyone`. Solo se quedan sin `anyone` lo marcado `kids` y lo que el
    vocabulario declara exclusivo de un género con `gender_specific`.
    """
    valores = list(original)
    if "kids" in valores:
        return ["kids"]
    if product_type in exclusivos:
        return valores
    if "anyone" not in valores:
        valores.append("anyone")
    return valores


def tipos_exclusivos_de_genero(ruta_vocabularios: Path) -> set[str]:
    vocabulario = yaml.safe_load(Path(ruta_vocabularios).read_text(encoding="utf-8"))
    tipos = vocabulario.get("product_type", {})
    return {
        clave
        for clave, definicion in tipos.items()
        if isinstance(definicion, dict) and definicion.get("gender_specific")
    }


# --------------------------------------------------------------------------
# Fusión de duplicados
# --------------------------------------------------------------------------


def _huella(fila: dict[str, str]) -> tuple[str, str, str]:
    """Nombre normalizado + precio + descripción, que es la regla de A2.2."""
    nombre = " ".join((fila.get("name") or "").split()).lower()
    precio = (fila.get("price_eur") or "").strip()
    descripcion = " ".join((fila.get("description") or "").split()).lower()
    return nombre, precio, descripcion


# --------------------------------------------------------------------------
# La canonicalización completa
# --------------------------------------------------------------------------


def canonicalizar(
    ruta_csv: str | Path,
    ruta_vocabularios: str | Path,
    tipos_por_producto: dict[str, str] | None = None,
) -> tuple[list[ProductoCanonico], list[Aviso]]:
    """Lee el CSV y devuelve los productos canónicos y los avisos de calidad.

    `tipos_por_producto` asocia cada `product_id` con su `product_type`. Lo
    aporta quien tenga la capa semántica delante; sin él, la apertura de
    `recipient` no puede reconocer los exclusivos de género y se detiene, que es
    preferible a abrirlos por descuido.
    """
    exclusivos = tipos_exclusivos_de_genero(Path(ruta_vocabularios))
    tipos_por_producto = tipos_por_producto or {}

    with Path(ruta_csv).open(encoding="utf-8-sig", newline="") as fichero:
        filas = list(csv.DictReader(fichero))

    avisos: list[Aviso] = []
    grupos: dict[tuple[str, str, str], list[tuple[int, dict[str, str]]]] = {}
    for indice, fila in enumerate(filas, start=2):  # la 1 es la cabecera
        grupos.setdefault(_huella(fila), []).append((indice, fila))

    canonicos: list[ProductoCanonico] = []
    for grupo in grupos.values():
        ordenado = sorted(grupo, key=lambda par: par[1]["product_id"])
        numero_de_fila, principal = ordenado[0]
        absorbidos = ordenado[1:]

        precio = normalizar_precio(principal.get("price_eur", ""), numero_de_fila)
        cantidad, disponible = normalizar_stock(principal.get("stock", ""), numero_de_fila)
        categoria = normalizar_categoria(principal.get("category", ""))
        product_id = principal["product_id"]

        recipient_original = _lista(principal.get("recipient", ""))
        recipient = abrir_recipient(
            recipient_original, tipos_por_producto.get(product_id), exclusivos
        )

        descripcion = (principal.get("description") or "").strip()

        producto = ProductoCanonico(
            product_id=product_id,
            alt_product_ids=[fila["product_id"] for _, fila in absorbidos],
            name=(principal.get("name") or "").strip(),
            category=categoria,
            secondary_categories=sorted(
                {
                    normalizar_categoria(fila.get("category", ""))
                    for _, fila in absorbidos
                    if normalizar_categoria(fila.get("category", "")) != categoria
                }
            ),
            subcategory=(principal.get("subcategory") or "").strip(),
            brand=(principal.get("brand") or "").strip(),
            price=precio,
            stock=cantidad,
            in_stock=disponible,
            recipient=recipient,
            occasion=_lista(principal.get("occasion", "")),
            tags=_lista(principal.get("tags", "")),
            color=(principal.get("color") or "").strip(),
            material=(principal.get("material") or "").strip(),
            gift_wrap=_booleano(principal.get("gift_wrap", ""), numero_de_fila, "gift_wrap"),
            shipping_days=_entero(
                principal.get("shipping_days", ""), numero_de_fila, "shipping_days"
            ),
            description=descripcion,
            description_quality=calidad_de_descripcion(descripcion),
            rating=_decimal(principal.get("rating", ""), numero_de_fila, "rating"),
            reviews_count=_entero(
                principal.get("reviews_count", ""), numero_de_fila, "reviews_count"
            ),
        )
        canonicos.append(producto)

        for _, fila in absorbidos:
            avisos.append(
                Aviso("duplicado", product_id, f"absorbe {fila['product_id']}")
            )
        if precio is None:
            avisos.append(Aviso("sin_precio", product_id, "el fichero no trae precio"))
        if producto.rating is None:
            avisos.append(Aviso("sin_valoracion", product_id, "el fichero no trae nota"))
        if not producto.occasion:
            avisos.append(Aviso("sin_ocasion", product_id, "el fichero no trae ocasión"))
        if producto.description_quality == "poor":
            avisos.append(
                Aviso("descripcion_pobre", product_id, "no permite construir una razón")
            )

    canonicos.sort(key=lambda producto: producto.product_id)
    return canonicos, avisos


def resolver_identificador(
    identificador: str, canonicos: list[ProductoCanonico]
) -> ProductoCanonico | None:
    """Devuelve el producto canónico de un `product_id` o de un `alt_product_id`.

    Un identificador absorbido no es un producto que no existe: resuelve al
    canónico y no produce `product_not_found`.
    """
    for producto in canonicos:
        if identificador == producto.product_id or identificador in producto.alt_product_ids:
            return producto
    return None
