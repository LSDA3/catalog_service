"""La puerta de cobertura.

Termina con código de error si el artefacto derivado no cubre exactamente el
catálogo canónico o si alguna relación no es íntegra. **Código de error significa
que no se despliega**: es una puerta, no un respaldo (A3.4).

Valida **forma e integridad, no reinterpreta el catálogo**. No comprueba si
`equivalent` se eligió bien —eso es una lectura del text_ y ocurre en el
enriquecimiento—, sino que lo escrito sea consistente consigo mismo.

El universe de la integridad referencial son **los identifiers canónicos**,
no los 152 brutos: un `alt_product_id` es un aliases_ de identidad, no un nodo.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

import normalization  # noqa: E402

VOCABULARY_VERSION = 4
# `product_type` NO está aquí: es el único vocabulary **controlado pero
# abierto**, porque un product nuevo introduce legítimamente un kind_ nuevo con
# sus aliases_. De él se comprueba que exista en el fichero —no que pertenezca a una
# lista congelada— y que ningún aliases_ resuelva a dos types_ distintos.
CLOSED_VOCABULARIES = (
    "use_case",
    "functional_family",
    "gift_risk",
    "suitable_relationships",
)
RELATION_TYPE = {"equivalent", "same_function"}


def _entradas(layer: dict | list) -> dict[str, dict]:
    products = layer["products"] if isinstance(layer, dict) and "products" in layer else layer
    if isinstance(products, list):
        return {entrada["product_id"]: entrada for entrada in products}
    return products


def validate(csv_path: Path, semantic_layer_path: Path, vocabularies_path: Path) -> list[str]:
    """Devuelve la lista de failures. Vacía significa que la puerta se abre."""
    failures: list[str] = []

    layer = json.loads(semantic_layer_path.read_text(encoding="utf-8"))
    entries = _entradas(layer)
    vocabulary = yaml.safe_load(vocabularies_path.read_text(encoding="utf-8"))

    declared_version = (
        layer.get("vocabulary_version") if isinstance(layer, dict) else None
    )
    if declared_version != VOCABULARY_VERSION:
        failures.append(
            f"la layer declara vocabulary_version {declared_version!r} "
            f"y se esperaba {VOCABULARY_VERSION}"
        )
    if vocabulary.get("version") != VOCABULARY_VERSION:
        failures.append(
            f"el vocabulary declara version {vocabulary.get('version')!r} "
            f"y se esperaba {VOCABULARY_VERSION}"
        )

    product_types_by_id = {
        product_id: entrada.get("product_type") for product_id, entrada in entries.items()
    }
    canonicos, _ = normalization.canonicalize(
        csv_path, vocabularies_path, product_types_by_id
    )
    identifiers = {product.product_id for product in canonicos}

    # Igualdad exacta de conjuntos: ni ausentes ni huérfanos.
    missing = sorted(identifiers - set(entries))
    orphans = sorted(set(entries) - identifiers)
    if missing:
        failures.append(f"sin entrada semántica: {missing}")
    if orphans:
        failures.append(f"entries huérfanas, sin product canónico: {orphans}")

    for product_id, entrada in sorted(entries.items()):
        for field in CLOSED_VOCABULARIES:
            if field not in entrada:
                continue
            value_ = entrada[field]
            valores = value_ if isinstance(value_, list) else [value_]
            fuera = [v for v in valores if v not in vocabulary.get(field, {})]
            if fuera:
                failures.append(f"{product_id}: {field} fuera del vocabulary: {fuera}")

        kind_ = entrada.get("product_type")
        if not kind_:
            failures.append(f"{product_id}: product_type vacío")
        elif kind_ not in vocabulary.get("product_type", {}):
            failures.append(
                f"{product_id}: product_type {kind_!r} no está declarado en el vocabulary. "
                "Puede crecer, pero se declara: no se usa sin darlo de alta"
            )

        if not entrada.get("use_case"):
            failures.append(f"{product_id}: use_case vacío")
        if not entrada.get("functional_family"):
            failures.append(f"{product_id}: functional_family vacío")

    # Un aliases_ no puede resolver a dos types_ distintos: sería una ambigüedad que
    # el servicio no puede deshacer al leer una consulta.
    owners: dict[str, str] = {}
    for kind_, definition in vocabulary.get("product_type", {}).items():
        for aliases_ in (definition.get("aliases", []) if isinstance(definition, dict) else []):
            key_ = aliases_.lower()
            if key_ in owners and owners[key_] != kind_:
                failures.append(
                    f"el aliases_ {aliases_!r} resuelve a {owners[key_]!r} y a {kind_!r}"
                )
            owners[key_] = kind_
        if not (definition.get("definicion") if isinstance(definition, dict) else None):
            failures.append(f"product_type {kind_!r} sin definition")

    pairs_seen: set[tuple[str, str]] = set()
    for product_id, entrada in sorted(entries.items()):
        for link in entrada.get("pairs_with") or []:
            other = link["product_id"] if isinstance(link, dict) else link
            if other not in identifiers:
                failures.append(f"{product_id}: pairs_with apunta a {other}, que no es canónico")
            if other == product_id:
                failures.append(f"{product_id}: pairs_with apunta a sí mismo")

        for link in entrada.get("alternative_to") or []:
            if not isinstance(link, dict) or "product_id" not in link:
                failures.append(f"{product_id}: alternative_to sin product_id")
                continue
            other = link["product_id"]
            if other not in identifiers:
                failures.append(
                    f"{product_id}: alternative_to apunta a {other}, que no es canónico"
                )
            if other == product_id:
                failures.append(f"{product_id}: alternative_to apunta a sí mismo")
            if link.get("relation_type") not in RELATION_TYPE:
                failures.append(
                    f"{product_id} → {other}: relation_type "
                    f"{link.get('relation_type')!r} no es válido"
                )
            pair = tuple(sorted((product_id, other)))
            if pair in pairs_seen:
                failures.append(f"{pair[0]} · {pair[1]}: la pair está persistida dos veces")
            pairs_seen.add(pair)
            if product_id != pair[0]:
                failures.append(
                    f"{pair[0]} · {pair[1]}: persistida bajo el identificador larger"
                )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Puerta de cobertura de la layer semántica")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--semantic", required=True)
    parser.add_argument("--vocabularies", default="data/vocabularies.yaml")
    options = parser.parse_args()

    failures = validate(
        Path(options.csv), Path(options.semantic), Path(options.vocabularies)
    )
    if failures:
        print("La puerta NO se abre. No se despliega.\n")
        for fallo in failures:
            print(f"  · {fallo}")
        return 1

    print("Cobertura completa e integridad correcta. La puerta se abre.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
