import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import Field

from core.app import create_app
from core.base import Schema
from core.config import Settings
from core.serialization import ok

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "serialization"


class GoldenStatus(StrEnum):
    READY = "ready"


class GoldenPayload(Schema):
    external_id: UUID = Field(alias="externalId")
    created_at: datetime
    business_date: date
    amount: Decimal
    status: GoldenStatus
    tags: tuple[str, ...]
    nested: list[dict[str, object]]
    optional_note: str | None = None


def test_serialization_complex_type_golden_examples_match_documented_contract() -> None:
    response = ok(
        GoldenPayload(
            externalId=UUID("12345678-1234-5678-1234-567812345678"),
            created_at=datetime(2026, 5, 28, 10, 30, 45, tzinfo=UTC),
            business_date=date(2026, 5, 28),
            amount=Decimal("123.4500"),
            status=GoldenStatus.READY,
            tags=("bid", "review"),
            nested=[
                {
                    "amount": Decimal("0.10"),
                    "created_at": datetime(2026, 5, 28, 11, 0, tzinfo=UTC),
                }
            ],
        ),
        request_id="req_golden",
    )

    assert response == _load_contract_json("golden-examples.json")


def test_example_app_openapi_schema_regression_matches_documented_contract() -> None:
    client = TestClient(create_app(Settings(installed_apps=["apps.example_domain.module"])))

    document = client.get("/openapi.json").json()

    assert _example_app_openapi_contract(document) == _load_contract_json(
        "example-openapi-schema.json"
    )


def _example_app_openapi_contract(document: dict[str, object]) -> dict[str, object]:
    schema_names = [
        "Envelope_ExamplePing_",
        "ExamplePing",
        "ExampleRead",
        "ListEnvelope_ExampleRead_",
        "Pagination",
    ]
    components = document["components"]  # type: ignore[index]
    schemas = components["schemas"]  # type: ignore[index]
    paths = document["paths"]  # type: ignore[index]
    return {
        "schemas": {name: schemas[name] for name in schema_names},  # type: ignore[index]
        "paths": {
            "/api/v1/examples": paths["/api/v1/examples"],  # type: ignore[index]
            "/api/v1/examples/ping": paths["/api/v1/examples/ping"],  # type: ignore[index]
        },
    }


def _load_contract_json(file_name: str) -> dict[str, object]:
    return json.loads((CONTRACT_DIR / file_name).read_text(encoding="utf-8"))
