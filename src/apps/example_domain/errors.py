from core.exceptions import ModuleErrorCode, define_module_error_codes

EXAMPLE_DOMAIN_NOT_READY = "EXAMPLE_DOMAIN_NOT_READY"

ERROR_CODES = define_module_error_codes(
    "example_domain",
    ModuleErrorCode(
        EXAMPLE_DOMAIN_NOT_READY,
        409,
        "Example Domain is not ready",
        details_schema={"reason": "str"},
    ),
)

__all__ = ["ERROR_CODES", "EXAMPLE_DOMAIN_NOT_READY"]
