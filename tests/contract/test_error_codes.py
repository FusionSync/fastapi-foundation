import pytest

from core.exceptions import (
    AppError,
    ErrorCodeSpec,
    get_error_code,
    register_error_codes,
)


def test_app_error_rejects_unregistered_error_code() -> None:
    with pytest.raises(ValueError, match="Unregistered error code"):
        raise AppError("TEMPORARY_NEW_ERROR")


def test_apps_must_register_error_codes_before_throwing_them() -> None:
    register_error_codes(
        ErrorCodeSpec(
            "EXAMPLE_NOT_READY",
            409,
            "example is not ready",
            owner_module="example",
        )
    )

    error = AppError("EXAMPLE_NOT_READY")

    assert error.code == "EXAMPLE_NOT_READY"
    assert get_error_code("EXAMPLE_NOT_READY").default_http_status == 409
