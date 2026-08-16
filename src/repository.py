"""The catalog protocol.

It is what lets the rest of the code not know there is a CSV behind. Today the
implementation reads three files and keeps them in memory; if tomorrow the
catalog came from somewhere else, this piece changes and nothing else does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import loader
from models import Product


class CatalogRepository(Protocol):
    """What the service needs to know how to ask of the catalog."""

    def all_products(self) -> list[Product]:
        """The 150 canonical products."""

    def by_id(self, product_id: str) -> Product | None:
        """A product by its canonical identifier or by an absorbed one."""

    def off_contract(self, product_id: str) -> dict:
        """`description_quality`, `tags`, `stock` and `alt_product_ids` (B4.6)."""

    def relation_type_of(self, source: str, target: str) -> str | None:
        """`equivalent` or `same_function` for an explicit link."""

    def categories(self) -> list[str]:
        """The normalized names of the catalog categories."""


class InMemoryCatalog:
    """Today's implementation: the three files, loaded once at start-up.

    There is no database and none is needed: 150 products fit in memory with room
    to spare, and every call remains a pure function of its parameters.
    """

    def __init__(
        self,
        csv_path: str | Path,
        vocabularies_path: str | Path,
        semantic_layer_path: str | Path,
    ) -> None:
        products, off_contract, relation_types = loader.load(
            csv_path, vocabularies_path, semantic_layer_path
        )
        self._products = products
        self._off_contract = off_contract
        self._relation_types = relation_types
        self._by_id: dict[str, Product] = {p.product_id: p for p in products}
        for product_id, data in off_contract.items():
            for absorbed in data["alt_product_ids"]:
                self._by_id[absorbed] = self._by_id[product_id]

    def all_products(self) -> list[Product]:
        return list(self._products)

    def by_id(self, product_id: str) -> Product | None:
        return self._by_id.get(product_id)

    def off_contract(self, product_id: str) -> dict:
        return self._off_contract[product_id]

    def relation_type_of(self, source: str, target: str) -> str | None:
        return self._relation_types.get((source, target))

    def categories(self) -> list[str]:
        return sorted({product.category for product in self._products})
