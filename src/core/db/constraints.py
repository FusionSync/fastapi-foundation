from __future__ import annotations

from sqlalchemy import Index, UniqueConstraint

from core.base.models import Model


def check_tenant_scoped_model(model: type[Model]) -> list[str]:
    violations: list[str] = []
    table = model.__table__
    tenant_id = table.columns.get("tenant_id")
    if tenant_id is None:
        return ["missing tenant_id column"]
    if tenant_id.nullable:
        violations.append("tenant_id must be non-nullable")
    if not _has_tenant_index_or_constraint(model):
        violations.append("tenant_id must be covered by an index or constraint")
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            column_names = {column.name for column in constraint.columns}
            business_unique = column_names and column_names != {"id"}
            if business_unique and "tenant_id" not in column_names:
                violations.append(
                    f"unique constraint {constraint.name or '<unnamed>'} must include tenant_id"
                )
    return violations


def _has_tenant_index_or_constraint(model: type[Model]) -> bool:
    table = model.__table__
    for index in table.indexes:
        if isinstance(index, Index) and "tenant_id" in {column.name for column in index.columns}:
            return True
    for constraint in table.constraints:
        if "tenant_id" in {column.name for column in constraint.columns}:
            return True
    return False
