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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CATALOG_KEY = "key_-de-catalog-para-pruebas"
DIAGNOSTICS_KEY = "key_-de-diagnostico-para-pruebas"
os.environ["CATALOG_API_KEY"] = CATALOG_KEY
os.environ["DIAGNOSTICS_API_KEY"] = DIAGNOSTICS_KEY


@pytest.fixture(scope="module")
def client_():
    from fastapi.testclient import TestClient

    import api

    return TestClient(api.app)


@pytest.fixture(autouse=True)
def clean_counter():
    import api

    for queue in api._recent_requests.values():
        queue.clear()
    yield


def with_catalog(client_, path_, **parameters):
    return client_.get(path_, params=parameters, headers={"X-Api-Key": CATALOG_KEY})


# --------------------------------------------------------------------------
# B6.12 · la frontera de acceso
# --------------------------------------------------------------------------


def test_without_credential_answers_401(client_):
    assert client_.get("/get_categories").status_code == 401


def test_with_an_unknown_credential_answers_401(client_):
    response = client_.get("/get_categories", headers={"X-Api-Key": "no-es-ninguna"})
    assert response.status_code == 401


def test_with_the_catalog_credential_answers_200(client_):
    assert with_catalog(client_, "/get_categories").status_code == 200


def test_the_catalog_credential_does_not_open_diagnostics(client_):
    response = client_.get(
        "/_diagnostics/load-report", headers={"X-Api-Key": CATALOG_KEY}
    )
    assert response.status_code == 403


def test_the_diagnostics_credential_does_not_open_the_catalog(client_):
    response = client_.get("/get_categories", headers={"X-Api-Key": DIAGNOSTICS_KEY})
    assert response.status_code == 403


def test_diagnostics_with_its_credential_answers_200(client_):
    response = client_.get(
        "/_diagnostics/load-report", headers={"X-Api-Key": DIAGNOSTICS_KEY}
    )
    assert response.status_code == 200
    assert response.json()["products"] == 150


def test_the_specification_and_the_documentation_are_open(client_):
    assert client_.get("/openapi.json").status_code == 200
    assert client_.get("/docs").status_code == 200


def test_diagnostics_is_not_in_the_specification(client_):
    specification = client_.get("/openapi.json").json()
    assert "/_diagnostics/load-report" not in specification["paths"]


def test_the_specification_contains_no_credential(client_):
    text_ = client_.get("/openapi.json").text
    assert CATALOG_KEY not in text_
    assert DIAGNOSTICS_KEY not in text_


# --------------------------------------------------------------------------
# B6.8 · el límite de tasa
# --------------------------------------------------------------------------


def test_the_61st_request_of_the_minute_gets_429(client_):
    for number in range(60):
        assert with_catalog(client_, "/get_categories").status_code == 200, number
    exceeded = with_catalog(client_, "/get_categories")
    assert exceeded.status_code == 429
    assert exceeded.json()["detail"]["error_code"] == "rate_limited"


def test_diagnostics_has_its_own_limit(client_):
    for _ in range(10):
        assert (
            client_.get(
                "/_diagnostics/load-report", headers={"X-Api-Key": DIAGNOSTICS_KEY}
            ).status_code
            == 200
        )
    exceeded = client_.get(
        "/_diagnostics/load-report", headers={"X-Api-Key": DIAGNOSTICS_KEY}
    )
    assert exceeded.status_code == 429


def test_the_two_counters_are_independent(client_):
    for _ in range(10):
        client_.get("/_diagnostics/load-report", headers={"X-Api-Key": DIAGNOSTICS_KEY})
    assert with_catalog(client_, "/get_categories").status_code == 200


# --------------------------------------------------------------------------
# B4.8 · los envelopes de cada operación
# --------------------------------------------------------------------------


def test_get_categories_returns_eleven_categories(client_):
    body = with_catalog(client_, "/get_categories").json()
    assert len(body["results"]) == 11
    assert body["currency"] == "EUR"
    assert "total" not in body and "excluded" not in body


def test_the_navigation_carries_total_and_offset(client_):
    body = with_catalog(
        client_, "/get_products_by_category", category="Kitchen & Dining"
    ).json()
    # 22 en la categoría, 2 agotados: navegar no exige `is_standalone_gift`.
    assert body["total"] == 20
    assert body["offset"] == 0
    assert len(body["results"]) <= 8
    assert "not_applied" not in body


