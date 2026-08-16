"""Pruebas de `models.py` y `loader.py`, contra los datos reales.

Cubren el test 1 de la puerta de la Fase 3 —los recuentos de carga—, el test 3
—una sola canonicalización— y la parte de forma de los tests 16 y 17.
"""

from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import loader  # noqa: E402
import models  # noqa: E402

CSV = RAIZ / "data" / "catalog.csv"
VOCABULARIOS = RAIZ / "data" / "vocabularies.yaml"
CAPA = RAIZ / "data" / "semantic_layer.json"


@pytest.fixture(scope="module")
def cargado():
    return loader.cargar(CSV, VOCABULARIOS, CAPA)


# --------------------------------------------------------------------------
# Test 1 · recuentos de carga
# --------------------------------------------------------------------------


def test_se_cargan_los_150_productos(cargado):
    productos, _, _ = cargado
    assert len(productos) == 150


def test_hay_145_product_type(cargado):
    productos, _, _ = cargado
    assert len({p.product_type for p in productos}) == 145


def test_hay_30_use_case(cargado):
    productos, _, _ = cargado
    assert len({v for p in productos for v in p.use_case}) == 30


def test_hay_31_functional_family(cargado):
    productos, _, _ = cargado
    assert len({v for p in productos for v in p.functional_family}) == 31


def test_el_reparto_de_gift_risk_es_130_15_5(cargado):
    productos, _, _ = cargado
    reparto = {valor: 0 for valor in ("low", "taste_dependent", "high_commitment")}
    for producto in productos:
        reparto[producto.gift_risk] += 1
    assert reparto == {"low": 130, "taste_dependent": 15, "high_commitment": 5}


def test_hay_5_stocking_filler(cargado):
    productos, _, _ = cargado
    assert sum(1 for p in productos if p.stocking_filler) == 5


# --------------------------------------------------------------------------
# Test 3 · una sola canonicalización
# --------------------------------------------------------------------------


def test_el_catalogo_y_la_capa_semantica_cubren_el_mismo_conjunto(cargado):
    productos, _, _ = cargado
    capa = json.loads(CAPA.read_text(encoding="utf-8"))
    entradas = capa["products"] if isinstance(capa, dict) and "products" in capa else capa
    claves = set(entradas) if isinstance(entradas, dict) else {e["product_id"] for e in entradas}
    assert {p.product_id for p in productos} == claves


def test_ningun_producto_se_queda_sin_clasificar(cargado):
    productos, _, _ = cargado
    for producto in productos:
        assert producto.product_type
        assert producto.functional_family
        assert producto.use_case
        assert producto.gift_risk


def test_ningun_valor_cae_fuera_del_vocabulario(cargado):
    import yaml

    productos, _, _ = cargado
    vocabulario = yaml.safe_load(VOCABULARIOS.read_text(encoding="utf-8"))
    for campo in ("product_type", "functional_family", "use_case", "gift_risk"):
        permitidos = set(vocabulario[campo])
        for producto in productos:
            valor = getattr(producto, campo)
            valores = valor if isinstance(valor, list) else [valor]
            assert set(valores) <= permitidos, (producto.product_id, campo, valores)


# --------------------------------------------------------------------------
# B4.3 · la forma de Product
# --------------------------------------------------------------------------


def test_product_tiene_exactamente_26_campos():
    assert len(fields(models.Product)) == 26
    assert tuple(f.name for f in fields(models.Product)) == models.CAMPOS_DE_PRODUCT


@pytest.mark.parametrize("campo", ["description_quality", "tags", "stock", "alt_product_ids"])
def test_los_campos_que_no_viajan_no_estan_en_product(campo):
    assert campo not in {f.name for f in fields(models.Product)}


def test_los_campos_que_no_viajan_si_los_conserva_el_loader(cargado):
    _, fuera_del_contrato, _ = cargado
    assert len(fuera_del_contrato) == 150
    for datos in fuera_del_contrato.values():
        assert set(datos) == {"description_quality", "tags", "stock", "alt_product_ids"}


# --------------------------------------------------------------------------
# Relaciones · una sola vez en el fichero, resueltas desde los dos extremos
# --------------------------------------------------------------------------


