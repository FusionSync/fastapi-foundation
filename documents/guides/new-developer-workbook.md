# Developer Workbook（新手完整流程）

目标：不只是会“看模板”，而是能从 0 到 1 写一个真实可验证的业务 APP。

本练习用 `books` 作为示例域。

## 第 1 步：生成并确认脚手架

```bash
core bootstrap-app books --target-root src --package platform_apps --json
```

会生成：
- `src/platform_apps/books/models.py`
- `src/platform_apps/books/schemas.py`
- `src/platform_apps/books/services.py`
- `src/platform_apps/books/repositories.py`（你可按需创建/补齐）
- `src/platform_apps/books/router.py`
- `src/platform_apps/books/permissions.py`
- `src/platform_apps/books/module.py`

## 第 2 步：明确目标（最小可用功能）

- `GET /api/v1/books`：分页查询列表
- `POST /api/v1/books`：创建书籍（需要 tenant_write）
- `POST /api/v1/books/status`：服务健康状态（可选）

权限约定：

- `resource=book`
- `action=read`（列表）
- `action=write`（新增）

## 第 3 步：填充模型与仓储

`src/platform_apps/books/models.py`

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import IdMixin, TenantScopedModel, TimestampMixin


class BookRecord(IdMixin, TimestampMixin, TenantScopedModel):
    __tablename__ = "books"

    title: Mapped[str] = mapped_column(String(128), nullable=False)
    isbn: Mapped[str] = mapped_column(String(32), nullable=False, index=True, unique=True)
```

`src/platform_apps/books/repositories.py`

```python
from core.base import TenantScopedRepository
from core.base.schemas import ListQuerySchema

from .models import BookRecord


