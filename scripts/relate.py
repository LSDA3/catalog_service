"""Recalcula las relaciones sobre el catálogo completo. Solo se ejecuta en CI.

**Nunca es incremental**, y no por comodidad: una relación necesita conocer el
catálogo entero, y un producto nuevo puede obligar a revisar las de productos que
ya estaban. Si entra un cuchillo de chef nuevo, la piedra de afilar que hoy
apunta al viejo puede tener que apuntar a los dos.

Escribe cada relación **una sola vez**, bajo el `product_id` lexicográficamente
menor de la pareja. Que el otro extremo la conozca es trabajo del loader.

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


def _ficha(producto: normalization.ProductoCanonico, entrada: dict) -> dict:
    """Lo que el modelo necesita ver de cada producto para relacionarlo."""
    return {
        "product_id": producto.product_id,
        "name": producto.name,
        "product_type": entrada.get("product_type"),
        "functional_family": entrada.get("functional_family"),
        "price_eur": producto.price,
        "description": producto.description,
    }


def pedir_relaciones(catalogo: list[dict], prompt: str) -> dict:
    from anthropic import Anthropic

    cliente = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    respuesta = cliente.messages.create(
        model=os.environ.get("RELATE_MODEL", "claude-sonnet-4-5"),
        max_tokens=8192,
        system=prompt,
        messages=[{"role": "user", "content": json.dumps(catalogo, ensure_ascii=False)}],
    )
    texto = "".join(bloque.text for bloque in respuesta.content if bloque.type == "text")
    inicio, fin = texto.find("{"), texto.rfind("}")
    return json.loads(texto[inicio : fin + 1])


def normalizar_relaciones(propuestas: dict, canonicos: set[str]) -> dict[str, dict]:
    """Deja una sola escritura por pareja, bajo el identificador menor.

    Lo que llega puede traer la misma pareja en los dos sentidos o repetida: aquí
    se reduce a la forma que la puerta de cobertura exige, sin puntuar nada y sin
    inventar ninguna relación que no venga propuesta.
    """
    parejas: dict[tuple[str, str], str] = {}
    complementos: set[tuple[str, str]] = set()

    for product_id, propuesta in propuestas.items():
        if product_id not in canonicos:
            continue
        for vinculo in propuesta.get("pairs_with") or []:
            otro = vinculo["product_id"] if isinstance(vinculo, dict) else vinculo
            if otro in canonicos and otro != product_id:
                complementos.add(tuple(sorted((product_id, otro))))
        for vinculo in propuesta.get("alternative_to") or []:
            if isinstance(vinculo, dict):
                otro = vinculo.get("product_id")
                clase = vinculo.get("relation_type", "same_function")
            else:
                otro, clase = vinculo, "same_function"
            if otro not in canonicos or otro == product_id:
                continue
            if clase not in RELATION_TYPE:
                clase = "same_function"  # ante la duda, la etiqueta segura
            pareja = tuple(sorted((product_id, otro)))
            # Si los dos extremos discrepan, manda la más conservadora.
            anterior = parejas.get(pareja)
            parejas[pareja] = (
                "equivalent" if anterior == "equivalent" and clase == "equivalent" else
                clase if anterior is None else
                ("equivalent" if anterior == clase == "equivalent" else "same_function")
            )

    relaciones: dict[str, dict] = {
        product_id: {"pairs_with": [], "alternative_to": []} for product_id in canonicos
    }
    for menor, mayor in sorted(complementos):
        relaciones[menor]["pairs_with"].append(mayor)
    for (menor, mayor), clase in sorted(parejas.items()):
        relaciones[menor]["alternative_to"].append(
            {"product_id": mayor, "relation_type": clase}
        )
    return relaciones


def main() -> int:
    argumentos = argparse.ArgumentParser(description="Relaciones del catálogo completo")
    argumentos.add_argument("--csv", required=True)
    argumentos.add_argument("--semantic", required=True)
    argumentos.add_argument("--vocabularies", default="data/vocabularies.yaml")
    argumentos.add_argument("--prompt", default="prompts/relate.md")
    opciones = argumentos.parse_args()

    ruta = Path(opciones.semantic)
    capa = json.loads(ruta.read_text(encoding="utf-8"))
    entradas: dict = capa["products"]

    tipos_por_producto = {
        product_id: entrada.get("product_type") for product_id, entrada in entradas.items()
    }
    canonicos, _ = normalization.canonicalizar(
        opciones.csv, opciones.vocabularies, tipos_por_producto
    )

    catalogo = [_ficha(p, entradas.get(p.product_id, {})) for p in canonicos]
    prompt = Path(opciones.prompt).read_text(encoding="utf-8")
    propuestas = pedir_relaciones(catalogo, prompt)

    relaciones = normalizar_relaciones(propuestas, {p.product_id for p in canonicos})
    for product_id, entrada in entradas.items():
        entrada["pairs_with"] = relaciones[product_id]["pairs_with"]
        entrada["alternative_to"] = relaciones[product_id]["alternative_to"]

    capa["products"] = dict(sorted(entradas.items()))
    ruta.write_text(json.dumps(capa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    escritas = sum(
        len(e["pairs_with"]) + len(e["alternative_to"]) for e in entradas.values()
    )
    print(f"{escritas} relaciones escritas sobre {len(entradas)} productos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
