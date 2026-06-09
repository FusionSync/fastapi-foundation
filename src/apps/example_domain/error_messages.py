from apps.example_domain.errors import ERROR_CODES, EXAMPLE_DOMAIN_NOT_READY
from core.messages import ModuleMessageCatalog, define_module_message_catalogs

MESSAGE_CATALOGS = define_module_message_catalogs(
    "example_domain",
    error_codes=ERROR_CODES,
    catalogs=[
        ModuleMessageCatalog(
            locale="en-US",
            messages={EXAMPLE_DOMAIN_NOT_READY: "Example Domain is not ready"},
        )
    ],
)

__all__ = ["MESSAGE_CATALOGS"]
