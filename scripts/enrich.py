"""Calcula los campos propios de cada product. Solo se ejecuta en CI.

**Incremental por defecto**: clasifica únicamente los products canónicos que no
tienen entrada. **Completo cuando cambia el criterio**: si el commit ha tocado
`data/vocabularies.yaml` o `prompts/enrich.md`, reclasifica los 150 aunque no
haya un solo product nuevo (A3.6).

Por qué esa condición no es opcional: los products ya clasificados lo fueron con
el criterio previous. Dejarlos intactos mezcla dos clasificaciones inside del
mismo fichero, y **el fallo es invisible** — el artefacto sigue siendo válido,
pasa la puerta de cobertura y clasifica mal.

Este script **no viaja al contenedor**. La key_ del modelo se inyecta solo aquí.
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

CRITERION_FILES = ("data/vocabularies.yaml", "prompts/enrich.md")
OWN_FIELDS = (
    "product_type",
    "functional_family",
    "use_case",
    "gift_risk",
    "suitable_relationships",
    "is_standalone_gift",
    "stocking_filler",
)


def files_in_commit() -> list[str]:
    """Qué ha cambiado en este commit, según git."""
    try:
        output = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Sin historia con la que comparar, se reclasifica todo: es el lado
        # seguro. Clasificar de más cuesta céntimos; classify de menos con un
        # criterio nuevo produce un fichero válido y equivocado.
        return list(CRITERION_FILES)
    return [linea for linea in output.stdout.splitlines() if linea]


def criterion_changed(changed: list[str]) -> bool:
    return any(fichero in changed for fichero in CRITERION_FILES)


def vocabulary_for_the_prompt(vocabularies_path: str | Path) -> str:
    """Los valores permitidos y sus definiciones, desde la fuente única.

    El prompt describe **el criterio**; el vocabulary aporta **los valores**. Si
    no se le dan al clasificador, inventa etiquetas que la puerta de cobertura
    rechazará después, y el pipeline falla por un motivo evitable.

    `product_type` se le da como referencia, no como lista cerrada: es el único
    vocabulary que crece, y el prompt le dice cuándo proponer uno nuevo.
    """
    import yaml

    vocabulary = yaml.safe_load(Path(vocabularies_path).read_text(encoding="utf-8"))
    blocks = [f"# Vocabulario, versión {vocabulary.get('version')}"]
    for field in ("use_case", "functional_family", "gift_risk", "suitable_relationships"):
        blocks.append(f"\n## `{field}` — vocabulary cerrado, elige solo de esta lista\n")
        for key_, definition in vocabulary[field].items():
            text_ = definition.get("definicion", "") if isinstance(definition, dict) else ""
            blocks.append(f"- `{key_}`: {text_}")

    blocks.append(
        "\n## `product_type` — vocabulary controlado que **puede crecer**\n"
        "Usa uno de los existentes si describe el objeto. Si ninguno encaja, propone "
        "uno nuevo en minúsculas y con guiones bajos, con su definición y sus aliases_.\n"
    )
    for key_, definition in vocabulary["product_type"].items():
        text_ = definition.get("definicion", "") if isinstance(definition, dict) else ""
        aliases_ = definition.get("aliases", []) if isinstance(definition, dict) else []
        suffix = f" (aliases_: {', '.join(aliases_)})" if aliases_ else ""
        blocks.append(f"- `{key_}`: {text_}{suffix}")

    return "\n".join(blocks)


def classify(product: normalization.CanonicalProduct, prompt: str) -> dict:
    """Pide al modelo los campos propios de un product.

    La llamada se hace aquí y solo aquí, con la key_ que CI inyecta. El
    contenedor no lleva ni la key_, ni el prompt, ni este fichero.
    """
    from anthropic import Anthropic

    client_ = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    ficha = {
        "product_id": product.product_id,
        "name": product.name,
        "category": product.category,
        "subcategory": product.subcategory,
        "brand": product.brand,
        "price_eur": product.price,
        "recipient": product.recipient,
        "occasion": product.occasion,
        "tags": product.tags,
        "color": product.color,
        "material": product.material,
        "description": product.description,
    }
    response = client_.messages.create(
        model=os.environ.get("ENRICH_MODEL", "claude-sonnet-4-5"),
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": json.dumps(ficha, ensure_ascii=False)}],
    )
    text_ = "".join(bloque.text for bloque in response.content if bloque.type == "text")
    start_, end_ = text_.find("{"), text_.rfind("}")
    entrada = json.loads(text_[start_ : end_ + 1])
    return {field: entrada[field] for field in OWN_FIELDS if field in entrada}


def main() -> int:
    parser = argparse.ArgumentParser(description="Campos propios de los products")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--vocabularies", default="data/vocabularies.yaml")
    parser.add_argument("--prompt", default="prompts/enrich.md")
    options = parser.parse_args()

    output = Path(options.out)
    layer = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    entries: dict = layer.get("products", {}) if isinstance(layer, dict) else {}

    product_types_by_id = {
        product_id: entrada.get("product_type") for product_id, entrada in entries.items()
    }
    canonicos, _ = normalization.canonicalize(
        options.csv, options.vocabularies, product_types_by_id
    )

    changed = files_in_commit()
    full_run = criterion_changed(changed)
    if full_run:
        print("El commit toca el criterio: se reclasifican all_products los products.")
        pending = canonicos
    else:
        pending = [p for p in canonicos if p.product_id not in entries]
        print(f"El criterio no ha cambiado: se clasifican {len(pending)} products nuevos.")

    if pending:
        prompt = (
            Path(options.prompt).read_text(encoding="utf-8")
            + "\n\n---\n\n"
            + vocabulary_for_the_prompt(options.vocabularies)
        )
        for product in pending:
            entries[product.product_id] = classify(product, prompt)
            print(f"  · {product.product_id} {product.name}")

    # Las entries huérfanas se van con el product que las justificaba.
    current = {p.product_id for p in canonicos}
    entries = {k: v for k, v in entries.items() if k in current}

    result_ = {
        "vocabulary_version": 4,
        "products": dict(sorted(entries.items())),
    }
    # Las relations ya written no se tocan aquí: las recalcula relate.py.
    output.write_text(
        json.dumps(result_, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(entries)} entries written en {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
