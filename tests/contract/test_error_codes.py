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
            details_schema={},
            deprecated=False,
        )
    )

    error = AppError("EXAMPLE_NOT_READY")

    assert error.code == "EXAMPLE_NOT_READY"
    assert get_error_code("EXAMPLE_NOT_READY").default_http_status == 409


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        (
            ErrorCodeSpec(
                "MISSING_OWNER",
                400,
                "missing owner",
                details_schema={},
                deprecated=False,
            ),
            "Owner module metadata is required",
        ),
        (
            ErrorCodeSpec(
                "MISSING_DETAILS_SCHEMA",
                400,
                "missing details schema",
                owner_module="example",
                deprecated=False,
            ),
            "Details schema metadata is required",
        ),
        (
            ErrorCodeSpec(
                "MISSING_DEPRECATION",
                400,
                "missing deprecation metadata",
                owner_module="example",
                details_schema={},
            ),
            "Deprecated flag metadata is required",
        ),
        (
            ErrorCodeSpec(
                "BAD_DETAILS_SCHEMA",
                400,
                "bad details schema",
                owner_module="example",
                details_schema=[],  # type: ignore[arg-type]
                deprecated=False,
            ),
            "Details schema metadata must be a dict",
        ),
    ],
)
def test_error_code_specs_require_explicit_metadata(
    spec: ErrorCodeSpec,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        register_error_codes(spec)
