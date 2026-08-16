"""La puerta de cobertura.

Termina con código de error si el artefacto derivado no cubre exactamente el
catálogo canónico o si alguna relación no es íntegra. **Código de error significa
que no se despliega**: es una puerta, no un respaldo (A3.4).

Valida **forma e integridad, no reinterpreta el catálogo**. No comprueba si
`equivalent` se eligió bien —eso es una lectura del texto y ocurre en el
enriquecimiento—, sino que lo escrito sea consistente consigo mismo.

El universo de la integridad referencial son **los identificadores canónicos**,
no los 152 brutos: un `alt_product_id` es un alias de identidad, no un nodo.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

import normalization  # noqa: E402

VERSION_DE_VOCABULARIO = 4
VOCABULARIOS_CERRADOS = (
    "product_type",
    "use_case",
    "functional_family",
    "gift_risk",
    "suitable_relationships",
)
RELATION_TYPE = {"equivalent", "same_function"}


def _entradas(capa: dict | list) -> dict[str, dict]:
    productos = capa["products"] if isinstance(capa, dict) and "products" in capa else capa
    if isinstance(productos, list):
        return {entrada["product_id"]: entrada for entrada in productos}
    return productos


def validar(ruta_csv: Path, ruta_semantica: Path, ruta_vocabularios: Path) -> list[str]:
    """Devuelve la lista de fallos. Vacía significa que la puerta se abre."""
    fallos: list[str] = []

    capa = json.loads(ruta_semantica.read_text(encoding="utf-8"))
    entradas = _entradas(capa)
    vocabulario = yaml.safe_load(ruta_vocabularios.read_text(encoding="utf-8"))

    version_declarada = (
        capa.get("vocabulary_version") if isinstance(capa, dict) else None
    )
    if version_declarada != VERSION_DE_VOCABULARIO:
        fallos.append(
            f"la capa declara vocabulary_version {version_declarada!r} "
            f"y se esperaba {VERSION_DE_VOCABULARIO}"
        )
    if vocabulario.get("version") != VERSION_DE_VOCABULARIO:
        fallos.append(
            f"el vocabulario declara version {vocabulario.get('version')!r} "
            f"y se esperaba {VERSION_DE_VOCABULARIO}"
        )

    tipos_por_producto = {
        product_id: entrada.get("product_type") for product_id, entrada in entradas.items()
    }
    canonicos, _ = normalization.canonicalizar(
        ruta_csv, ruta_vocabularios, tipos_por_producto
    )
    identificadores = {producto.product_id for producto in canonicos}

    # Igualdad exacta de conjuntos: ni ausentes ni huérfanos.
    faltan = sorted(identificadores - set(entradas))
    huerfanos = sorted(set(entradas) - identificadores)
    if faltan:
        fallos.append(f"sin entrada semántica: {faltan}")
    if huerfanos:
        fallos.append(f"entradas huérfanas, sin producto canónico: {huerfanos}")

    for product_id, entrada in sorted(entradas.items()):
        for campo in VOCABULARIOS_CERRADOS:
            if campo not in entrada:
                continue
            valor = entrada[campo]
            valores = valor if isinstance(valor, list) else [valor]
            fuera = [v for v in valores if v not in vocabulario.get(campo, {})]
            if fuera:
                fallos.append(f"{product_id}: {campo} fuera del vocabulario: {fuera}")

        if not entrada.get("use_case"):
            fallos.append(f"{product_id}: use_case vacío")
        if not entrada.get("functional_family"):
            fallos.append(f"{product_id}: functional_family vacío")

    parejas_vistas: set[tuple[str, str]] = set()
    for product_id, entrada in sorted(entradas.items()):
        for vinculo in entrada.get("pairs_with") or []:
            otro = vinculo["product_id"] if isinstance(vinculo, dict) else vinculo
            if otro not in identificadores:
                fallos.append(f"{product_id}: pairs_with apunta a {otro}, que no es canónico")
            if otro == product_id:
                fallos.append(f"{product_id}: pairs_with apunta a sí mismo")

        for vinculo in entrada.get("alternative_to") or []:
            if not isinstance(vinculo, dict) or "product_id" not in vinculo:
                fallos.append(f"{product_id}: alternative_to sin product_id")
                continue
            otro = vinculo["product_id"]
            if otro not in identificadores:
                fallos.append(
                    f"{product_id}: alternative_to apunta a {otro}, que no es canónico"
                )
            if otro == product_id:
                fallos.append(f"{product_id}: alternative_to apunta a sí mismo")
            if vinculo.get("relation_type") not in RELATION_TYPE:
                fallos.append(
                    f"{product_id} → {otro}: relation_type "
                    f"{vinculo.get('relation_type')!r} no es válido"
                )
            pareja = tuple(sorted((product_id, otro)))
            if pareja in parejas_vistas:
                fallos.append(f"{pareja[0]} · {pareja[1]}: la pareja está persistida dos veces")
            parejas_vistas.add(pareja)
            if product_id != pareja[0]:
                fallos.append(
                    f"{pareja[0]} · {pareja[1]}: persistida bajo el identificador mayor"
                )

    return fallos


def main() -> int:
    argumentos = argparse.ArgumentParser(description="Puerta de cobertura de la capa semántica")
    argumentos.add_argument("--csv", required=True)
    argumentos.add_argument("--semantic", required=True)
    argumentos.add_argument("--vocabularies", default="data/vocabularies.yaml")
    opciones = argumentos.parse_args()

    fallos = validar(
        Path(opciones.csv), Path(opciones.semantic), Path(opciones.vocabularies)
    )
    if fallos:
        print("La puerta NO se abre. No se despliega.\n")
        for fallo in fallos:
            print(f"  · {fallo}")
        return 1

    print("Cobertura completa e integridad correcta. La puerta se abre.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