def test_the_search_does_not_paginate_and_may_carry_not_applied(client_):
    body = with_catalog(client_, "/find_products_by_criteria", product_type="santoku").json()
    assert "total" not in body and "offset" not in body
    assert body["not_applied"] == [
        {"parameter": "product_type", "received": "santoku", "reason": "unresolved"}
    ]


def test_the_alias_gyuto_resolves_to_chef_knife(client_):
    body = with_catalog(client_, "/find_products_by_criteria", product_type="gyuto").json()
    assert body["query_understood"]["product_type"] == "chef_knife"
    assert "not_applied" not in body


def test_the_knife_scenario_through_the_api(client_):
    body = with_catalog(
        client_, "/find_products_by_criteria", product_type="chef_knife", max_price=100
    ).json()
    assert body["results"] == []
    assert body["excluded"][0]["product_id"] == "KD-001"
    assert body["excluded"][0]["exclusion_reason"] == "over_budget"
    assert body["excluded"][0]["actual"] == 149.0
    assert body["excluded"][0]["required"] == 100


def test_the_detail_returns_result_and_not_a_list(client_):
    body = with_catalog(client_, "/get_product_details", product_id="KD-001").json()
    assert body["result"]["product_id"] == "KD-001"
    assert "results" not in body


def test_an_absorbed_identifier_resolves_and_is_not_product_not_found(client_):
    body = with_catalog(client_, "/get_product_details", product_id="KD-024").json()
    assert body["result"]["product_id"] == "HL-021"


def test_an_unknown_identifier_is_product_not_found(client_):
    body = with_catalog(client_, "/get_product_details", product_id="KD-999").json()
    assert body["error_type"] == "product_not_found"


def test_no_response_carries_the_fields_that_do_not_travel(client_):
    body = with_catalog(client_, "/find_products_by_criteria").json()
    for product in body["results"]:
        for field in ("description_quality", "tags", "stock", "alt_product_ids"):
            assert field not in product


def test_the_sharpening_stone_is_browsable_though_not_a_gift(client_):
    body = with_catalog(
        client_, "/get_products_by_category", category="Kitchen & Dining", limit=8, offset=8
    ).json()
    all_products = {p["product_id"] for p in body["results"]}
    second_ = with_catalog(
        client_, "/get_products_by_category", category="Kitchen & Dining", limit=8, offset=16
    ).json()
    all_products |= {p["product_id"] for p in second_["results"]}
    first_ = with_catalog(
        client_, "/get_products_by_category", category="Kitchen & Dining"
    ).json()
    all_products |= {p["product_id"] for p in first_["results"]}
    assert "KD-003" in all_products


def test_but_the_search_does_not_recommend_it(client_):
    body = with_catalog(client_, "/find_products_by_criteria").json()
    assert "KD-003" not in {p["product_id"] for p in body["results"]}


def test_no_operation_returns_more_than_eight(client_):
    for path_, parameters in (
        ("/find_products_by_criteria", {}),
        ("/get_products_by_category", {"category": "Home & Living"}),
    ):
        body = with_catalog(client_, path_, **parameters).json()
        assert len(body["results"]) <= 8


# --------------------------------------------------------------------------
# Los errores recuperables viajan con HTTP 200
# --------------------------------------------------------------------------


def test_a_limit_out_of_range_is_invalid_parameter(client_):
    response = with_catalog(client_, "/find_products_by_criteria", limit=20)
    assert response.status_code == 200
    assert response.json()["error_type"] == "invalid_parameter"


def test_a_contradictory_budget_is_conflicting_parameters(client_):
    body = with_catalog(
        client_, "/find_products_by_criteria", min_price=100, max_price=50
    ).json()
    assert body["error_type"] == "conflicting_parameters"


def test_without_relation_it_is_invalid_parameter(client_):
    body = with_catalog(client_, "/get_related_products").json()
    assert body["error_type"] == "invalid_parameter"


def test_pairs_with_without_product_id_is_missing_anchor(client_):
    body = with_catalog(client_, "/get_related_products", relation="pairs_with").json()
    assert body["error_type"] == "missing_anchor"


def test_alternative_to_with_only_a_price_is_missing_anchor(client_):
    body = with_catalog(
        client_, "/get_related_products", relation="alternative_to", max_price=50
    ).json()
    assert body["error_type"] == "missing_anchor"