class BookRepository(TenantScopedRepository):
    model = BookRecord

    async def list_books(self, query: ListQuerySchema) -> list[BookRecord]:
        statement = self.apply_list_query(
            self.scoped_query().select(),
            query,
            sort_columns={"created_at": BookRecord.created_at, "title": BookRecord.title},
            filter_columns={
                "title": BookRecord.title,
                "keyword": lambda statement, value: statement.where(
                    BookRecord.title.ilike(f"%{value}%")
                    | BookRecord.isbn.ilike(f"%{value}%")
                ),
            },
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    def to_read_dict(self, record: BookRecord) -> dict[str, object]:
        return {
            "id": record.id,
            "tenant_id": record.tenant_id,
            "title": record.title,
            "isbn": record.isbn,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }
```

## 第 4 步：完善 schema 与 service

`src/platform_apps/books/schemas.py`

```python
from core.base import BaseSchema, CreateSchema, ListQuerySchema, ReadSchema


class BookCreate(CreateSchema):
    title: str
    isbn: str


class BookRead(ReadSchema):
    tenant_id: str
    title: str
    isbn: str


class BookQuery(ListQuerySchema):
    filterable_fields: frozenset[str] = frozenset({"keyword", "title", "isbn"})
    sortable_fields: frozenset[str] = frozenset({"created_at", "title", "updated_at"})
    title: str | None = None
    isbn: str | None = None
```

`src/platform_apps/books/services.py`

```python
from core.base.services import BaseService

from .repositories import BookRepository
from .schemas import BookCreate, BookQuery


class BookService(BaseService):
    async def list_books(self, repo: BookRepository, query: BookQuery):
        records = await repo.list_books(query)
        return records, len(records)

    async def create_book(self, repo: BookRepository, payload: BookCreate):
        return await repo.create(payload.model_dump())
```

## 第 5 步：权限与路由（可执行）

`src/platform_apps/books/permissions.py`

```python
from core.permissions import PermissionSpec


PERMISSIONS = [
    PermissionSpec(resource="book", action="read", scope="tenant", description="Read book records"),
    PermissionSpec(resource="book", action="write", scope="tenant", description="Write book records", risk_level="high"),
]
```

`src/platform_apps/books/router.py`

```python
from typing import Annotated

from fastapi import Depends, Request

from core.base import create_router
from core.context import get_current_context
from core.db import unit_of_work
from core.permissions import AuthorizationDecision, route_authorization_decision
from core.serialization import Envelope, ListEnvelope, ok, ok_list

from .repositories import BookRepository
from .schemas import BookCreate, BookRead, BookQuery
from .services import BookService


list_router = create_router(
    "/books",
    tags=["books"],
    permissions=["book:read"],
    permission_scope="tenant",
)

write_router = create_router(
    "/books",
    tags=["books"],
    permissions=["book:write"],
    permission_scope="tenant",
    tenant_operation="write",
)


@list_router.get("", response_model=ListEnvelope[BookRead])
async def list_books(
    request: Request,
    query: Annotated[BookQuery, Depends()],
) -> dict[str, object]:
    async with unit_of_work(request.app.state.session_factory) as uow:
        repo = BookRepository(uow.session)  # type: ignore[arg-type]
        service = BookService()
        records, total = await service.list_books(repo, query)
        return ok_list(
            [repo.to_read_dict(record) for record in records],
            query.to_pagination(total=total),
        )


@write_router.post("", response_model=Envelope[BookRead])
async def create_book(
    request: Request,
    payload: BookCreate,
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    async with unit_of_work(request.app.state.session_factory) as uow:
        repo = BookRepository(uow.session)  # type: ignore[arg-type]
        context = get_current_context()
        if context is None or context.tenant_id is None:
            raise RuntimeError("Tenant context is required")
        created = await BookService().create_book(repo, payload)
        return ok(
            repo.to_read_dict(created),
            message=f"tenant={context.tenant_id}, auth={decision.scope}/{decision.resource}:{decision.action}",
        )


@write_router.get("/status", response_model=Envelope[dict])
async def status_check() -> dict[str, object]:
    return ok({"app": "books", "status": "ready"})
```

## 第 6 步：导出 module

`src/platform_apps/books/module.py`

```python
from core.apps import AppModule, MigrationSpec

from .permissions import PERMISSIONS
from .router import list_router, write_router


module = AppModule(
    label="books",
    version="0.1.0",
    dependencies=[],
    routers=[list_router, write_router],
    models=["platform_apps.books.models"],
    migrations=MigrationSpec(path="platform_apps.books.migrations"),
    permissions=PERMISSIONS,
    public_api=["platform_apps.books.public_api"],
)
```

## 第 7 步：加入环境并验证

`INSTALLED_APPS` 中加入：

```text
INSTALLED_APPS=["platform_apps.platform_accounts","platform_apps.platform_tenants","platform_apps.books"]
```

执行：

```bash
core check-app platform_apps.books.module --json
core list-apps --json
core check-config --profile local --json
core migrate plan --json
core serve --dry-run --json
core serve --run --host 127.0.0.1 --port 8000 --json
```

## 第 8 步：最小调用清单（有可用用户 token）

```bash
TOKEN=...
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/v1/books?page=1&page_size=20&sort=-created_at"

curl -X POST http://127.0.0.1:8000/api/v1/books \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"First Book","isbn":"9780000000001"}'
```

## 第 9：给你一个上线前最小门槛清单

- `check-app` 通过
- `check-config` 通过（`ok=true`）
- 读写路由返回 `Envelope / ListEnvelope` 的标准结构
- 路由权限与 `PermissionSpec` 一一对应
- 写入接口带 `route_authorization_decision`

## 常见新手坑（避免）

- `permission_scope` 写了模板未支持值（当前只支持 `tenant/platform`）
- Service/Router 里直接拼 `select` 或 `update` 原始 SQL（应放到 repository）
- 忘记给 `read/write` 路由一致声明权限
- `permission_scope` 用了 `public=True` 的路由（非法）
- `core check-app` 报 `TENANT_ACCESS_DENIED` 后仍在查询测试 `tenant` 外数据
