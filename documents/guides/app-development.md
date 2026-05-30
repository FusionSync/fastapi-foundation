# APP Development（开发新模块）

本页给出“能快速上线一个业务模块”的最小标准。  
你只要按顺序填这 6 个文件并过 `check-app`，就能和其他模块一致。

## 1）路由规则要先写清（最容易让 check-app 过不了）

`create_router` 常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `prefix` | 无 | 路由前缀，如 `/books` |
| `tags` | `[]` | OpenAPI 分组 |
| `public` | `False` | true 时表示公开路由，不能有权限参数 |
| `auth_required` | `True` | 通常不需要显式设置 |
| `tenant_required` | `True` | 平台路由可设 `False` |
| `permissions` | `()` | 例：`["book:read"]` |
| `permission_scope` | `tenant/platform` | 仅在写 `permissions` 时生效 |
| `tenant_operation` | `read` | 写操作建议显式写 `write` |

常见错误：
- `public=True` 但写了 `permissions`
- `permission_scope` 设了非法值
- 路由声明了 `book:write`，但 `module.Permissions` 写了别名

## 2）最小文件清单（Bootstrap 后）

```
src/platform_apps/<your_app>/
  models.py
  schemas.py
  repositories.py
  services.py
  permissions.py
  router.py
  module.py
```

## 3）模型（`models.py`）

```python
from datetime import UTC, datetime
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import IdMixin, TenantScopedModel, TimestampMixin


class NoteRecord(IdMixin, TimestampMixin, TenantScopedModel):
    __tablename__ = "notes"

    title: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(String(2048), nullable=False)
    published_at: Mapped[datetime] = mapped_column(default_factory=lambda: datetime.now(UTC))
```

## 4）仓储（`repositories.py`）

```python
from core.base import TenantScopedRepository
from core.base.schemas import ListQuerySchema

from .models import NoteRecord


class NoteRepository(TenantScopedRepository):
    model = NoteRecord

    async def list_notes(self, query: ListQuerySchema) -> tuple[list[NoteRecord], int]:
        statement = self.apply_list_query(
            self.scoped_query().select(),
            query,
            sort_columns={"created_at": NoteRecord.created_at, "title": NoteRecord.title},
            filter_columns={
                "title": NoteRecord.title,
                "keyword": lambda st, value: st.where(
                    NoteRecord.title.ilike(f"%{value}%")
                    | NoteRecord.body.ilike(f"%{value}%")
                ),
            },
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all()), 0
```

- `TenantScopedRepository.scoped_query()` 自动加 `tenant_id` 过滤
- 若需要总数，在 SQL 中可再执行一次 `select(func.count())`

## 5）Schema（`schemas.py`）

```python
from core.base import BaseSchema, CreateSchema, ListQuerySchema, ReadSchema, UpdateSchema

class NoteCreate(CreateSchema):
    title: str
    body: str

class NoteUpdate(UpdateSchema):
    title: str | None = None
    body: str | None = None

class NoteRead(ReadSchema):
    tenant_id: str
    title: str
    body: str

class NoteQuery(ListQuerySchema):
    filterable_fields: frozenset[str] = frozenset({"keyword", "title"})
    sortable_fields: frozenset[str] = frozenset({"created_at", "title"})
    title: str | None = None
```

## 6）Service（`services.py`）

```python
from core.base.services import BaseService
from .repositories import NoteRepository
from .schemas import NoteCreate, NoteQuery

class NoteService(BaseService):
    async def list_notes(self, repo: NoteRepository, query: NoteQuery):
        records = await repo.list_notes(query)
        return records

    async def create_note(self, repo: NoteRepository, payload: NoteCreate):
        return await repo.create(payload.model_dump())
```

## 7）权限声明（`permissions.py`）

```python
from core.permissions import PermissionSpec

PERMISSIONS = [
    PermissionSpec(resource="note", action="read", scope="tenant"),
    PermissionSpec(resource="note", action="write", scope="tenant", risk_level="high"),
]
```

## 8）Router（`router.py`）

```python
from typing import Annotated
from fastapi import Depends, Request

from core.base import create_router
from core.context import get_current_context
from core.db import unit_of_work
from core.permissions import AuthorizationDecision, route_authorization_decision
from core.serialization import Envelope, ListEnvelope, ok, ok_list

from .repositories import NoteRepository
from .schemas import NoteCreate, NoteQuery, NoteRead

list_router = create_router("/notes", tags=["notes"], permissions=["note:read"])
write_router = create_router(
    "/notes",
    tags=["notes"],
    permissions=["note:write"],
    tenant_operation="write",
)

@list_router.get("", response_model=ListEnvelope[NoteRead])
async def list_notes(request: Request, query: Annotated[NoteQuery, Depends()]):
    async with unit_of_work(request.app.state.session_factory) as uow:
        repo = NoteRepository(uow.session)
        records, total = await repo.list_notes(query)
        return ok_list(records, query.to_pagination(total=total))

@write_router.post("", response_model=Envelope[NoteRead])
async def create_note(
    request: Request,
    payload: NoteCreate,
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
):
    async with unit_of_work(request.app.state.session_factory) as uow:
        repo = NoteRepository(uow.session)
        record = await NoteService().create_note(repo, payload)
        ctx = get_current_context()
        return ok(
            {"tenant_id": ctx.tenant_id, "note": record},
            message=f"policy={decision.policy_version}",
        )
```

> 上线前可加 `ctx` 打点，便于定位“为什么本次请求允许/拒绝”。

## 9）模块入口（`module.py`）

```python
from core.apps import AppModule, MigrationSpec
from .permissions import PERMISSIONS
from .router import list_router, write_router

module = AppModule(
    label="notes",
    version="0.1.0",
    routers=[list_router, write_router],
    models=["platform_apps.notes.models"],
    migrations=MigrationSpec(path="platform_apps.notes.migrations"),
    permissions=PERMISSIONS,
    public_api=["platform_apps.notes.public_api"],
)
```

## 10）验收清单（每改一次都跑）

```bash
core check-app platform_apps.notes.module --json
core list-apps --json
core serve --dry-run --host 127.0.0.1 --port 8000 --json
```

### 校验点

- `core check-app` 输出 `ok: true`
- 路由响应统一为 `Envelope` / `ListEnvelope`
- `PermissionSpec` 与 `router permissions` 字符串一一对应
- 仓储里存在 `TenantScopedRepository` 与 `apply_list_query` 处理列表逻辑
