"""Tests of the access boundary, the rate limit and the published contract.

The ones that need the service up use the `TestClient` of FastAPI. The two
credentials are injected through the environment, as in production: there is no
credential written in the code or in this file.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CATALOG_KEY = "catalog-key-for-tests"
DIAGNOSTICS_KEY = "diagnostics-key-for-tests"
os.environ["CATALOG_API_KEY"] = CATALOG_KEY
os.environ["DIAGNOSTICS_API_KEY"] = DIAGNOSTICS_KEY


@pytest.fixture(scope="module")
def client_():
    from fastapi.testclient import TestClient

    import api

    return TestClient(api.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def clean_counter():
    import api

    for queue in api._recent_requests.values():
        queue.clear()
    yield


def with_catalog(client_, path_, **parameters):
    return client_.get(path_, params=parameters, headers={"X-Api-Key": CATALOG_KEY})


# --------------------------------------------------------------------------
# B6.12 · the access boundary
# --------------------------------------------------------------------------


def test_without_credential_answers_401(client_):
    response = client_.get("/get_categories")
    assert response.status_code == 401
    assert response.json()["error_code"] == "unauthorized"
    assert response.json()["retryable"] is False
    assert response.json()["incident_id"]


def test_with_an_unknown_credential_answers_401(client_):
    response = client_.get("/get_categories", headers={"X-Api-Key": "not-a-real-one"})
    assert response.status_code == 401
    assert response.json()["error_code"] == "unauthorized"
    assert response.json()["retryable"] is False


def test_with_the_catalog_credential_answers_200(client_):
    assert with_catalog(client_, "/get_categories").status_code == 200


def test_the_catalog_credential_does_not_open_diagnostics(client_):
    response = client_.get(
        "/_diagnostics/load-report", headers={"X-Api-Key": CATALOG_KEY}
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "forbidden"
    assert response.json()["retryable"] is False


def test_the_diagnostics_credential_does_not_open_the_catalog(client_):
    response = client_.get("/get_categories", headers={"X-Api-Key": DIAGNOSTICS_KEY})
    assert response.status_code == 403
    assert response.json()["error_code"] == "forbidden"
    assert response.json()["retryable"] is False


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
# B6.8 · the rate limit
# --------------------------------------------------------------------------


def test_the_61st_request_of_the_minute_gets_429(client_):
    for number in range(60):
        assert with_catalog(client_, "/get_categories").status_code == 200, number
    exceeded = with_catalog(client_, "/get_categories")
    assert exceeded.status_code == 429
    assert exceeded.json()["error_code"] == "rate_limited"
    assert exceeded.json()["retryable"] is True
    assert exceeded.json()["incident_id"]


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
    assert exceeded.json()["error_code"] == "rate_limited"


def test_the_two_counters_are_independent(client_):
    for _ in range(10):
        client_.get("/_diagnostics/load-report", headers={"X-Api-Key": DIAGNOSTICS_KEY})
    assert with_catalog(client_, "/get_categories").status_code == 200


# --------------------------------------------------------------------------
# B4.8 · the envelope of each operation
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
# Recoverable errors travel with HTTP 200
# --------------------------------------------------------------------------


def test_a_limit_out_of_range_is_invalid_parameter(client_):
    response = with_catalog(client_, "/find_products_by_criteria", limit=20)
    assert response.status_code == 200
    assert response.json() == {
        "error_type": "invalid_parameter",
        "parameter": "limit",
        "received": "20",
    }


def test_a_contradictory_budget_is_conflicting_parameters(client_):
    body = with_catalog(
        client_, "/find_products_by_criteria", min_price=100, max_price=50
    ).json()
    assert body == {
        "error_type": "conflicting_parameters",
        "parameter": ["min_price", "max_price"],
        "received": {"min_price": 100, "max_price": 50},
    }


def test_without_relation_it_is_invalid_parameter(client_):
    body = with_catalog(client_, "/get_related_products").json()
    assert body == {"error_type": "invalid_parameter", "parameter": "relation"}


def test_negative_shipping_days_is_invalid_parameter(client_):
    response = with_catalog(
        client_, "/find_products_by_criteria", max_shipping_days=-2
    )
    assert response.status_code == 200
    assert response.json() == {
        "error_type": "invalid_parameter",
        "parameter": "max_shipping_days",
        "received": "-2",
    }


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
    assert "excluded" not in body

    body = with_catalog(
        client_,
        "/get_related_products",
        relation="alternative_to",
        product_id="HL-009",
        max_price=100,
        limit=5,
    ).json()
    assert "HL-010" in {product["product_id"] for product in body["results"]}
    assert body["excluded"]
    assert len(body["excluded"]) <= 2
    assert all(product["exclusion_reason"] == "over_budget" for product in body["excluded"])
    assert all(product["price"] > 100 for product in body["excluded"])
    assert all(product["actual"] == product["price"] for product in body["excluded"])
    assert all(product["required"] == 100 for product in body["excluded"])


def test_the_complement_arrives_though_not_a_gift_on_its_own(client_):
    body = with_catalog(
        client_, "/get_related_products", relation="pairs_with", product_id="KD-001"
    ).json()
    assert "KD-003" in {p["product_id"] for p in body["results"]}


# --------------------------------------------------------------------------
# B7 · the descriptions are the ones in the memory, literally
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
    parameters = _by_name(client_.get("/openapi.json").json(), "find_products_by_criteria")
    for field in ("use_case", "functional_family"):
        description_text = parameters[field]["description"]
        assert "`cooking`" in description_text or "`" in description_text
        assert len(description_text) > 200
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
# The specification publishes the parameters · the only thing indigo.ai reads
# --------------------------------------------------------------------------


def _by_name(specification, operation_id) -> dict:
    for path_ in specification["paths"].values():
        for method_ in path_.values():
            if method_.get("operationId") == operation_id:
                return {p["name"]: p for p in method_.get("parameters", [])}
    raise AssertionError(f"{operation_id} is not published")


def _parametros(specification, operation_id) -> set[str]:
    return {
        name
        for name, parameter in _by_name(specification, operation_id).items()
        if parameter.get("in") == "query"
    }


def _cabeceras(specification, operation_id) -> set[str]:
    return {
        name
        for name, parameter in _by_name(specification, operation_id).items()
        if parameter.get("in") == "header"
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


def test_the_five_operations_use_security_not_a_credential_parameter(client_):
    specification = client_.get("/openapi.json").json()
    for path_ in specification["paths"].values():
        for method_ in path_.values():
            assert _cabeceras(specification, method_["operationId"]) == set()
            assert method_["security"] == [{"CatalogApiKey": []}]


def test_relation_is_the_only_required_related_parameter(client_):
    specification = client_.get("/openapi.json").json()
    parameters = _by_name(specification, "get_related_products")
    required = {name for name, parameter in parameters.items() if parameter.get("required")}
    assert required == {"relation"}

    navigation = _by_name(specification, "get_products_by_category")
    search = _by_name(specification, "find_products_by_criteria")
    assert navigation["limit"]["schema"]["minimum"] == 1
    assert navigation["limit"]["schema"]["maximum"] == 8
    assert navigation["offset"]["schema"]["minimum"] == 0
    assert search["limit"]["schema"]["minimum"] == 1
    assert search["limit"]["schema"]["maximum"] == 8
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 5


def test_product_type_is_not_an_enum(client_):
    specification = client_.get("/openapi.json").json()
    for operation in ("find_products_by_criteria", "get_related_products"):
        for path_ in specification["paths"].values():
            for method_ in path_.values():
                if method_.get("operationId") != operation:
                    continue
                for parameter in method_["parameters"]:
                    if parameter["name"] == "product_type":
                        assert "enum" not in str(parameter["schema"])


def test_closed_vocabularies_do_travel_with_their_definitions(client_):
    parameters = _by_name(client_.get("/openapi.json").json(), "find_products_by_criteria")
    assert "cooking" in parameters["use_case"]["description"]
    assert "gender_specific" in parameters["recipient"]["description"]


def test_each_operation_declares_its_response_shape(client_):
    specification = client_.get("/openapi.json").json()
    for path_ in specification["paths"].values():
        for method_ in path_.values():
            schema = method_["responses"]["200"]["content"]["application/json"]["schema"]
            expected = "".join(
                part.capitalize() for part in method_["operationId"].split("_")
            ) + "Response"
            if method_["operationId"] == "get_categories":
                assert schema == {"$ref": f"#/components/schemas/{expected}"}
            else:
                assert schema["oneOf"] == [
                    {"$ref": f"#/components/schemas/{expected}"},
                    {"$ref": "#/components/schemas/RecoverableError"},
                ]


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
    specification = client_.get("/openapi.json").json()
    for path_, metodos in specification["paths"].items():
        for method_ in metodos.values():
            operation = method_["operationId"]
            assert path_ == f"/{operation}", (path_, operation)
            schema = method_["responses"]["200"]["content"]["application/json"]["schema"]
            reference = schema.get("$ref", "")
            if not reference:
                reference = schema["oneOf"][0]["$ref"]
            expected = "".join(parte.capitalize() for parte in operation.split("_")) + "Response"
            assert reference.endswith(expected), (reference, expected)


def test_no_published_schema_carries_an_invented_name(client_):
    schemas = set(client_.get("/openapi.json").json()["components"]["schemas"])
    agreed = {
        "Product",
        "ExcludedProduct",
        "CategorySummary",
        "NotApplied",
        "RelatedProduct",
        "RecoverableError",
        "TechnicalFailure",
        "UseCase",
        "FunctionalFamily",
        "GiftRisk",
        "SuitableRelationship",
    }
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
    extra_ = schemas - agreed - derived
    assert not extra_, extra_


# --------------------------------------------------------------------------
# B7.10 · closed vocabularies as real enums, `product_type` as free text
# --------------------------------------------------------------------------


def test_the_closed_vocabularies_are_published_as_enums(client_):
    schemas = client_.get("/openapi.json").json()["components"]["schemas"]
    assert len(schemas["UseCase"]["enum"]) == 30
    assert len(schemas["FunctionalFamily"]["enum"]) == 31
    assert schemas["GiftRisk"]["enum"] == ["high_commitment", "low", "taste_dependent"]
    assert len(schemas["SuitableRelationship"]["enum"]) == 5
    assert schemas["RecoverableError"]["properties"]["error_type"]["enum"] == [
        "invalid_parameter",
        "conflicting_parameters",
        "missing_anchor",
        "product_not_found",
    ]
    assert schemas["TechnicalFailure"]["properties"]["error_code"]["enum"] == [
        "service_unavailable",
        "unauthorized",
        "forbidden",
        "rate_limited",
    ]
    assert schemas["RelatedProduct"]["properties"]["relation_type"]["anyOf"][0]["enum"] == [
        "equivalent",
        "same_function",
    ]


def test_the_enum_parameters_reference_those_schemas(client_):
    parameters = _by_name(client_.get("/openapi.json").json(), "find_products_by_criteria")
    assert "UseCase" in str(parameters["use_case"]["schema"])
    assert "FunctionalFamily" in str(parameters["functional_family"]["schema"])
    assert "SuitableRelationship" in str(parameters["relationship"]["schema"])


def test_the_shared_criteria_are_described_the_same_in_both_operations(client_):
    specification = client_.get("/openapi.json").json()
    search = _by_name(specification, "find_products_by_criteria")
    related = _by_name(specification, "get_related_products")
    for name in (
        "product_type",
        "functional_family",
        "use_case",
        "category",
        "subcategory",
        "max_price",
        "min_price",
        "target_price",
    ):
        assert search[name]["description"] == related[name]["description"], name


# --------------------------------------------------------------------------
# B5.3 · foreseeable validation errors travel as 200
# --------------------------------------------------------------------------


def test_a_value_outside_an_enum_is_a_recoverable_error(client_):
    response = with_catalog(client_, "/find_products_by_criteria", use_case="not_a_situation")
    assert response.status_code == 200
    assert response.json() == {
        "error_type": "invalid_parameter",
        "parameter": "use_case",
        "received": "not_a_situation",
    }


def test_a_malformed_number_is_a_recoverable_error(client_):
    response = with_catalog(client_, "/find_products_by_criteria", max_price="cheap")
    assert response.status_code == 200
    assert response.json() == {
        "error_type": "invalid_parameter",
        "parameter": "max_price",
        "received": "cheap",
    }


def test_no_operation_declares_a_422(client_):
    specification = client_.get("/openapi.json").json()
    for path_ in specification["paths"].values():
        for method_ in path_.values():
            assert "422" not in method_["responses"]


def test_every_operation_declares_401_403_429_and_5xx(client_):
    specification = client_.get("/openapi.json").json()
    for path_ in specification["paths"].values():
        for method_ in path_.values():
            assert {"401", "403", "429", "503"} <= set(method_["responses"])
            for code in ("401", "403", "429", "503"):
                schema = method_["responses"][code]["content"]["application/json"]["schema"]
                assert schema == {"$ref": "#/components/schemas/TechnicalFailure"}


def test_an_internal_failure_is_not_disguised_as_empty_catalog(client_, monkeypatch):
    import api

    def fail():
        raise RuntimeError("must-not-travel")

    monkeypatch.setattr(api.catalog, "all_products", fail)
    response = with_catalog(client_, "/get_categories")
    assert response.status_code == 503
    assert response.json()["error_code"] == "service_unavailable"
    assert response.json()["retryable"] is False
    assert response.json()["incident_id"]
    assert "must-not-travel" not in response.text


# --------------------------------------------------------------------------
# B7.8 · the response fields that condition what the agent may claim
# --------------------------------------------------------------------------


def test_the_response_fields_of_b7_8_carry_their_description(client_):
    schemas = client_.get("/openapi.json").json()["components"]["schemas"]
    assert schemas["FindProductsByCriteriaResponse"]["properties"]["results"]["description"]
    assert schemas["FindProductsByCriteriaResponse"]["properties"]["excluded"]["description"]
    assert schemas["FindProductsByCriteriaResponse"]["properties"]["not_applied"]["description"]
    assert schemas["FindProductsByCriteriaResponse"]["properties"]["query_understood"][
        "description"
    ]
    assert schemas["GetProductsByCategoryResponse"]["properties"]["total"]["description"]
    assert schemas["GetProductsByCategoryResponse"]["properties"]["offset"]["description"]
    assert schemas["ExcludedProduct"]["properties"]["exclusion_reason"]["description"]
    assert schemas["RelatedProduct"]["properties"]["relation_type"]["description"]
    for field in (
        "gift_risk",
        "is_standalone_gift",
        "in_stock",
        "stocking_filler",
        "pairs_with",
        "alternative_to",
    ):
        assert schemas["Product"]["properties"][field]["description"], field


# --------------------------------------------------------------------------
# v34 → v35 · the exact-match restriction does not reach related products
# --------------------------------------------------------------------------


def test_related_products_may_be_of_another_type(client_):
    body = with_catalog(
        client_, "/get_related_products", relation="alternative_to", product_type="chef_knife"
    ).json()
    types = {p["product_type"] for p in body["results"]}
    assert types - {"chef_knife"}, "a substitute is very often another object"

    body = with_catalog(
        client_, "/get_related_products", relation="alternative_to", use_case="cooking"
    ).json()
    assert body["results"]
    assert all("cooking" in p["use_case"] for p in body["results"])
    assert all(p["relation_type"] == "same_function" for p in body["results"])


def test_but_the_search_does_restrict_to_the_exact_type(client_):
    body = with_catalog(client_, "/find_products_by_criteria", product_type="chef_knife").json()
    assert {p["product_type"] for p in body["results"]} <= {"chef_knife"}
