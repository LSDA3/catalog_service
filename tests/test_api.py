"""Pruebas de la frontera de acceso, del límite de tasa y del contrato publicado.

Las que necesitan levantar el servicio usan el `TestClient` de FastAPI. Las dos
credenciales se inyectan por entorno, como en producción: aquí no hay ninguna
credencial escrita en el código ni en el fichero.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

CATALOG_KEY = "clave-de-catalogo-para-pruebas"
DIAGNOSTICS_KEY = "clave-de-diagnostico-para-pruebas"
os.environ["CATALOG_API_KEY"] = CATALOG_KEY
os.environ["DIAGNOSTICS_API_KEY"] = DIAGNOSTICS_KEY


@pytest.fixture(scope="module")
def cliente():
    from fastapi.testclient import TestClient

    import api

    return TestClient(api.app)


@pytest.fixture(autouse=True)
def contador_limpio():
    import api

    for cola in api._peticiones_recientes.values():
        cola.clear()
    yield


def con_catalogo(cliente, ruta, **parametros):
    return cliente.get(ruta, params=parametros, headers={"X-Api-Key": CATALOG_KEY})


# --------------------------------------------------------------------------
# B6.12 · la frontera de acceso
# --------------------------------------------------------------------------


def test_sin_credencial_responde_401(cliente):
    assert cliente.get("/categories").status_code == 401


def test_con_credencial_desconocida_responde_401(cliente):
    respuesta = cliente.get("/categories", headers={"X-Api-Key": "no-es-ninguna"})
    assert respuesta.status_code == 401


def test_con_la_credencial_de_catalogo_responde_200(cliente):
    assert con_catalogo(cliente, "/categories").status_code == 200


def test_la_credencial_de_catalogo_no_abre_el_diagnostico(cliente):
    respuesta = cliente.get(
        "/_diagnostics/load-report", headers={"X-Api-Key": CATALOG_KEY}
    )
    assert respuesta.status_code == 403


def test_la_credencial_de_diagnostico_no_abre_el_catalogo(cliente):
    respuesta = cliente.get("/categories", headers={"X-Api-Key": DIAGNOSTICS_KEY})
    assert respuesta.status_code == 403


def test_el_diagnostico_con_su_credencial_responde_200(cliente):
    respuesta = cliente.get(
        "/_diagnostics/load-report", headers={"X-Api-Key": DIAGNOSTICS_KEY}
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["products"] == 150


def test_la_especificacion_y_la_documentacion_son_abiertas(cliente):
    assert cliente.get("/openapi.json").status_code == 200
    assert cliente.get("/docs").status_code == 200


def test_el_diagnostico_no_figura_en_la_especificacion(cliente):
    especificacion = cliente.get("/openapi.json").json()
    assert "/_diagnostics/load-report" not in especificacion["paths"]


def test_la_especificacion_no_contiene_ninguna_credencial(cliente):
    texto = cliente.get("/openapi.json").text
    assert CATALOG_KEY not in texto
    assert DIAGNOSTICS_KEY not in texto


# --------------------------------------------------------------------------
# B6.8 · el límite de tasa
# --------------------------------------------------------------------------


def test_la_peticion_61_del_minuto_recibe_429(cliente):
    for numero in range(60):
        assert con_catalogo(cliente, "/categories").status_code == 200, numero
    excedida = con_catalogo(cliente, "/categories")
    assert excedida.status_code == 429
    assert excedida.json()["detail"]["error_code"] == "rate_limited"


def test_el_diagnostico_tiene_su_propio_limite(cliente):
    for _ in range(10):
        assert (
            cliente.get(
                "/_diagnostics/load-report", headers={"X-Api-Key": DIAGNOSTICS_KEY}
            ).status_code
            == 200
        )
    excedida = cliente.get(
        "/_diagnostics/load-report", headers={"X-Api-Key": DIAGNOSTICS_KEY}
    )
    assert excedida.status_code == 429


def test_los_dos_contadores_son_independientes(cliente):
    for _ in range(10):
        cliente.get("/_diagnostics/load-report", headers={"X-Api-Key": DIAGNOSTICS_KEY})
    assert con_catalogo(cliente, "/categories").status_code == 200


# --------------------------------------------------------------------------
# B4.8 · los envelopes de cada operación
# --------------------------------------------------------------------------


def test_get_categories_devuelve_once_categorias(cliente):
    cuerpo = con_catalogo(cliente, "/categories").json()
    assert len(cuerpo["results"]) == 11
    assert cuerpo["currency"] == "EUR"
    assert "total" not in cuerpo and "excluded" not in cuerpo


def test_la_navegacion_lleva_total_y_offset(cliente):
    cuerpo = con_catalogo(
        cliente, "/products/by-category", category="Kitchen & Dining"
    ).json()
    # 22 en la categoría, 2 agotados: navegar no exige `is_standalone_gift`.
    assert cuerpo["total"] == 20
    assert cuerpo["offset"] == 0
    assert len(cuerpo["results"]) <= 8
    assert "not_applied" not in cuerpo


def test_la_busqueda_no_pagina_y_puede_llevar_not_applied(cliente):
    cuerpo = con_catalogo(cliente, "/products/search", product_type="santoku").json()
    assert "total" not in cuerpo and "offset" not in cuerpo
    assert cuerpo["not_applied"] == [
        {"parameter": "product_type", "received": "santoku", "reason": "unresolved"}
    ]


def test_el_alias_gyuto_resuelve_a_chef_knife(cliente):
    cuerpo = con_catalogo(cliente, "/products/search", product_type="gyuto").json()
    assert cuerpo["query_understood"]["product_type"] == "chef_knife"
    assert "not_applied" not in cuerpo


def test_el_escenario_del_cuchillo_en_la_api(cliente):
    cuerpo = con_catalogo(
        cliente, "/products/search", product_type="chef_knife", max_price=100
    ).json()
    assert cuerpo["results"] == []
    assert cuerpo["excluded"][0]["product_id"] == "KD-001"
    assert cuerpo["excluded"][0]["exclusion_reason"] == "over_budget"
    assert cuerpo["excluded"][0]["actual"] == 149.0
    assert cuerpo["excluded"][0]["required"] == 100


def test_el_detalle_devuelve_result_y_no_una_lista(cliente):
    cuerpo = con_catalogo(cliente, "/products/KD-001").json()
    assert cuerpo["result"]["product_id"] == "KD-001"
    assert "results" not in cuerpo


def test_un_identificador_absorbido_resuelve_y_no_es_product_not_found(cliente):
    cuerpo = con_catalogo(cliente, "/products/KD-024").json()
    assert cuerpo["result"]["product_id"] == "HL-021"


def test_un_identificador_inexistente_es_product_not_found(cliente):
    cuerpo = con_catalogo(cliente, "/products/KD-999").json()
    assert cuerpo["error_type"] == "product_not_found"


def test_ninguna_respuesta_lleva_los_campos_que_no_viajan(cliente):
    cuerpo = con_catalogo(cliente, "/products/search").json()
    for producto in cuerpo["results"]:
        for campo in ("description_quality", "tags", "stock", "alt_product_ids"):
            assert campo not in producto


def test_la_piedra_de_afilar_se_navega_aunque_no_sea_regalo(cliente):
    cuerpo = con_catalogo(
        cliente, "/products/by-category", category="Kitchen & Dining", limit=8, offset=8
    ).json()
    todos = {p["product_id"] for p in cuerpo["results"]}
    segunda = con_catalogo(
        cliente, "/products/by-category", category="Kitchen & Dining", limit=8, offset=16
    ).json()
    todos |= {p["product_id"] for p in segunda["results"]}
    primera = con_catalogo(
        cliente, "/products/by-category", category="Kitchen & Dining"
    ).json()
    todos |= {p["product_id"] for p in primera["results"]}
    assert "KD-003" in todos


def test_pero_la_busqueda_no_la_recomienda(cliente):
    cuerpo = con_catalogo(cliente, "/products/search").json()
    assert "KD-003" not in {p["product_id"] for p in cuerpo["results"]}


def test_ninguna_operacion_devuelve_mas_de_ocho(cliente):
    for ruta, parametros in (
        ("/products/search", {}),
        ("/products/by-category", {"category": "Home & Living"}),
    ):
        cuerpo = con_catalogo(cliente, ruta, **parametros).json()
        assert len(cuerpo["results"]) <= 8


# --------------------------------------------------------------------------
# Los errores recuperables viajan con HTTP 200
# --------------------------------------------------------------------------


def test_un_limite_fuera_de_rango_es_invalid_parameter(cliente):
    respuesta = con_catalogo(cliente, "/products/search", limit=20)
    assert respuesta.status_code == 200
    assert respuesta.json()["error_type"] == "invalid_parameter"


def test_un_presupuesto_contradictorio_es_conflicting_parameters(cliente):
    cuerpo = con_catalogo(
        cliente, "/products/search", min_price=100, max_price=50
    ).json()
    assert cuerpo["error_type"] == "conflicting_parameters"


def test_sin_relation_es_invalid_parameter(cliente):
    cuerpo = con_catalogo(cliente, "/products/related").json()
    assert cuerpo["error_type"] == "invalid_parameter"


def test_pairs_with_sin_product_id_es_missing_anchor(cliente):
    cuerpo = con_catalogo(cliente, "/products/related", relation="pairs_with").json()
    assert cuerpo["error_type"] == "missing_anchor"


def test_alternative_to_solo_con_precio_es_missing_anchor(cliente):
    cuerpo = con_catalogo(
        cliente, "/products/related", relation="alternative_to", max_price=50
    ).json()
    assert cuerpo["error_type"] == "missing_anchor"


def test_un_ancla_inexistente_es_product_not_found(cliente):
    cuerpo = con_catalogo(
        cliente, "/products/related", relation="alternative_to", product_id="KD-999"
    ).json()
    assert cuerpo["error_type"] == "product_not_found"


def test_los_relacionados_declaran_su_relation_type(cliente):
    cuerpo = con_catalogo(
        cliente, "/products/related", relation="alternative_to", product_id="HL-009"
    ).json()
    assert cuerpo["results"]
    assert cuerpo["results"][0]["relation_type"] in ("equivalent", "same_function")


def test_el_complemento_llega_aunque_no_sea_regalo_por_si_solo(cliente):
    cuerpo = con_catalogo(
        cliente, "/products/related", relation="pairs_with", product_id="KD-001"
    ).json()
    assert "KD-003" in {p["product_id"] for p in cuerpo["results"]}


# --------------------------------------------------------------------------
# B7 · las descripciones son las de la memoria, literales
# --------------------------------------------------------------------------

FRASES_DE_B7 = (
    "Returns the current normalized product categories in the catalog",
    "Browses products from one explicitly requested catalog category",
    "Primary cross-category product-discovery operation",
    "Returns products related to a product the customer has in mind",
    "Returns the complete catalog representation of one identified product",
    "A candidate from a lower level never overtakes one from a higher level",
    "A request with only a price boundary and no product or concept returns",
    "no numeric product score exists",
    "Do not call this operation merely to enrich a product already returned",
)


def _descripciones_escritas() -> str:
    arbol = ast.parse((RAIZ / "src" / "api.py").read_text(encoding="utf-8"))
    textos = [
        nodo.value.value
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.keyword)
        and nodo.arg == "description"
        and isinstance(nodo.value, ast.Constant)
    ]
    return "\n".join(textos)


@pytest.mark.parametrize("frase", FRASES_DE_B7)
def test_las_descripciones_son_las_de_b7(frase):
    assert frase in _descripciones_escritas()


def test_los_enum_publican_las_definiciones_del_vocabulario(cliente):
    especificacion = cliente.get("/openapi.json").json()
    esquemas = especificacion["components"]["schemas"]
    for campo in ("product_type", "use_case", "functional_family", "gift_risk"):
        assert esquemas[campo]["enum"]
        assert esquemas[campo]["description"].strip()


def test_la_especificacion_declara_catalogapikey(cliente):
    especificacion = cliente.get("/openapi.json").json()
    esquema = especificacion["components"]["securitySchemes"]["CatalogApiKey"]
    assert esquema == {"type": "apiKey", "in": "header", "name": "X-Api-Key"}


def test_las_cinco_operaciones_estan_publicadas(cliente):
    especificacion = cliente.get("/openapi.json").json()
    publicadas = {
        metodo["operationId"]
        for ruta in especificacion["paths"].values()
        for metodo in ruta.values()
    }
    assert publicadas == {
        "get_categories",
        "get_products_by_category",
        "find_products_by_criteria",
        "get_related_products",
        "get_product_details",
    }
