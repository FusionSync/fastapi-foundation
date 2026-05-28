from apps.example_domain.schemas import ExamplePing
from core.base.services import BaseService


class ExampleService(BaseService):
    def ping(self) -> ExamplePing:
        return ExamplePing(app="example_domain", status="ready")
