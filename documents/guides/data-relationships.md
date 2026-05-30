# Data Relationships（表关系与查询边界）

目标：让新手第一次写模型时就避开“跨租户污染”“在路由直接写 SQL”的坑。

## 1）租户模型边界（先做对）

在本框架中，**业务表一般都应是租户隔离模型**（除非是平台级共用表）：

```python
class BookRecord(IdMixin, TimestampMixin, TenantScopedModel):
    __tablename__ = "books"
    title: Mapped[str]
    isbn: Mapped[str]
```

关键要求：
- `tenant_id` 由 `TenantScopedModel` 注入，不要在路由手工传递
- `update/delete/read` 统一走带租户过滤的仓储层
- 唯一约束应尽量带租户维度，避免不同租户出现同键冲突

## 2）Repository 是你的唯一入口（强推荐）

```python
from core.base import TenantScopedRepository

class BookRepository(TenantScopedRepository):
    model = Book

    def by_isbn(self, isbn: str):
        return self.scoped_query().select().where(Book.isbn == isbn)
```

规则：

- `create/update/delete/list/get` 使用 `TenantScopedRepository` 可自动注入/校验 tenant
- 直接在 `router` 里 `select/update`，会绕过统一检查，容易导致越权风险

## 3）分页与过滤参数（`ListQuerySchema`）

几乎所有列表查询建议继承 `ListQuerySchema`：

| 参数 | 必选 | 说明 | 示例 |
|---|---|---|---|
| `page` | 否 | 页码，默认 `1`，最小 `1` | `?page=2` |
| `page_size` | 否 | 每页数量，默认 `20`，最大 `200` | `?page_size=50` |
| `sort` | 否 | 排序字段，支持 `created_at` / `-created_at` / `name` | `?sort=-created_at` |
| `keyword` | 否 | 关键字过滤（默认去前后空格） | `?keyword=abc` |

在仓储层要把字段映射到列，不能乱写字段名：

```python
statement = self.apply_list_query(
    self.scoped_query().select(),
    query,
    sort_columns={"created_at": Book.created_at, "title": Book.title},
    filter_columns={
        "title": Book.title,
        "keyword": lambda st, value: st.where(
            Book.title.ilike(f"%{value}%")
            | Book.isbn.ilike(f"%{value}%")
        ),
    },
)
```

## 4）实体关系（示例）

```python
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

class Author(TenantScopedModel, IdMixin, TimestampMixin):
    __tablename__ = "authors"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    books: Mapped[list["Book"]] = relationship(back_populates="author")


class Book(TenantScopedModel, IdMixin, TimestampMixin):
    __tablename__ = "books"
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    author_id: Mapped[str] = mapped_column(String(36), ForeignKey("authors.id"), nullable=False)
    author: Mapped[Author] = relationship(back_populates="books")
```

注意：`TenantScopedRepository` 负责租户隔离；外键关系只负责业务完整性，两者都要做。

## 5）跨租户查询（CrossTenantRepository）

默认业务应该避免跨租户；只有需要管理员视图时才用 `CrossTenantRepository`，并强制传入：
- `platform_decision` 或 `platform_access`
- `reason`

否则会在构造时失败并抛出 `PERMISSION_DENIED`。

## 6）新手常见踩坑

- 把 `tenant_id` 的过滤放在 service 而不放仓储（后面容易漏）
- 唯一索引未带 `tenant_id`
- `filter_columns` 没覆盖自定义 `keyword`，导致运行时 `missing filter column`
- 查询时用裸 `select`，导致未自动注入 tenant 条件