def test_las_relaciones_se_resuelven_desde_los_dos_extremos(cargado):
    productos, _, _ = cargado
    por_id = {p.product_id: p for p in productos}
    for producto in productos:
        for otro in producto.pairs_with:
            assert producto.product_id in por_id[otro].pairs_with
        for otro in producto.alternative_to:
            assert producto.product_id in por_id[otro].alternative_to


def test_participan_32_productos_en_alguna_relacion(cargado):
    productos, _, _ = cargado
    participan = {p.product_id for p in productos if p.pairs_with or p.alternative_to}
    assert len(participan) == 32


def test_ningun_producto_se_relaciona_consigo_mismo(cargado):
    productos, _, _ = cargado
    for producto in productos:
        assert producto.product_id not in producto.pairs_with
        assert producto.product_id not in producto.alternative_to


def test_el_relation_type_no_viaja_en_product(cargado):
    productos, _, tipos_de_relacion = cargado
    assert "relation_type" not in {f.name for f in fields(models.Product)}
    assert tipos_de_relacion
    for clase in tipos_de_relacion.values():
        assert clase in {"equivalent", "same_function"}


def test_solo_hay_una_relacion_equivalent(cargado):
    _, _, tipos_de_relacion = cargado
    equivalentes = {
        tuple(sorted(par)) for par, clase in tipos_de_relacion.items() if clase == "equivalent"
    }
    assert equivalentes == {("HL-009", "HL-010")}


# --------------------------------------------------------------------------
# B4.8 · los metadatos son de cada operación, no comunes
# --------------------------------------------------------------------------


def test_cada_operacion_declara_sus_metadatos():
    assert models.METADATOS_POR_OPERACION == {
        "get_categories": (),
        "get_products_by_category": ("total", "offset"),
        "find_products_by_criteria": ("query_understood", "excluded", "not_applied"),
        "get_related_products": ("relation_type", "query_understood", "excluded"),
        "get_product_details": (),
    }


def test_not_applied_solo_existe_en_la_busqueda():
    con_not_applied = {
        operacion
        for operacion, metadatos in models.METADATOS_POR_OPERACION.items()
        if "not_applied" in metadatos
    }
    assert con_not_applied == {"find_products_by_criteria"}


def test_excluded_solo_existe_en_busqueda_y_relacionados():
    con_excluded = {
        operacion
        for operacion, metadatos in models.METADATOS_POR_OPERACION.items()
        if "excluded" in metadatos
    }
    assert con_excluded == {"find_products_by_criteria", "get_related_products"}


def test_total_y_offset_solo_existen_en_la_navegacion():
    con_paginacion = {
        operacion
        for operacion, metadatos in models.METADATOS_POR_OPERACION.items()
        if "total" in metadatos or "offset" in metadatos
    }
    assert con_paginacion == {"get_products_by_category"}


def test_ninguna_operacion_pasa_de_ocho_productos():
    for minimo, maximo, por_defecto in models.LIMITES_POR_OPERACION.values():
        assert 1 <= minimo <= por_defecto <= maximo <= models.MAXIMO_ABSOLUTO


def test_relacionados_devuelve_tres_por_defecto():
    assert models.LIMITES_POR_OPERACION["get_related_products"] == (1, 5, 3)


def test_excluded_y_not_applied_se_omiten_cuando_estan_vacios():
    respuesta = models.RespuestaDeBusqueda(results=[], query_understood={})
    assert respuesta.excluded is None
    assert respuesta.not_applied is None


# --------------------------------------------------------------------------
# La puerta de A3.4, vista desde el arranque
# --------------------------------------------------------------------------


def test_una_capa_semantica_incompleta_detiene_el_arranque(tmp_path):
    capa = json.loads(CAPA.read_text(encoding="utf-8"))
    entradas = capa["products"] if isinstance(capa, dict) and "products" in capa else capa
    if isinstance(entradas, dict):
        entradas.pop(next(iter(entradas)))
    else:
        entradas.pop(0)
    recortada = tmp_path / "semantic_layer.json"
    recortada.write_text(json.dumps(capa), encoding="utf-8")

    with pytest.raises(loader.CapaSemanticaIncompleta):
        loader.cargar(CSV, VOCABULARIOS, recortada)
