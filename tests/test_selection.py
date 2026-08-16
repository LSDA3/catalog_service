"""Pruebas de las fronteras, la precedencia y los relacionados.

Aquí caen los seis escenarios de A8.7 con sus recuentos declarados —34, 0, 97 y
132— y los casos que se implementan mal si no se comprueban: `universal`, los
ausentes de `rating`, `gift_risk` modulado, y la cascada de tres niveles de
`alternative_to`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import normalization  # noqa: E402
import selection  # noqa: E402
from models import Product  # noqa: E402
from repository import CatalogoEnMemoria  # noqa: E402

CSV = RAIZ / "data" / "catalog.csv"
VOCABULARIOS = RAIZ / "data" / "vocabularies.yaml"
CAPA = RAIZ / "data" / "semantic_layer.json"


@pytest.fixture(scope="module")
def catalogo():
    return CatalogoEnMemoria(CSV, VOCABULARIOS, CAPA)


@pytest.fixture(scope="module")
def exclusivos():
    return normalization.tipos_exclusivos_de_genero(VOCABULARIOS)


@pytest.fixture(scope="module")
def calidad(catalogo):
    return {
        p.product_id: catalogo.fuera_del_contrato(p.product_id)["description_quality"]
        for p in catalogo.todos()
    }


def buscar(catalogo, exclusivos, calidad, criterios, product_type=None):
    base = selection.restringir_por_coincidencia_exacta(catalogo.todos(), product_type)
    dentro = selection.coger_lo_que_cumple(base, criterios, exclusivos)
    return dentro, selection.ordenar_por_precedencia(dentro, criterios, calidad)


# --------------------------------------------------------------------------
# Las doce fronteras
# --------------------------------------------------------------------------


def test_los_dos_cortes_invariantes_dejan_132(catalogo, exclusivos):
    dentro = selection.coger_lo_que_cumple(catalogo.todos(), {}, exclusivos)
    assert len(dentro) == 132


def test_ningun_agotado_entra_nunca(catalogo, exclusivos):
    dentro = selection.coger_lo_que_cumple(catalogo.todos(), {}, exclusivos)
    assert all(p.in_stock for p in dentro)


def test_gift_wrap_deja_127_disponibles(catalogo, exclusivos):
    con_envoltorio = [p for p in catalogo.todos() if p.gift_wrap is True]
    assert len(con_envoltorio) == 137
    assert sum(1 for p in con_envoltorio if p.in_stock) == 127


def test_max_shipping_days_de_tres_deja_98_disponibles(catalogo):
    disponibles = [p for p in catalogo.todos() if p.in_stock]
    assert len(disponibles) == 139
    en_plazo = [
        p for p in disponibles if p.shipping_days is not None and p.shipping_days <= 3
    ]
    assert len(en_plazo) == 98


def test_target_price_abre_una_banda_de_mas_menos_20(catalogo, exclusivos):
    dentro = selection.coger_lo_que_cumple(catalogo.todos(), {"target_price": 50}, exclusivos)
    assert all(40 <= p.price <= 60 for p in dentro)


def test_recipient_kids_es_el_unico_valor_de_recipient_que_corta(catalogo, exclusivos):
    dentro = selection.coger_lo_que_cumple(
        catalogo.todos(), {"recipient": "kids"}, exclusivos
    )
    assert all("kids" in p.recipient for p in dentro)

    con_her = selection.coger_lo_que_cumple(catalogo.todos(), {"recipient": "her"}, exclusivos)
    sin_criterio = selection.coger_lo_que_cumple(catalogo.todos(), {}, exclusivos)
    assert len(con_her) == len(sin_criterio)


# --------------------------------------------------------------------------
# La restricción de coincidencia exacta
# --------------------------------------------------------------------------


def test_product_type_restringe_y_no_admite_otro_objeto(catalogo):
    conjunto = selection.restringir_por_coincidencia_exacta(catalogo.todos(), "chef_knife")
    assert conjunto
    assert all(p.product_type == "chef_knife" for p in conjunto)


def test_sin_product_type_no_restringe_nada(catalogo):
    assert len(selection.restringir_por_coincidencia_exacta(catalogo.todos(), None)) == 150


# --------------------------------------------------------------------------
# A8.7 · los seis escenarios
# --------------------------------------------------------------------------


def test_escenario_1_la_hermana(catalogo, exclusivos, calidad):
    criterios = {
        "target_price": 50,
        "occasion": "birthday",
        "recipient": "her",
        "relationship": "family",
        "max_shipping_days": 7,
    }
    dentro, orden = buscar(catalogo, exclusivos, calidad, criterios)
    assert len(dentro) == 34
    # La consulta no lleva `use_case`, y ahí `universal` va delante.
    assert orden[0].product_id == "EX-001"


def test_escenario_2_el_cuchillo(catalogo, exclusivos, calidad):
    criterios = {"max_price": 100, "max_shipping_days": 7}
    dentro, orden = buscar(catalogo, exclusivos, calidad, criterios, product_type="chef_knife")
    assert dentro == []

    conjunto = selection.restringir_por_coincidencia_exacta(catalogo.todos(), "chef_knife")
    excluidos = selection.por_encima_del_presupuesto(conjunto, criterios, exclusivos, calidad)
    assert len(excluidos) == 1
    assert excluidos[0].product_id == "KD-001"
    assert excluidos[0].exclusion_reason == "over_budget"
    assert excluidos[0].actual == 149.00
    assert excluidos[0].required == 100


def test_escenario_3_la_manta(catalogo, exclusivos, calidad):
    criterios = {
        "max_price": 100,
        "max_shipping_days": 7,
        "functional_family": ["soft_furnishing"],
    }
    dentro, orden = buscar(catalogo, exclusivos, calidad, criterios)
    assert len(dentro) == 97
    assert orden[0].product_id == "HL-010"


def test_escenario_5_lo_retro(catalogo, exclusivos, calidad):
    criterios = {"use_case": ["tabletop_gaming"]}
    dentro, orden = buscar(catalogo, exclusivos, calidad, criterios)
    assert len(dentro) == 132
    assert [p.product_id for p in orden[:4]] == ["GP-005", "GP-001", "GP-009", "GP-003"]
    # La consola agotada no aparece.
    assert all("Console" not in p.name for p in orden)


# --------------------------------------------------------------------------
# El orden por precedencia · los casos que se implementan mal
# --------------------------------------------------------------------------


def test_universal_va_delante_cuando_no_hay_use_case(catalogo, exclusivos, calidad):
    criterios = {"target_price": 50, "occasion": "birthday", "recipient": "her"}
    _, orden = buscar(catalogo, exclusivos, calidad, criterios)
    assert "universal" in orden[0].use_case


def test_universal_no_coincide_con_una_situacion_concreta(catalogo, exclusivos, calidad):
    criterios = {"use_case": ["cooking"]}
    _, orden = buscar(catalogo, exclusivos, calidad, criterios)
    posicion_de_universal = next(
        i for i, p in enumerate(orden) if "universal" in p.use_case
    )
    cocinan = [i for i, p in enumerate(orden) if "cooking" in p.use_case]
    ni_uno_ni_otro = [
        i
        for i, p in enumerate(orden)
        if "cooking" not in p.use_case and "universal" not in p.use_case
    ]
    assert max(cocinan) < posicion_de_universal
    assert posicion_de_universal < min(ni_uno_ni_otro)


def test_una_nota_conocida_va_delante_de_una_ausente(catalogo, exclusivos, calidad):
    """Y solo entre los que llegan empatados a ese nivel.

    Un producto sin nota que ya ganó en un nivel anterior sigue delante: este
    nivel solo interviene cuando la comparación ha llegado hasta aquí.
    """
    _, orden = buscar(catalogo, exclusivos, calidad, {})
    claves = [selection.clave_de_precedencia(p, {}, calidad[p.product_id]) for p in orden]
    comparados = 0
    for anterior, siguiente in zip(orden, orden[1:]):
        clave_anterior = selection.clave_de_precedencia(
            anterior, {}, calidad[anterior.product_id]
        )
        clave_siguiente = selection.clave_de_precedencia(
            siguiente, {}, calidad[siguiente.product_id]
        )
        if clave_anterior[:5] != clave_siguiente[:5]:
            continue  # los separó un nivel anterior
        if anterior.rating is None and siguiente.rating is not None:
            raise AssertionError(
                f"{anterior.product_id} sin nota va delante de {siguiente.product_id}"
            )
        comparados += 1
    assert comparados and claves


def test_una_nota_ausente_no_se_compara_como_cero(catalogo):
    sin_nota = next(p for p in catalogo.todos() if p.rating is None)
    clave = selection.clave_de_precedencia(sin_nota, {})
    nivel_seis = clave[5]
    assert nivel_seis[0] == 1  # desconocido, detrás del conocido
    assert nivel_seis[1] == 0.0  # y no una nota de cero


def test_manda_la_nota_y_solo_desempatan_las_resenas(catalogo):
    mejor_nota = next(p for p in catalogo.todos() if p.product_id == "GP-005")  # 4.8 · 163
    mas_resenas = next(p for p in catalogo.todos() if p.product_id == "GP-001")  # 4.7 · 588
    assert selection._nivel_seis(mejor_nota) < selection._nivel_seis(mas_resenas)


def test_buyer_knows_recipient_omite_el_nivel_de_gift_risk(catalogo):
    arriesgado = next(p for p in catalogo.todos() if p.gift_risk == "high_commitment")
    con_precaucion = selection.clave_de_precedencia(arriesgado, {})
    sin_precaucion = selection.clave_de_precedencia(
        arriesgado, {"buyer_knows_recipient": True}
    )
    assert con_precaucion[6] == 2
    assert sin_precaucion[6] == 0


def test_ausente_y_false_ordenan_igual_en_gift_risk(catalogo):
    arriesgado = next(p for p in catalogo.todos() if p.gift_risk == "high_commitment")
    ausente = selection.clave_de_precedencia(arriesgado, {})
    declarado = selection.clave_de_precedencia(
        arriesgado, {"buyer_knows_recipient": False}
    )
    assert ausente == declarado


def test_coincidir_con_dos_valores_no_adelanta_a_coincidir_con_uno(catalogo):
    productos = catalogo.todos()
    uno = next(p for p in productos if "tabletop_gaming" in p.use_case)
    criterios = {"use_case": ["tabletop_gaming", "cooking"]}
    clave = selection.clave_de_precedencia(uno, criterios)
    assert clave[0] == (-1, 0)


def test_el_empate_final_se_estabiliza_con_product_id(catalogo, exclusivos, calidad):
    _, primera = buscar(catalogo, exclusivos, calidad, {})
    _, segunda = buscar(catalogo, exclusivos, calidad, {})
    assert [p.product_id for p in primera] == [p.product_id for p in segunda]


def test_ninguna_respuesta_lleva_una_puntuacion(catalogo):
    from dataclasses import fields

    nombres = {f.name for f in fields(Product)}
    for sospechoso in ("score", "product_score", "weight", "similarity", "rank", "position"):
        assert sospechoso not in nombres


# --------------------------------------------------------------------------
# Relacionados · los tres niveles
# --------------------------------------------------------------------------


def test_un_nivel_inferior_nunca_adelanta_a_uno_superior(catalogo, exclusivos, calidad):
    ancla = next(p for p in catalogo.todos() if p.alternative_to)
    elegidos = selection.relacionados(
        catalogo.todos(), "alternative_to", ancla, {}, 5, exclusivos, calidad
    )
    explicitos = [p for p in elegidos if p.product_id in ancla.alternative_to]
    otros = [p for p in elegidos if p.product_id not in ancla.alternative_to]
    if explicitos and otros:
        assert elegidos.index(explicitos[-1]) < elegidos.index(otros[0])


def test_el_ancla_nunca_se_devuelve_a_si_misma(catalogo, exclusivos, calidad):
    for relacion in ("alternative_to", "pairs_with"):
        for ancla in catalogo.todos()[:40]:
            elegidos = selection.relacionados(
                catalogo.todos(), relacion, ancla, {}, 5, exclusivos, calidad
            )
            assert ancla.product_id not in {p.product_id for p in elegidos}


def test_pairs_with_no_tiene_tres_niveles(catalogo, exclusivos, calidad):
    ancla = next(p for p in catalogo.todos() if p.pairs_with)
    elegidos = selection.relacionados(
        catalogo.todos(), "pairs_with", ancla, {}, 5, exclusivos, calidad
    )
    assert elegidos
    assert all(p.product_id in ancla.pairs_with for p in elegidos)


def test_el_complemento_no_tiene_que_sostenerse_solo_como_regalo(
    catalogo, exclusivos, calidad
):
    """La piedra de afilar llega por `pairs_with`, y no es un regalo por sí sola."""
    cuchillo = catalogo.por_id("KD-001")
    elegidos = selection.relacionados(
        catalogo.todos(), "pairs_with", cuchillo, {}, 5, exclusivos, calidad
    )
    assert "KD-003" in {p.product_id for p in elegidos}
    assert any(not p.is_standalone_gift for p in elegidos)


def test_pero_al_recomendar_si_tiene_que_sostenerse_solo(catalogo, exclusivos):
    dentro = selection.coger_lo_que_cumple(catalogo.todos(), {}, exclusivos)
    assert all(p.is_standalone_gift for p in dentro)


def test_in_stock_no_tiene_excepcion_ni_en_los_complementos(
    catalogo, exclusivos, calidad
):
    for ancla in catalogo.todos():
        elegidos = selection.relacionados(
            catalogo.todos(), "pairs_with", ancla, {}, 5, exclusivos, calidad
        )
        assert all(p.in_stock for p in elegidos)


def test_pairs_with_sin_ancla_no_devuelve_nada(catalogo, exclusivos, calidad):
    assert (
        selection.relacionados(catalogo.todos(), "pairs_with", None, {}, 5, exclusivos, calidad)
        == []
    )


def test_los_relacionados_respetan_las_fronteras(catalogo, exclusivos, calidad):
    ancla = next(p for p in catalogo.todos() if p.alternative_to)
    elegidos = selection.relacionados(
        catalogo.todos(), "alternative_to", ancla, {"max_price": 50}, 5, exclusivos, calidad
    )
    assert all(p.price is None or p.price <= 50 for p in elegidos)


def test_los_relacionados_no_pasan_del_limite(catalogo, exclusivos, calidad):
    ancla = next(p for p in catalogo.todos() if p.functional_family)
    for limite in (1, 3, 5):
        elegidos = selection.relacionados(
            catalogo.todos(), "alternative_to", ancla, {}, limite, exclusivos, calidad
        )
        assert len(elegidos) <= limite