def test_an_unknown_anchor_is_product_not_found(client_):
    body = with_catalog(
        client_, "/get_related_products", relation="alternative_to", product_id="KD-999"
    ).json()
    assert body["error_type"] == "product_not_found"


def test_related_products_declare_their_relation_type(client_):
    body = with_catalog(
        client_, "/get_related_products", relation="alternative_to", product_id="HL-009"
    ).json()
    assert body["results"]
    assert body["results"][0]["relation_type"] in ("equivalent", "same_function")


def test_the_complement_arrives_though_not_a_gift_on_its_own(client_):
    body = with_catalog(
        client_, "/get_related_products", relation="pairs_with", product_id="KD-001"
    ).json()
    assert "KD-003" in {p["product_id"] for p in body["results"]}


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
    arbol = ast.parse((ROOT / "src" / "api.py").read_text(encoding="utf-8"))
    textos = [
        nodo.value.value
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.keyword)
        and nodo.arg == "description"
        and isinstance(nodo.value, ast.Constant)
    ]
    return "\n".join(textos)


@pytest.mark.parametrize("frase", FRASES_DE_B7)
def test_the_descriptions_are_the_ones_of_b7(frase):
    assert frase in _descripciones_escritas()


def test_vocabularies_travel_with_their_definitions_in_each_parameter(client_):
    """Las definiciones viven en el parámetro que las necesita, no en un schema suelto.

    Un `enum` de treinta values_ sin definiciones obliga al modelo a adivinar qué
    significa cada one_, y un schema aparte en `components` no lo lee al construir
    la llamada: lo que lee es la descripción del parámetro.
    """
    parameters = _por_nombre(client_.get("/openapi.json").json(), "find_products_by_criteria")
    for field in ("use_case", "functional_family"):
        description_text = parameters[field]["description"]
        assert "`cooking`" in description_text or "`" in description_text
        assert len(description_text) > 200  # lleva las definiciones, no solo el nombre
    assert "aliases" in parameters["product_type"]["description"] or "alias" in (
        parameters["product_type"]["description"]
    )


def test_the_specification_declares_catalogapikey(client_):
    specification = client_.get("/openapi.json").json()
    schema = specification["components"]["securitySchemes"]["CatalogApiKey"]
    assert schema == {"type": "apiKey", "in": "header", "name": "X-Api-Key"}


def test_the_five_operations_are_published(client_):
    specification = client_.get("/openapi.json").json()
    publicadas = {
        method_["operationId"]
        for path_ in specification["paths"].values()
        for method_ in path_.values()
    }
    assert publicadas == {
        "get_categories",
        "get_products_by_category",
        "find_products_by_criteria",
        "get_related_products",
        "get_product_details",
    }


# --------------------------------------------------------------------------
# La especificación publica los parámetros · lo único que indigo.ai lee
# --------------------------------------------------------------------------


def _por_nombre(specification, operation_id) -> dict:
    for path_ in specification["paths"].values():
        for method_ in path_.values():
            if method_.get("operationId") == operation_id:
                return {p["name"]: p for p in method_.get("parameters", [])}
    raise AssertionError(f"{operation_id} no está publicada")


def _parametros(specification, operation_id) -> set[str]:
    """Los parámetros de negocio: los que viajan en la query.

    `X-Api-Key` también se publica, y debe publicarse —es parte del contrato—,
    pero es una cabecera y no un criterio: se cuenta aparte.
    """
    return {
        nombre
        for nombre, parametro in _por_nombre(specification, operation_id).items()
        if parametro.get("in") == "query"
    }


def _cabeceras(specification, operation_id) -> set[str]:
    return {
        nombre
        for nombre, parametro in _por_nombre(specification, operation_id).items()
        if parametro.get("in") == "header"
    }


def test_the_search_publishes_its_19_parameters(client_):
    published = _parametros(client_.get("/openapi.json").json(), "find_products_by_criteria")
    assert published == {
        "max_price",
        "target_price",
        "min_price",
        "recipient",
        "relationship",
        "occasion",
        "use_case",
        "functional_family",
        "buyer_knows_recipient",
        "product_type",
        "category",
        "subcategory",
        "brand",
        "color",
        "material",
        "max_shipping_days",
        "gift_wrap_required",
        "stocking_filler",
        "limit",
    }


