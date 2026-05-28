from datetime import UTC, datetime

from apps.example_domain.schemas import ExampleListQuery, ExamplePing, ExampleRead
from core.base import SortTerm
from core.base.services import BaseService


class ExampleService(BaseService):
    def ping(self) -> ExamplePing:
        return ExamplePing(app="example_domain", status="ready")

    def list_examples(self, query: ExampleListQuery) -> tuple[list[ExampleRead], int]:
        records = [
            ExampleRead(
                id="example-1",
                created_at=datetime(2026, 5, 28, 11, 0, tzinfo=UTC),
                updated_at=datetime(2026, 5, 28, 11, 0, tzinfo=UTC),
                tenant_id="tenant-demo",
                title="demo overview",
            ),
            ExampleRead(
                id="example-2",
                created_at=datetime(2026, 5, 28, 10, 0, tzinfo=UTC),
                updated_at=datetime(2026, 5, 28, 10, 0, tzinfo=UTC),
                tenant_id="tenant-demo",
                title="demo contract",
            ),
        ]
        filtered = _filter_examples(records, query.filter_values())
        sorted_records = _sort_examples(filtered, query.sort_terms())
        total = len(sorted_records)
        return sorted_records[query.offset : query.offset + query.limit], total


def _filter_examples(
    records: list[ExampleRead],
    filters: dict[str, object],
) -> list[ExampleRead]:
    title = filters.get("title")
    keyword = filters.get("keyword")
    filtered = records
    if isinstance(title, str):
        filtered = [record for record in filtered if title.lower() in record.title.lower()]
    if isinstance(keyword, str):
        filtered = [record for record in filtered if keyword.lower() in record.title.lower()]
    return filtered


def _sort_examples(
    records: list[ExampleRead],
    sort_terms: tuple[SortTerm, ...],
) -> list[ExampleRead]:
    sorted_records = list(records)
    for term in reversed(sort_terms):
        field = term.field
        direction = term.direction
        sorted_records.sort(
            key=lambda record, field=field: getattr(record, field),
            reverse=direction == "desc",
        )
    return sorted_records
