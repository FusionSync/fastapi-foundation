from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

import pytest

from core.exceptions import AppError
from core.serialization import fail, ok, ok_list


class ReviewStatus(StrEnum):
    READY = "ready"


def test_ok_envelope_contains_all_fields() -> None:
    response = ok({"id": "1"}, request_id="req_test")

    assert response == {
        "code": "OK",
        "message": "success",
        "data": {"id": "1"},
        "list": None,
        "pagination": None,
        "details": None,
        "request_id": "req_test",
    }


def test_list_envelope_contains_pagination() -> None:
    response = ok_list(
        [{"id": "1"}],
        {"total": 1, "page": 1, "page_size": 20, "has_next": False},
        request_id="req_test",
    )

    assert response["data"] is None
    assert response["list"] == [{"id": "1"}]
    assert response["pagination"] == {
        "total": 1,
        "page": 1,
        "page_size": 20,
        "has_next": False,
    }
    assert response["request_id"] == "req_test"


def test_fail_envelope_contains_null_payload_fields() -> None:
    response = fail(
        "PERMISSION_DENIED",
        message="denied",
        details={"resource": "workspace"},
        request_id="req_test",
    )

    assert response["code"] == "PERMISSION_DENIED"
    assert response["data"] is None
    assert response["list"] is None
    assert response["pagination"] is None
    assert response["details"] == {"resource": "workspace"}


def test_ok_envelope_serializes_golden_json_types() -> None:
    response = ok(
        {
            "created_at": datetime(2026, 5, 28, 10, 30, 45, tzinfo=UTC),
            "business_date": date(2026, 5, 28),
            "amount": Decimal("123.4500"),
            "id": UUID("12345678-1234-5678-1234-567812345678"),
            "status": ReviewStatus.READY,
            "nested": [{"amount": Decimal("0.10")}],
        },
        request_id="req_test",
    )

    assert response["data"] == {
        "created_at": "2026-05-28T10:30:45+00:00",
        "business_date": "2026-05-28",
        "amount": "123.4500",
        "id": "12345678-1234-5678-1234-567812345678",
        "status": "ready",
        "nested": [{"amount": "0.10"}],
    }


def test_ok_list_serializes_items_before_envelope_dump() -> None:
    response = ok_list(
        [{"id": UUID("12345678-1234-5678-1234-567812345678")}],
        {"total": 1, "page": 1, "page_size": 20, "has_next": False},
        request_id="req_test",
    )

    assert response["list"] == [{"id": "12345678-1234-5678-1234-567812345678"}]


def test_naive_datetime_is_rejected_before_api_output() -> None:
    with pytest.raises(AppError) as rejected:
        ok({"created_at": datetime(2026, 5, 28, 10, 30, 45)}, request_id="req_test")

    assert rejected.value.code == "SYSTEM_ERROR"
    assert rejected.value.details == {"reason": "naive_datetime"}
