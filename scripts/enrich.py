"""Calcula los campos propios de cada producto. Solo se ejecuta en CI.

**Incremental por defecto**: clasifica únicamente los productos canónicos que no
tienen entrada. **Completo cuando cambia el criterio**: si el commit ha tocado
`data/vocabularies.yaml` o `prompts/enrich.md`, reclasifica los 150 aunque no
haya un solo producto nuevo (A3.6).

Por qué esa condición no es opcional: los productos ya clasificados lo fueron con
el criterio anterior. Dejarlos intactos mezcla dos clasificaciones dentro del
mismo fichero, y **el fallo es invisible** — el artefacto sigue siendo válido,
pasa la puerta de cobertura y clasifica mal.

Este script **no viaja al contenedor**. La clave del modelo se inyecta solo aquí.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import normalization  # noqa: E402

CRITERIO = ("data/vocabularies.yaml", "prompts/enrich.md")
CAMPOS_PROPIOS = (
    "product_type",
    "functional_family",
    "use_case",
    "gift_risk",
    "suitable_relationships",
    "is_standalone_gift",
    "stocking_filler",
)


def ficheros_del_commit() -> list[str]:
    """Qué ha cambiado en este commit, según git."""
    try:
        salida = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Sin historia con la que comparar, se reclasifica todo: es el lado
        # seguro. Clasificar de más cuesta céntimos; clasificar de menos con un
        # criterio nuevo produce un fichero válido y equivocado.
        return list(CRITERIO)
    return [linea for linea in salida.stdout.splitlines() if linea]


def hay_que_reclasificar_todo(cambiados: list[str]) -> bool:
    return any(fichero in cambiados for fichero in CRITERIO)


def clasificar(producto: normalization.ProductoCanonico, prompt: str) -> dict:
    """Pide al modelo los campos propios de un producto.

    La llamada se hace aquí y solo aquí, con la clave que CI inyecta. El
    contenedor no lleva ni la clave, ni el prompt, ni este fichero.
    """
    from anthropic import Anthropic

    cliente = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    ficha = {
        "product_id": producto.product_id,
        "name": producto.name,
        "category": producto.category,
        "subcategory": producto.subcategory,
        "brand": producto.brand,
        "price_eur": producto.price,
        "recipient": producto.recipient,
        "occasion": producto.occasion,
        "tags": producto.tags,
        "color": producto.color,
        "material": producto.material,
        "description": producto.description,
    }
    respuesta = cliente.messages.create(
        model=os.environ.get("ENRICH_MODEL", "claude-sonnet-4-5"),
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": json.dumps(ficha, ensure_ascii=False)}],
    )
    texto = "".join(bloque.text for bloque in respuesta.content if bloque.type == "text")
    inicio, fin = texto.find("{"), texto.rfind("}")
    entrada = json.loads(texto[inicio : fin + 1])
    return {campo: entrada[campo] for campo in CAMPOS_PROPIOS if campo in entrada}


def main() -> int:
    argumentos = argparse.ArgumentParser(description="Campos propios de los productos")
    argumentos.add_argument("--csv", required=True)
    argumentos.add_argument("--out", required=True)
    argumentos.add_argument("--vocabularies", default="data/vocabularies.yaml")
    argumentos.add_argument("--prompt", default="prompts/enrich.md")
    opciones = argumentos.parse_args()

    salida = Path(opciones.out)
    capa = json.loads(salida.read_text(encoding="utf-8")) if salida.exists() else {}
    entradas: dict = capa.get("products", {}) if isinstance(capa, dict) else {}

    tipos_por_producto = {
        product_id: entrada.get("product_type") for product_id, entrada in entradas.items()
    }
    canonicos, _ = normalization.canonicalizar(
        opciones.csv, opciones.vocabularies, tipos_por_producto
    )

    cambiados = ficheros_del_commit()
    completo = hay_que_reclasificar_todo(cambiados)
    if completo:
        print("El commit toca el criterio: se reclasifican todos los productos.")
        pendientes = canonicos
    else:
        pendientes = [p for p in canonicos if p.product_id not in entradas]
        print(f"El criterio no ha cambiado: se clasifican {len(pendientes)} productos nuevos.")

    if pendientes:
        prompt = Path(opciones.prompt).read_text(encoding="utf-8")
        for producto in pendientes:
            entradas[producto.product_id] = clasificar(producto, prompt)
            print(f"  · {producto.product_id} {producto.name}")

    # Las entradas huérfanas se van con el producto que las justificaba.
    vigentes = {p.product_id for p in canonicos}
    entradas = {k: v for k, v in entradas.items() if k in vigentes}

    resultado = {
        "vocabulary_version": 4,
        "products": dict(sorted(entradas.items())),
    }
    # Las relaciones ya escritas no se tocan aquí: las recalcula relate.py.
    salida.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(entradas)} entradas escritas en {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
