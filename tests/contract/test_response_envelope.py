from core.serialization import fail, ok, ok_list


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
