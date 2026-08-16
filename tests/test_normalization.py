"""Pruebas de la canonicalización, contra el catálogo real.

Cubren la parte de la puerta de la Fase 3 que depende solo de `normalization.py`:
el test 1 en lo que toca al conteo de carga, el test 2 entero y el test 6 en lo
que toca a no inventar valores ausentes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import normalization as n  # noqa: E402

CSV = RAIZ / "data" / "catalog.csv"
VOCABULARIOS = RAIZ / "data" / "vocabularies.yaml"
CAPA = RAIZ / "data" / "semantic_layer.json"


@pytest.fixture(scope="module")
def tipos() -> dict[str, str]:
    capa = json.loads(CAPA.read_text(encoding="utf-8"))
    productos = capa["products"] if isinstance(capa, dict) and "products" in capa else capa
    if isinstance(productos, list):
        return {p["product_id"]: p["product_type"] for p in productos}
    return {clave: valor["product_type"] for clave, valor in productos.items()}


@pytest.fixture(scope="module")
def catalogo(tipos):
    return n.canonicalizar(CSV, VOCABULARIOS, tipos)


# --------------------------------------------------------------------------
# Test 2 · canonicalización
# --------------------------------------------------------------------------


def test_las_152_filas_dan_150_productos(catalogo):
    canonicos, _ = catalogo
    assert len(canonicos) == 150


def test_las_dos_fusiones_son_las_escritas(catalogo):
    canonicos, _ = catalogo
    fusionados = {p.product_id: p.alt_product_ids for p in canonicos if p.alt_product_ids}
    assert fusionados == {"HL-021": ["KD-024"], "HL-024": ["KD-023"]}


def test_el_id_canonico_es_el_lexicograficamente_menor(catalogo):
    canonicos, _ = catalogo
    for producto in canonicos:
        for absorbido in producto.alt_product_ids:
            assert producto.product_id < absorbido


def test_la_categoria_del_absorbido_pasa_a_secundarias(catalogo):
    canonicos, _ = catalogo
    por_id = {p.product_id: p for p in canonicos}
    assert por_id["HL-021"].secondary_categories == ["Kitchen & Dining"]
    assert por_id["HL-024"].secondary_categories == ["Kitchen & Dining"]


def test_un_identificador_absorbido_resuelve_al_canonico(catalogo):
    canonicos, _ = catalogo
    assert n.resolver_identificador("KD-024", canonicos).product_id == "HL-021"
    assert n.resolver_identificador("KD-023", canonicos).product_id == "HL-024"


def test_un_identificador_inexistente_no_resuelve(catalogo):
    canonicos, _ = catalogo
    assert n.resolver_identificador("KD-999", canonicos) is None


# --------------------------------------------------------------------------
# Test 1 · conteos de carga
# --------------------------------------------------------------------------


def test_hay_139_disponibles(catalogo):
    canonicos, _ = catalogo
    assert sum(1 for p in canonicos if p.in_stock) == 139


def test_los_17_valores_de_categoria_dan_11_categorias(catalogo):
    canonicos, _ = catalogo
    assert len({p.category for p in canonicos}) == 11


def test_la_normalizacion_de_categoria_equipara_and_con_ampersand():
    assert n.normalizar_categoria(" Home & Living") == "Home & Living"
    assert n.normalizar_categoria("Home and Living") == "Home & Living"
    assert n.normalizar_categoria("home & living") == "Home & Living"
    assert n.normalizar_categoria("Tech and Gadgets") == "Tech & Gadgets"


# --------------------------------------------------------------------------
# Apertura de recipient
# --------------------------------------------------------------------------


def test_140_productos_llevan_anyone(catalogo):
    canonicos, _ = catalogo
    assert sum(1 for p in canonicos if "anyone" in p.recipient) == 140


def test_los_diez_exclusivos_son_los_escritos(catalogo):
    canonicos, _ = catalogo
    sin_anyone = sorted(p.product_id for p in canonicos if "anyone" not in p.recipient)
    assert sin_anyone == [
        "BW-004",
        "BW-006",
        "JW-003",
        "JW-004",
        "KI-001",
        "KI-002",
        "KI-003",
        "KI-004",
        "KI-005",
        "KI-006",
    ]


def test_el_valor_original_se_conserva(catalogo):
    canonicos, _ = catalogo
    por_id = {p.product_id: p for p in canonicos}
    teclado = por_id["TG-012"]
    assert "anyone" in teclado.recipient
    assert "him" in teclado.recipient


def test_kids_nunca_lleva_anyone(catalogo):
    canonicos, _ = catalogo
    for producto in canonicos:
        if "kids" in producto.recipient:
            assert producto.recipient == ["kids"]


# --------------------------------------------------------------------------
# Test 6 · no se inventan los ausentes
# --------------------------------------------------------------------------


def test_las_cinco_notas_ausentes_siguen_ausentes(catalogo):
    canonicos, _ = catalogo
    sin_nota = [p for p in canonicos if p.rating is None]
    assert len(sin_nota) == 5
    assert all(p.rating != 0 for p in sin_nota)


def test_las_tres_ocasiones_ausentes_siguen_vacias(catalogo):
    canonicos, _ = catalogo
    assert sum(1 for p in canonicos if not p.occasion) == 3


def test_las_dos_descripciones_pobres_son_las_escritas(catalogo):
    canonicos, _ = catalogo
    pobres = sorted(p.product_id for p in canonicos if p.description_quality == "poor")
    assert pobres == ["BS-015", "HL-013"]


# --------------------------------------------------------------------------
# Normalización de formatos · la tabla de A2.2
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entra, sale",
    [
        ("49", 49.00),
        ("€49.95", 49.95),
        ("49.95 €", 49.95),
        ("EUR 49.95", 49.95),
        ("49,95", 49.95),
        ("1.234,56", 1234.56),
    ],
)
def test_el_precio_normaliza_lo_que_no_es_ambiguo(entra, sale):
    assert n.normalizar_precio(entra, 2) == sale


def test_el_precio_vacio_deja_el_producto_sin_precio():
    assert n.normalizar_precio("", 2) is None


@pytest.mark.parametrize("entra", ["1,234", "-5", "0"])
def test_el_precio_se_detiene_ante_lo_imposible_o_lo_ambiguo(entra):
    with pytest.raises(n.CatalogoAmbiguo):
        n.normalizar_precio(entra, 2)


@pytest.mark.parametrize(
    "entra, cantidad, disponible",
    [("7", 7, True), ("0", 0, False), ("yes", None, True), ("Y", None, True)],
)
def test_el_stock_separa_cantidad_de_disponibilidad(entra, cantidad, disponible):
    assert n.normalizar_stock(entra, 2) == (cantidad, disponible)


def test_el_stock_ilegible_detiene_el_arranque():
    with pytest.raises(n.CatalogoAmbiguo):
        n.normalizar_stock("bastantes", 2)


# --------------------------------------------------------------------------
# Determinismo · lo que sostiene la puerta de cobertura
# --------------------------------------------------------------------------


def test_dos_ejecuciones_dan_el_mismo_conjunto_de_identificadores(tipos):
    primera, _ = n.canonicalizar(CSV, VOCABULARIOS, tipos)
    segunda, _ = n.canonicalizar(CSV, VOCABULARIOS, tipos)
    assert [p.product_id for p in primera] == [p.product_id for p in segunda]


def test_el_conjunto_canonico_coincide_con_la_capa_semantica(catalogo, tipos):
    canonicos, _ = catalogo
    assert {p.product_id for p in canonicos} == set(tipos)