def test_related_publishes_its_20_parameters(client_):
    published = _parametros(client_.get("/openapi.json").json(), "get_related_products")
    assert len(published) == 20
    assert {"relation", "product_id"} <= published
    assert "stocking_filler" not in published


def test_the_navigation_publishes_its_8_parameters(client_):
    published = _parametros(client_.get("/openapi.json").json(), "get_products_by_category")
    assert published == {
        "category",
        "max_price",
        "target_price",
        "min_price",
        "max_shipping_days",
        "sort",
        "limit",
        "offset",
    }


def test_the_five_operations_declare_the_credential_header(client_):
    specification = client_.get("/openapi.json").json()
    for operation in (
        "get_categories",
        "get_products_by_category",
        "find_products_by_criteria",
        "get_related_products",
        "get_product_details",
    ):
        assert "x-api-key" in {c.lower() for c in _cabeceras(specification, operation)}


def test_product_type_is_not_an_enum(client_):
    specification = client_.get("/openapi.json").json()
    for operation in ("find_products_by_criteria", "get_related_products"):
        for path_ in specification["paths"].values():
            for method_ in path_.values():
                if method_.get("operationId") != operation:
                    continue
                for parametro in method_["parameters"]:
                    if parametro["name"] == "product_type":
                        assert "enum" not in str(parametro["schema"])


def test_closed_vocabularies_do_travel_with_their_definitions(client_):
    parameters = _por_nombre(client_.get("/openapi.json").json(), "find_products_by_criteria")
    assert "cooking" in parameters["use_case"]["description"]
    assert parameters["recipient"]["description"]


def test_each_operation_declares_its_response_shape(client_):
    specification = client_.get("/openapi.json").json()
    for path_ in specification["paths"].values():
        for method_ in path_.values():
            schema = method_["responses"]["200"]["content"]["application/json"]["schema"]
            assert schema.get("$ref") or schema.get("type")


def test_the_navigation_order_responds_to_sort(client_):
    cheap = with_catalog(
        client_, "/get_products_by_category", category="Jewellery", sort="price_asc"
    ).json()
    expensive = with_catalog(
        client_, "/get_products_by_category", category="Jewellery", sort="price_desc"
    ).json()
    prices = [p["price"] for p in cheap["results"]]
    assert prices == sorted(prices)
    assert expensive["results"][0]["price"] >= cheap["results"][0]["price"]


def test_stocking_filler_switches_on_the_filling_mechanic(client_):
    body = with_catalog(client_, "/find_products_by_criteria", stocking_filler="true").json()
    assert len(body["results"]) == 5
    assert all(p["stocking_filler"] for p in body["results"])
    assert all(p["price"] <= 28 for p in body["results"])


def test_an_absent_stocking_filler_does_not_cut(client_):
    body = with_catalog(client_, "/find_products_by_criteria").json()
    assert "stocking_filler" not in body["query_understood"]


def test_the_related_one_carries_relation_type_in_the_product_itself(client_):
    body = with_catalog(
        client_, "/get_related_products", relation="alternative_to", product_id="HL-009"
    ).json()
    first_element = body["results"][0]
    assert "product_id" in first_element and "relation_type" in first_element


def test_each_operation_is_named_the_same_in_the_three_places(client_):
    """La path_, el `operation_id` y el schema de response, con el mismo nombre."""
    specification = client_.get("/openapi.json").json()
    for path_, metodos in specification["paths"].items():
        for method_ in metodos.values():
            operation = method_["operationId"]
            assert path_ == f"/{operation}", (path_, operation)
            schema = method_["responses"]["200"]["content"]["application/json"]["schema"]
            reference = schema.get("$ref", "")
            expected = "".join(parte.capitalize() for parte in operation.split("_")) + "Response"
            assert reference.endswith(expected), (reference, expected)


def test_no_published_schema_carries_an_invented_name(client_):
    schemas = set(client_.get("/openapi.json").json()["components"]["schemas"])
    agreed = {"Product", "ExcludedProduct", "CategorySummary", "NotApplied", "RelatedProduct"}
    derived = {
        "".join(p.capitalize() for p in operation.split("_")) + "Response"
        for operation in (
            "get_categories",
            "get_products_by_category",
            "find_products_by_criteria",
            "get_related_products",
            "get_product_details",
        )
    }
    extra_ = {e for e in schemas if not e.startswith("HTTPValidationError")}
    extra_ -= agreed | derived | {"ValidationError"}
    assert not extra_, extra_
