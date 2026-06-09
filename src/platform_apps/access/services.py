from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditRecorder
from core.events import EventPublisher
from core.exceptions import AppError
from core.permissions import (
    PLATFORM_TENANT_ID,
    AuthorizationDecision,
    PermissionRegistry,
    PolicyProjector,
    ProjectedPolicy,
    ReconciliationResult,
    RoleGrantService,
    assert_authorization_decision,
)
from core.permissions.models import RoleGrant, RoleTemplate
from core.permissions.projector import ROLE_GRANT_CHANGED_EVENT
from platform_apps.access.models import FrontendAccessMapping, FrontendAccessMappingRevision


class AccessCatalogService:
    def __init__(self, permission_registry: PermissionRegistry) -> None:
        self.permission_registry = permission_registry

    def list_permissions(self) -> list[dict[str, object]]:
        return [
            permission.to_dict()
            for permission in sorted(
                self.permission_registry.permissions,
                key=lambda item: (
                    item.spec.scope,
                    item.app_label,
                    item.spec.resource,
                    item.spec.action,
                ),
            )
        ]


class RoleTemplateService:
    def __init__(
        self,
        session: AsyncSession,
        permission_registry: PermissionRegistry,
    ) -> None:
        self.session = session
        self.permission_registry = permission_registry

    async def list_templates(self, *, scope: str | None = None) -> list[RoleTemplate]:
        statement = select(RoleTemplate)
        if scope is not None:
            statement = statement.where(RoleTemplate.scope == _normalize_scope(scope))
        result = await self.session.execute(
            statement.order_by(RoleTemplate.scope.asc(), RoleTemplate.name.asc())
        )
        return list(result.scalars().all())

    async def create_template(
        self,
        *,
        scope: str,
        name: str,
        version: int,
        permissions: list[dict[str, str]],
    ) -> RoleTemplate:
        resolved_scope = _normalize_scope(scope)
        resolved_name = _normalize_name(name)
        _validate_version(version)
        resolved_permissions = self._validate_permissions(
            scope=resolved_scope,
            permissions=permissions,
        )
        existing = await self._template_by_identity(
            scope=resolved_scope,
            name=resolved_name,
            version=version,
        )
        if existing is not None:
            raise AppError(
                "CONFLICT",
                "RoleTemplate already exists for scope, name and version",
                status_code=409,
            )
        template = RoleTemplate(
            id=str(uuid4()),
            scope=resolved_scope,
            name=resolved_name,
            version=version,
            permissions=resolved_permissions,
        )
        self.session.add(template)
        await self.session.flush()
        return template

    async def update_template(
        self,
        template_id: str,
        *,
        name: str | None = None,
        permissions: list[dict[str, str]] | None = None,
    ) -> RoleTemplate:
        template = await self.session.get(RoleTemplate, template_id)
        if template is None:
            raise AppError("NOT_FOUND", f"RoleTemplate {template_id!r} not found", status_code=404)
        if name is not None:
            template.name = _normalize_name(name)
        if permissions is not None:
            template.permissions = self._validate_permissions(
                scope=template.scope,
                permissions=permissions,
            )
            template.version += 1
        await self.session.flush()
        return template

    async def _template_by_identity(
        self,
        *,
        scope: str,
        name: str,
        version: int,
    ) -> RoleTemplate | None:
        result = await self.session.execute(
            select(RoleTemplate)
            .where(RoleTemplate.scope == scope)
            .where(RoleTemplate.name == name)
            .where(RoleTemplate.version == version)
        )
        return result.scalars().first()

    def _validate_permissions(
        self,
        *,
        scope: str,
        permissions: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if not permissions:
            raise AppError(
                "VALIDATION_ERROR",
                "RoleTemplate requires at least one permission",
                status_code=400,
            )
        resolved: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for permission in permissions:
            resource = str(permission.get("resource", "")).strip()
            action = str(permission.get("action", "")).strip()
            if not resource or not action:
                raise AppError(
                    "VALIDATION_ERROR",
                    "RoleTemplate permission requires resource and action",
                    status_code=400,
                )
            if not self.permission_registry.has_permission(
                resource=resource,
                action=action,
                scope=scope,
            ):
                raise AppError(
                    "VALIDATION_ERROR",
                    "RoleTemplate permission is not registered",
                    status_code=400,
                    details={"scope": scope, "resource": resource, "action": action},
                )
            key = (resource, action)
            if key in seen:
                continue
            seen.add(key)
            resolved.append({"resource": resource, "action": action})
        return resolved


class TenantRoleGrantService:
    def __init__(
        self,
        session: AsyncSession,
        events: EventPublisher,
        *,
        permission_registry: PermissionRegistry | None = None,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.events = events
        self.permission_registry = permission_registry
        self.audit = audit

    async def list_grants(self, *, tenant_id: str) -> list[RoleGrant]:
        result = await self.session.execute(
            select(RoleGrant)
            .where(RoleGrant.tenant_id == tenant_id)
            .order_by(RoleGrant.subject_type.asc(), RoleGrant.subject_id.asc())
        )
        return list(result.scalars().all())

    async def grant_role(
        self,
        *,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
        role_template_id: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None,
        reason: str | None,
    ) -> RoleGrant:
        resolved_reason = _require_reason(reason, operation="Role grant")
        resolved_subject_type = _normalize_subject_type(subject_type)
        resolved_subject_id = _normalize_subject_id(subject_id)
        role_template = await self.session.get(RoleTemplate, role_template_id)
        if role_template is None:
            raise AppError(
                "NOT_FOUND",
                f"RoleTemplate {role_template_id!r} not found",
                status_code=404,
            )
        if role_template.scope != "tenant":
            raise AppError(
                "VALIDATION_ERROR",
                "Tenant role grants require a tenant role template",
                status_code=400,
            )
        existing = await self._existing_grant(
            tenant_id=tenant_id,
            subject_type=resolved_subject_type,
            subject_id=resolved_subject_id,
            role_template_id=role_template.id,
        )
        if existing is not None:
            raise AppError(
                "CONFLICT",
                "RoleGrant already exists for this subject and role template",
                status_code=409,
                details={
                    "grant_id": existing.id,
                    "tenant_id": tenant_id,
                    "subject_type": resolved_subject_type,
                    "subject_id": resolved_subject_id,
                    "role_template_id": role_template.id,
                },
            )
        grant = await RoleGrantService(self.session, self.events, audit=self.audit).grant_role(
            tenant_id=tenant_id,
            subject_type=resolved_subject_type,
            subject_id=resolved_subject_id,
            role_template_id=role_template.id,
            actor_id=actor_id,
            request_id=request_id,
            authorization_decision=authorization_decision,
            reason=resolved_reason,
            policy_version=role_template.version,
        )
        await self.session.flush()
        await self._project_grant(grant, role_template)
        return grant

    async def revoke_role(
        self,
        *,
        tenant_id: str,
        grant_id: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None,
        reason: str | None,
    ) -> RoleGrant:
        resolved_reason = _require_reason(reason, operation="Role revoke")
        grant = await self.session.get(RoleGrant, grant_id)
        if grant is None or grant.tenant_id != tenant_id:
            raise AppError("NOT_FOUND", f"RoleGrant {grant_id!r} not found", status_code=404)
        return await RoleGrantService(self.session, self.events, audit=self.audit).revoke_role(
            grant_id=grant_id,
            actor_id=actor_id,
            request_id=request_id,
            authorization_decision=authorization_decision,
            reason=resolved_reason,
        )

    async def _existing_grant(
        self,
        *,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
        role_template_id: str,
    ) -> RoleGrant | None:
        result = await self.session.execute(
            select(RoleGrant)
            .where(RoleGrant.tenant_id == tenant_id)
            .where(RoleGrant.subject_type == subject_type)
            .where(RoleGrant.subject_id == subject_id)
            .where(RoleGrant.role_template_id == role_template_id)
        )
        return result.scalars().first()

    async def _project_grant(self, grant: RoleGrant, role_template: RoleTemplate) -> None:
        await PolicyProjector(
            self.session,
            permission_registry=self.permission_registry,
        ).project_grant(grant, role_template)


class EffectiveAccessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_effective_permissions(
        self,
        *,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
    ) -> list[ProjectedPolicy]:
        subject = f"{_normalize_subject_type(subject_type)}:{_normalize_subject_id(subject_id)}"
        result = await self.session.execute(
            select(ProjectedPolicy)
            .where(ProjectedPolicy.tenant_id == tenant_id)
            .where(ProjectedPolicy.subject == subject)
            .order_by(ProjectedPolicy.resource.asc(), ProjectedPolicy.action.asc())
        )
        return list(result.scalars().all())


class AccessProjectionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        permission_registry: PermissionRegistry | None = None,
    ) -> None:
        self.session = session
        self.permission_registry = permission_registry

    async def reconcile(self, *, repair: bool) -> ReconciliationResult:
        return await PolicyProjector(
            self.session,
            permission_registry=self.permission_registry,
        ).reconcile(repair=repair)


class FrontendAccessConfigService:
    def __init__(
        self,
        session: AsyncSession,
        permission_registry: PermissionRegistry,
        *,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.permission_registry = permission_registry
        self.audit = audit

    async def list_mappings(self, *, client_id: str) -> list[FrontendAccessMapping]:
        resolved_client_id = _normalize_required_text(client_id, field="client_id")
        result = await self.session.execute(
            select(FrontendAccessMapping)
            .where(FrontendAccessMapping.client_id == resolved_client_id)
            .order_by(FrontendAccessMapping.access_key.asc())
        )
        return list(result.scalars().all())

    async def get_mapping(self, *, client_id: str, access_key: str) -> FrontendAccessMapping:
        mapping = await self._mapping(client_id=client_id, access_key=access_key)
        if mapping is None:
            raise AppError(
                "NOT_FOUND",
                f"Frontend access mapping {access_key!r} not found",
                status_code=404,
            )
        return mapping

    async def create_mapping(
        self,
        *,
        client_id: str,
        access_key: str,
        owner_module: str,
        evaluation_scope: str,
        expression: dict[str, object],
        description: str | None,
        actor_id: str,
        request_id: str,
        reason: str | None,
    ) -> FrontendAccessMapping:
        resolved_reason = _require_reason(reason, operation="Frontend access mapping")
        resolved_client_id = _normalize_required_text(client_id, field="client_id")
        resolved_access_key = _normalize_required_text(access_key, field="access_key")
        if await self._mapping(client_id=resolved_client_id, access_key=resolved_access_key):
            raise AppError(
                "CONFLICT",
                "Frontend access mapping already exists",
                status_code=409,
                details={"client_id": resolved_client_id, "access_key": resolved_access_key},
            )
        resolved_scope = _normalize_evaluation_scope(evaluation_scope)
        resolved_expression = validate_frontend_access_expression(
            expression,
            permission_registry=self.permission_registry,
            evaluation_scope=resolved_scope,
        )
        mapping = FrontendAccessMapping(
            id=str(uuid4()),
            client_id=resolved_client_id,
            access_key=resolved_access_key,
            owner_module=_normalize_required_text(owner_module, field="owner_module"),
            evaluation_scope=resolved_scope,
            expression_json=resolved_expression,
            description=_normalize_optional_text(description),
            status="active",
            version=1,
            updated_by=actor_id,
            reason=resolved_reason,
        )
        self.session.add(mapping)
        await self.session.flush()
        self._add_revision(
            mapping,
            old_expression=None,
            new_expression=resolved_expression,
            old_status=None,
            new_status=mapping.status,
            actor_id=actor_id,
            reason=resolved_reason,
        )
        await self._audit(
            action="frontend_access.mapping_created",
            mapping=mapping,
            actor_id=actor_id,
            request_id=request_id,
            reason=resolved_reason,
        )
        return mapping

    async def update_mapping(
        self,
        *,
        client_id: str,
        access_key: str,
        owner_module: str | None,
        evaluation_scope: str | None,
        expression: dict[str, object] | None,
        description: str | None,
        status: str | None,
        actor_id: str,
        request_id: str,
        reason: str | None,
    ) -> FrontendAccessMapping:
        resolved_reason = _require_reason(reason, operation="Frontend access mapping")
        mapping = await self.get_mapping(client_id=client_id, access_key=access_key)
        old_expression = dict(mapping.expression_json)
        old_status = mapping.status
        resolved_scope = mapping.evaluation_scope
        if evaluation_scope is not None:
            resolved_scope = _normalize_evaluation_scope(evaluation_scope)
        resolved_expression = dict(mapping.expression_json)
        if expression is not None or evaluation_scope is not None:
            resolved_expression = validate_frontend_access_expression(
                expression if expression is not None else resolved_expression,
                permission_registry=self.permission_registry,
                evaluation_scope=resolved_scope,
            )

        if owner_module is not None:
            mapping.owner_module = _normalize_required_text(owner_module, field="owner_module")
        if evaluation_scope is not None:
            mapping.evaluation_scope = resolved_scope
        if expression is not None or evaluation_scope is not None:
            mapping.expression_json = resolved_expression
        if description is not None:
            mapping.description = _normalize_optional_text(description)
        if status is not None:
            mapping.status = _normalize_frontend_access_status(status)
        mapping.version += 1
        mapping.updated_by = actor_id
        mapping.reason = resolved_reason
        await self.session.flush()
        self._add_revision(
            mapping,
            old_expression=old_expression,
            new_expression=dict(mapping.expression_json),
            old_status=old_status,
            new_status=mapping.status,
            actor_id=actor_id,
            reason=resolved_reason,
        )
        await self._audit(
            action="frontend_access.mapping_updated",
            mapping=mapping,
            actor_id=actor_id,
            request_id=request_id,
            reason=resolved_reason,
        )
        return mapping

    async def disable_mapping(
        self,
        *,
        client_id: str,
        access_key: str,
        actor_id: str,
        request_id: str,
        reason: str | None,
    ) -> FrontendAccessMapping:
        return await self.update_mapping(
            client_id=client_id,
            access_key=access_key,
            owner_module=None,
            evaluation_scope=None,
            expression=None,
            description=None,
            status="disabled",
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
        )

    async def list_revisions(
        self,
        *,
        client_id: str,
        access_key: str,
    ) -> list[FrontendAccessMappingRevision]:
        mapping = await self.get_mapping(client_id=client_id, access_key=access_key)
        result = await self.session.execute(
            select(FrontendAccessMappingRevision)
            .where(FrontendAccessMappingRevision.mapping_id == mapping.id)
            .order_by(FrontendAccessMappingRevision.version.asc())
        )
        return list(result.scalars().all())

    async def validate(
        self,
        *,
        evaluation_scope: str,
        expression: dict[str, object],
        actor_id: str,
        request_id: str,
        reason: str | None,
    ) -> dict[str, object]:
        resolved_reason = _require_reason(reason, operation="Frontend access validation")
        resolved_scope = _normalize_evaluation_scope(evaluation_scope)
        resolved_expression = validate_frontend_access_expression(
            expression,
            permission_registry=self.permission_registry,
            evaluation_scope=resolved_scope,
        )
        await self._audit(
            action="frontend_access.mapping_validated",
            mapping=None,
            actor_id=actor_id,
            request_id=request_id,
            reason=resolved_reason,
            payload={"evaluation_scope": resolved_scope},
        )
        return {
            "ok": True,
            "permissions": [
                _format_permission(resource, action)
                for resource, action in _expression_permission_pairs(resolved_expression)
            ],
        }

    async def _mapping(
        self,
        *,
        client_id: str,
        access_key: str,
    ) -> FrontendAccessMapping | None:
        result = await self.session.execute(
            select(FrontendAccessMapping)
            .where(
                FrontendAccessMapping.client_id
                == _normalize_required_text(client_id, field="client_id")
            )
            .where(
                FrontendAccessMapping.access_key
                == _normalize_required_text(access_key, field="access_key")
            )
        )
        return result.scalars().first()

    def _add_revision(
        self,
        mapping: FrontendAccessMapping,
        *,
        old_expression: dict[str, object] | None,
        new_expression: dict[str, object] | None,
        old_status: str | None,
        new_status: str | None,
        actor_id: str,
        reason: str,
    ) -> None:
        self.session.add(
            FrontendAccessMappingRevision(
                id=str(uuid4()),
                mapping_id=mapping.id,
                client_id=mapping.client_id,
                access_key=mapping.access_key,
                old_expression_json=old_expression,
                new_expression_json=new_expression,
                old_status=old_status,
                new_status=new_status,
                version=mapping.version,
                changed_by=actor_id,
                reason=reason,
            )
        )

    async def _audit(
        self,
        *,
        action: str,
        mapping: FrontendAccessMapping | None,
        actor_id: str,
        request_id: str,
        reason: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        if self.audit is None:
            return
        await self.audit.record(
            action=action,
            resource_type="frontend_access_mapping",
            resource_id=mapping.id if mapping is not None else None,
            result="success",
            tenant_id=PLATFORM_TENANT_ID,
            actor_id=actor_id,
            reason=reason,
            policy_version=mapping.version if mapping is not None else None,
            request_id=request_id,
            payload=payload
            or (
                {
                    "client_id": mapping.client_id,
                    "access_key": mapping.access_key,
                    "evaluation_scope": mapping.evaluation_scope,
                    "status": mapping.status,
                }
                if mapping is not None
                else None
            ),
        )


class FrontendAccessEvaluationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def evaluate_all(
        self,
        *,
        client_id: str,
        tenant_id: str | None,
        user_id: str,
    ) -> dict[str, object]:
        mappings = await self._mappings(client_id=client_id)
        permission_sets, policy_version = await self._permission_sets(
            tenant_id=tenant_id,
            user_id=user_id,
        )
        access = {
            mapping.access_key: self._evaluate_mapping(
                mapping,
                permission_sets,
                tenant_id=tenant_id,
            )["allowed"]
            for mapping in mappings
        }
        return {
            "client_id": _normalize_required_text(client_id, field="client_id"),
            "tenant_id": tenant_id,
            "version": max((mapping.version for mapping in mappings), default=0),
            "policy_version": policy_version,
            "access_revision": _frontend_access_revision(
                client_id=client_id,
                mappings=mappings,
                permission_sets=permission_sets,
            ),
            "evaluated_at": datetime.now(UTC).isoformat(),
            "permissions": [
                _format_permission(resource, action)
                for resource, action in sorted(
                    permission_sets.get("tenant" if tenant_id is not None else "platform", set())
                )
            ],
            "access": access,
        }

    async def check(
        self,
        *,
        client_id: str,
        tenant_id: str | None,
        user_id: str,
        access_keys: list[str],
    ) -> dict[str, object]:
        resolved_client_id = _normalize_required_text(client_id, field="client_id")
        requested = [
            _normalize_required_text(access_key, field="access_key")
            for access_key in access_keys
        ]
        mappings = {
            mapping.access_key: mapping
            for mapping in await self._mappings(client_id=resolved_client_id)
        }
        permission_sets, policy_version = await self._permission_sets(
            tenant_id=tenant_id,
            user_id=user_id,
        )
        results: list[dict[str, object]] = []
        for access_key in requested:
            mapping = mappings.get(access_key)
            if mapping is None:
                results.append(
                    {
                        "access_key": access_key,
                        "allowed": False,
                        "reason": "unknown_access_key",
                        "version": None,
                    }
                )
                continue
            evaluated = self._evaluate_mapping(
                mapping,
                permission_sets,
                tenant_id=tenant_id,
            )
            results.append(
                {
                    "access_key": access_key,
                    "allowed": evaluated["allowed"],
                    "reason": evaluated["reason"],
                    "version": mapping.version,
                }
            )
        return {
            "client_id": resolved_client_id,
            "tenant_id": tenant_id,
            "policy_version": policy_version,
            "access_revision": _frontend_access_revision(
                client_id=resolved_client_id,
                mappings=list(mappings.values()),
                permission_sets=permission_sets,
            ),
            "evaluated_at": datetime.now(UTC).isoformat(),
            "results": results,
        }

    async def _mappings(self, *, client_id: str) -> list[FrontendAccessMapping]:
        result = await self.session.execute(
            select(FrontendAccessMapping)
            .where(
                FrontendAccessMapping.client_id
                == _normalize_required_text(client_id, field="client_id")
            )
            .order_by(FrontendAccessMapping.access_key.asc())
        )
        return list(result.scalars().all())

    async def _permission_sets(
        self,
        *,
        tenant_id: str | None,
        user_id: str,
    ) -> tuple[dict[str, set[tuple[str, str]]], int]:
        subject = f"user:{_normalize_subject_id(user_id)}"
        tenant_ids = [PLATFORM_TENANT_ID]
        if tenant_id is not None:
            tenant_ids.append(tenant_id)
        result = await self.session.execute(
            select(ProjectedPolicy)
            .where(ProjectedPolicy.tenant_id.in_(tenant_ids))
            .where(ProjectedPolicy.subject == subject)
            .where(ProjectedPolicy.effect == "allow")
        )
        sets: dict[str, set[tuple[str, str]]] = {"tenant": set(), "platform": set()}
        policy_version = 0
        for policy in result.scalars().all():
            scope = "platform" if policy.tenant_id == PLATFORM_TENANT_ID else "tenant"
            sets[scope].add((policy.resource, policy.action))
            policy_version = max(policy_version, policy.policy_version)
        return sets, policy_version

    def _evaluate_mapping(
        self,
        mapping: FrontendAccessMapping,
        permission_sets: dict[str, set[tuple[str, str]]],
        *,
        tenant_id: str | None,
    ) -> dict[str, object]:
        if mapping.status != "active":
            return {"allowed": False, "reason": "disabled_mapping"}
        if mapping.evaluation_scope == "tenant" and tenant_id is None:
            return {"allowed": False, "reason": "tenant_context_required"}
        permissions = permission_sets.get(mapping.evaluation_scope, set())
        try:
            allowed = _evaluate_expression(mapping.expression_json, permissions)
        except AppError:
            return {"allowed": False, "reason": "invalid_mapping"}
        return {
            "allowed": allowed,
            "reason": "matched_expression" if allowed else "missing_permission",
        }


class PlatformAdminService:
    def __init__(
        self,
        session: AsyncSession,
        events: EventPublisher,
        *,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.events = events
        self.audit = audit

    async def grant_platform_admin(
        self,
        *,
        user_id: str,
        role_template_id: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None = None,
        reason: str | None = None,
    ) -> RoleGrant:
        resolved_reason = _require_reason(reason, operation="Platform administrator grant")
        _assert_platform_admin_mutation_authorized(
            authorization_decision=authorization_decision,
            actor_id=actor_id,
        )
        role_template = await self.session.get(RoleTemplate, role_template_id)
        if role_template is None:
            raise AppError(
                "NOT_FOUND",
                f"RoleTemplate {role_template_id!r} not found",
                status_code=404,
            )
        if role_template.scope != "platform":
            raise AppError(
                "VALIDATION_ERROR",
                "Platform administrator grants require a platform role template",
                status_code=400,
            )

        current = await self._existing_platform_admin_grant(
            user_id=user_id,
            role_template_id=role_template_id,
        )
        if current is not None:
            return current

        grant = RoleGrant(
            id=str(uuid4()),
            tenant_id=PLATFORM_TENANT_ID,
            subject_type="user",
            subject_id=user_id,
            role_template_id=role_template_id,
            policy_version=authorization_decision.policy_version or role_template.version,
        )
        self.session.add(grant)
        await self.session.flush()
        await self.events.publish(
            event_type=ROLE_GRANT_CHANGED_EVENT,
            aggregate_type="role_grant",
            aggregate_id=grant.id,
            tenant_id=PLATFORM_TENANT_ID,
            payload={
                "tenant_id": PLATFORM_TENANT_ID,
                "actor_id": actor_id,
                "request_id": request_id,
                "grant_id": grant.id,
                "subject_type": grant.subject_type,
                "subject_id": grant.subject_id,
                "role_template_id": role_template_id,
                "reason": resolved_reason,
            },
        )
        if self.audit is not None:
            await self.audit.record(
                action="platform_admin.granted",
                resource_type="role_grant",
                resource_id=grant.id,
                result="success",
                tenant_id=PLATFORM_TENANT_ID,
                actor_id=actor_id,
                reason=resolved_reason,
                policy_version=grant.policy_version,
                request_id=request_id,
                payload={
                    "subject_type": grant.subject_type,
                    "subject_id": grant.subject_id,
                    "role_template_id": role_template_id,
                },
            )
        return grant

    async def _existing_platform_admin_grant(
        self,
        *,
        user_id: str,
        role_template_id: str,
    ) -> RoleGrant | None:
        result = await self.session.execute(
            select(RoleGrant)
            .where(RoleGrant.tenant_id == PLATFORM_TENANT_ID)
            .where(RoleGrant.subject_type == "user")
            .where(RoleGrant.subject_id == user_id)
            .where(RoleGrant.role_template_id == role_template_id)
        )
        return result.scalars().first()


def _assert_platform_admin_mutation_authorized(
    *,
    authorization_decision: AuthorizationDecision | None,
    actor_id: str,
) -> None:
    assert_authorization_decision(
        authorization_decision,
        tenant_id=PLATFORM_TENANT_ID,
        actor_id=actor_id,
        resource="access.platform_admin",
        actions={"manage"},
        operation="Platform administrator grant",
        allow_platform=True,
    )


def _normalize_scope(value: str) -> str:
    resolved = value.strip()
    if resolved not in {"platform", "tenant"}:
        raise AppError(
            "VALIDATION_ERROR",
            "RoleTemplate scope must be platform or tenant",
            status_code=400,
        )
    return resolved


def _normalize_name(value: str) -> str:
    resolved = value.strip()
    if not resolved:
        raise AppError("VALIDATION_ERROR", "RoleTemplate name is required", status_code=400)
    return resolved


def _normalize_required_text(value: str, *, field: str) -> str:
    resolved = value.strip()
    if not resolved:
        raise AppError("VALIDATION_ERROR", f"{field} is required", status_code=400)
    return resolved


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    resolved = value.strip()
    return resolved or None


def _normalize_evaluation_scope(value: str) -> str:
    resolved = value.strip()
    if resolved not in {"tenant", "platform"}:
        raise AppError(
            "VALIDATION_ERROR",
            "Frontend access evaluation_scope must be tenant or platform",
            status_code=400,
        )
    return resolved


def _normalize_frontend_access_status(value: str) -> str:
    resolved = value.strip()
    if resolved not in {"active", "disabled", "deprecated"}:
        raise AppError(
            "VALIDATION_ERROR",
            "Frontend access status must be active, disabled or deprecated",
            status_code=400,
        )
    return resolved


def validate_frontend_access_expression(
    expression: dict[str, object],
    *,
    permission_registry: PermissionRegistry,
    evaluation_scope: str,
) -> dict[str, object]:
    resolved_scope = _normalize_evaluation_scope(evaluation_scope)
    normalized = _normalize_expression(expression)
    for resource, action in _expression_permission_pairs(normalized):
        if not _registry_has_permission_for_frontend_scope(
            permission_registry,
            resource=resource,
            action=action,
            evaluation_scope=resolved_scope,
        ):
            raise AppError(
                "VALIDATION_ERROR",
                "Frontend access permission is not registered for scope",
                status_code=400,
                details={
                    "evaluation_scope": resolved_scope,
                    "resource": resource,
                    "action": action,
                },
            )
    return normalized


def _normalize_expression(expression: dict[str, object]) -> dict[str, object]:
    if not isinstance(expression, dict):
        raise AppError(
            "VALIDATION_ERROR",
            "Frontend access expression must be an object",
            status_code=400,
        )
    keys = set(expression)
    allowed_keys = {"permission", "all", "any"}
    if len(keys & allowed_keys) != 1 or keys - allowed_keys:
        raise AppError(
            "VALIDATION_ERROR",
            "Frontend access expression must contain exactly one of permission, all or any",
            status_code=400,
        )
    if "permission" in expression:
        resource, action = _parse_permission(str(expression["permission"]))
        return {"permission": _format_permission(resource, action)}

    operator = "all" if "all" in expression else "any"
    children = expression[operator]
    if not isinstance(children, list) or not children:
        raise AppError(
            "VALIDATION_ERROR",
            f"Frontend access {operator} expression requires a non-empty list",
            status_code=400,
        )
    return {operator: [_normalize_expression(child) for child in children]}


def _parse_permission(value: str) -> tuple[str, str]:
    resource, separator, action = value.rpartition(":")
    if not separator or not resource.strip() or not action.strip():
        raise AppError(
            "VALIDATION_ERROR",
            "Permission must use resource:action format",
            status_code=400,
            details={"permission": value},
        )
    return resource.strip(), action.strip()


def _format_permission(resource: str, action: str) -> str:
    return f"{resource}:{action}"


def _expression_permission_pairs(expression: dict[str, object]) -> set[tuple[str, str]]:
    if "permission" in expression:
        return {_parse_permission(str(expression["permission"]))}
    operator = "all" if "all" in expression else "any"
    pairs: set[tuple[str, str]] = set()
    for child in expression[operator]:
        pairs.update(_expression_permission_pairs(child))
    return pairs


def _evaluate_expression(
    expression: dict[str, object],
    allowed_permissions: set[tuple[str, str]],
) -> bool:
    if "permission" in expression:
        return _parse_permission(str(expression["permission"])) in allowed_permissions
    if "all" in expression:
        return all(_evaluate_expression(child, allowed_permissions) for child in expression["all"])
    if "any" in expression:
        return any(_evaluate_expression(child, allowed_permissions) for child in expression["any"])
    raise AppError("VALIDATION_ERROR", "Invalid frontend access expression", status_code=400)


def _frontend_access_revision(
    *,
    client_id: str,
    mappings: list[FrontendAccessMapping],
    permission_sets: dict[str, set[tuple[str, str]]],
) -> str:
    payload = {
        "client_id": client_id,
        "mappings": [
            {
                "access_key": mapping.access_key,
                "evaluation_scope": mapping.evaluation_scope,
                "status": mapping.status,
                "version": mapping.version,
            }
            for mapping in sorted(mappings, key=lambda item: item.access_key)
        ],
        "permissions": {
            scope: [_format_permission(resource, action) for resource, action in sorted(values)]
            for scope, values in sorted(permission_sets.items())
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]


def _registry_has_permission_for_frontend_scope(
    permission_registry: PermissionRegistry,
    *,
    resource: str,
    action: str,
    evaluation_scope: str,
) -> bool:
    if evaluation_scope == "platform":
        return permission_registry.has_permission(
            resource=resource,
            action=action,
            scope="platform",
        )
    return any(
        permission.spec.resource == resource
        and permission.spec.action == action
        and permission.spec.scope in {"tenant", "own", "resource"}
        for permission in permission_registry.permissions
    )


def _validate_version(value: int) -> None:
    if value < 1:
        raise AppError(
            "VALIDATION_ERROR",
            "RoleTemplate version must be greater than zero",
            status_code=400,
        )


def _normalize_subject_type(value: str) -> str:
    resolved = value.strip()
    if resolved not in {"user", "service_account"}:
        raise AppError(
            "VALIDATION_ERROR",
            "RoleGrant subject_type must be user or service_account",
            status_code=400,
        )
    return resolved


def _normalize_subject_id(value: str) -> str:
    resolved = value.strip()
    if not resolved:
        raise AppError("VALIDATION_ERROR", "RoleGrant subject_id is required", status_code=400)
    return resolved


def _require_reason(value: str | None, *, operation: str) -> str:
    if value is None or not value.strip():
        raise AppError(
            "VALIDATION_ERROR",
            f"{operation} reason is required",
            status_code=400,
        )
    return value.strip()


__all__ = [
    "AccessCatalogService",
    "AccessProjectionService",
    "EffectiveAccessService",
    "FrontendAccessConfigService",
    "FrontendAccessEvaluationService",
    "PlatformAdminService",
    "RoleTemplateService",
    "TenantRoleGrantService",
    "validate_frontend_access_expression",
]
