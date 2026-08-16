"""Recalcula las relations sobre el catálogo full_run. Solo se ejecuta en CI.

**Nunca es incremental**, y no por comodidad: una relación necesita conocer el
catálogo entero, y un product nuevo puede obligar a revisar las de products que
ya estaban. Si entra un cuchillo de chef nuevo, la piedra de afilar que hoy
apunta al viejo puede tener que apuntar a los dos.

Escribe cada relación **una sola vez**, bajo el `product_id` lexicográficamente
smaller de la pair. Que el other extremo la conozca es trabajo del loader.

Este script **no viaja al contenedor**.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import normalization  # noqa: E402

RELATION_TYPE = {"equivalent", "same_function"}


def _card(product: normalization.CanonicalProduct, entrada: dict) -> dict:
    """Lo que el modelo necesita ver de cada product para relacionarlo."""
    return {
        "product_id": product.product_id,
        "name": product.name,
        "product_type": entrada.get("product_type"),
        "functional_family": entrada.get("functional_family"),
        "price_eur": product.price,
        "description": product.description,
    }


def request_relations(catalog: list[dict], prompt: str) -> dict:
    from anthropic import Anthropic

    client_ = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client_.messages.create(
        model=os.environ.get("RELATE_MODEL", "claude-sonnet-4-5"),
        max_tokens=8192,
        system=prompt,
        messages=[{"role": "user", "content": json.dumps(catalog, ensure_ascii=False)}],
    )
    text_ = "".join(bloque.text for bloque in response.content if bloque.type == "text")
    start_, end_ = text_.find("{"), text_.rfind("}")
    return json.loads(text_[start_ : end_ + 1])


def normalize_relations(proposals: dict, canonicos: set[str]) -> dict[str, dict]:
    """Deja una sola escritura por pair, bajo el identificador smaller.

    Lo que llega puede traer la misma pair en los dos sentidos o repetida: aquí
    se reduce a la forma que la puerta de cobertura exige, sin puntuar nada y sin
    inventar ninguna relación que no venga propuesta.
    """
    pairs: dict[tuple[str, str], str] = {}
    complements: set[tuple[str, str]] = set()

    for product_id, propuesta in proposals.items():
        if product_id not in canonicos:
            continue
        for link in propuesta.get("pairs_with") or []:
            other = link["product_id"] if isinstance(link, dict) else link
            if other in canonicos and other != product_id:
                complements.add(tuple(sorted((product_id, other))))
        for link in propuesta.get("alternative_to") or []:
            if isinstance(link, dict):
                other = link.get("product_id")
                kind = link.get("relation_type", "same_function")
            else:
                other, kind = link, "same_function"
            if other not in canonicos or other == product_id:
                continue
            if kind not in RELATION_TYPE:
                kind = "same_function"  # ante la duda, la etiqueta segura
            pair = tuple(sorted((product_id, other)))
            # Si los dos extremos discrepan, manda la más conservadora.
            previous = pairs.get(pair)
            pairs[pair] = (
                "equivalent" if previous == "equivalent" and kind == "equivalent" else
                kind if previous is None else
                ("equivalent" if previous == kind == "equivalent" else "same_function")
            )

    relations: dict[str, dict] = {
        product_id: {"pairs_with": [], "alternative_to": []} for product_id in canonicos
    }
    for smaller, larger in sorted(complements):
        relations[smaller]["pairs_with"].append(larger)
    for (smaller, larger), kind in sorted(pairs.items()):
        relations[smaller]["alternative_to"].append(
            {"product_id": larger, "relation_type": kind}
        )
    return relations


def main() -> int:
    parser = argparse.ArgumentParser(description="Relaciones del catálogo full_run")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--semantic", required=True)
    parser.add_argument("--vocabularies", default="data/vocabularies.yaml")
    parser.add_argument("--prompt", default="prompts/relate.md")
    options = parser.parse_args()

    path_ = Path(options.semantic)
    layer = json.loads(path_.read_text(encoding="utf-8"))
    entries: dict = layer["products"]

    product_types_by_id = {
        product_id: entrada.get("product_type") for product_id, entrada in entries.items()
    }
    canonicos, _ = normalization.canonicalize(
        options.csv, options.vocabularies, product_types_by_id
    )

    catalog = [_card(p, entries.get(p.product_id, {})) for p in canonicos]
    prompt = Path(options.prompt).read_text(encoding="utf-8")
    proposals = request_relations(catalog, prompt)

    relations = normalize_relations(proposals, {p.product_id for p in canonicos})
    for product_id, entrada in entries.items():
        entrada["pairs_with"] = relations[product_id]["pairs_with"]
        entrada["alternative_to"] = relations[product_id]["alternative_to"]

    layer["products"] = dict(sorted(entries.items()))
    path_.write_text(json.dumps(layer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    written = sum(
        len(e["pairs_with"]) + len(e["alternative_to"]) for e in entries.values()
    )
    print(f"{written} relations written sobre {len(entries)} products")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
