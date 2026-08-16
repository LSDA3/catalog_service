"""El protocolo del catálogo.

Es lo que permite que el resto del código no sepa que detrás hay un CSV. Hoy la
implementación lee tres ficheros y los mantiene en memoria; si mañana el catálogo
llegara de otro sitio, cambia esta pieza y nada más.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import loader
from models import Product


class CatalogRepository(Protocol):
    """Lo que el servicio necesita saber pedir del catálogo."""

    def todos(self) -> list[Product]:
        """Los 150 productos canónicos."""

    def por_id(self, product_id: str) -> Product | None:
        """Un producto por su identificador canónico o por uno absorbido."""

    def fuera_del_contrato(self, product_id: str) -> dict:
        """`description_quality`, `tags`, `stock` y `alt_product_ids` (B4.6)."""

    def tipo_de_relacion(self, origen: str, destino: str) -> str | None:
        """`equivalent` o `same_function` para un vínculo explícito."""

    def categorias(self) -> list[str]:
        """Los nombres normalizados de las categorías del catálogo."""


class CatalogoEnMemoria:
    """La implementación de hoy: los tres ficheros, cargados una vez al arrancar.

    No hay base de datos y no hace falta: 150 productos caben de sobra en memoria,
    y cada llamada sigue siendo una función pura de sus parámetros.
    """

    def __init__(
        self,
        ruta_csv: str | Path,
        ruta_vocabularios: str | Path,
        ruta_capa_semantica: str | Path,
    ) -> None:
        productos, fuera_del_contrato, tipos_de_relacion = loader.cargar(
            ruta_csv, ruta_vocabularios, ruta_capa_semantica
        )
        self._productos = productos
        self._fuera_del_contrato = fuera_del_contrato
        self._tipos_de_relacion = tipos_de_relacion
        self._por_id: dict[str, Product] = {p.product_id: p for p in productos}
        for product_id, datos in fuera_del_contrato.items():
            for absorbido in datos["alt_product_ids"]:
                self._por_id[absorbido] = self._por_id[product_id]

    def todos(self) -> list[Product]:
        return list(self._productos)

    def por_id(self, product_id: str) -> Product | None:
        return self._por_id.get(product_id)

    def fuera_del_contrato(self, product_id: str) -> dict:
        return self._fuera_del_contrato[product_id]

    def tipo_de_relacion(self, origen: str, destino: str) -> str | None:
        return self._tipos_de_relacion.get((origen, destino))

    def categorias(self) -> list[str]:
        return sorted({producto.category for producto in self._productos})
