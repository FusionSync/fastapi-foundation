from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings


def test_example_route_openapi_uses_typed_response_envelope() -> None:
    client = TestClient(create_app(Settings(installed_apps=["apps.example_domain.module"])))

    response = client.get("/openapi.json")

    assert response.status_code == 200
    document = response.json()
    response_schema = document["paths"]["/api/v1/examples/ping"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    envelope_schema = _resolve_schema(document, response_schema)
    data_schema = envelope_schema["properties"]["data"]

    assert envelope_schema["properties"]["code"]["type"] == "string"
    assert envelope_schema["properties"]["request_id"]["type"] == "string"
    assert _schema_ref_name(data_schema) == "ExamplePing"


def _resolve_schema(document: dict[str, object], schema: dict[str, object]) -> dict[str, object]:
    ref_name = _schema_ref_name(schema)
    return document["components"]["schemas"][ref_name]  # type: ignore[index]


def _schema_ref_name(schema: dict[str, object]) -> str:
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    any_of = schema.get("anyOf")
    assert isinstance(any_of, list)
    ref_schema = next(item for item in any_of if isinstance(item, dict) and "$ref" in item)
    return _schema_ref_name(ref_schema)
