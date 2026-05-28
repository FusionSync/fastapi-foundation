from core.context import RequestContext, get_current_context


class BaseService:
    @property
    def current_context(self) -> RequestContext:
        context = get_current_context()
        if context is None:
            raise RuntimeError("No current request context")
        return context

    @property
    def current_user_id(self) -> str | None:
        return self.current_context.user_id

    @property
    def current_tenant_id(self) -> str | None:
        return self.current_context.tenant_id
