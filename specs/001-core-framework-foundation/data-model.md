# Data Model: Core Framework Foundation

## Tenant

Represents an isolated organization or workspace.

Fields:

- `id`
- `code`
- `name`
- `status`
- `deployment_mode`
- `created_at`
- `updated_at`

Rules:

- Status follows the tenant lifecycle state machine.
- Tenant deletion is asynchronous.
- Tenant code must be globally unique.

## User

Represents a human account known to the platform.

Fields:

- `id`
- `email`
- `display_name`
- `status`
- `auth_provider`
- `external_id`
- `token_version`
- `created_at`
- `updated_at`

Rules:

- External identity uniqueness is provider scoped.
- Disabled users cannot create new sessions.
- Disabling a user increments `token_version` and revokes active sessions.

## UserSession

Represents an authenticated session for a user and optional tenant.

Fields:

- `id`
- `user_id`
- `tenant_id`
- `auth_provider`
- `status`
- `token_version`
- `revoke_reason`
- `revoked_at`
- `created_at`

Rules:

- New sessions can only be created for active users.
- Session token_version must match the user token_version at creation time.
- Tenant suspension and deletion revoke active sessions scoped to the tenant.

## TenantMember

Represents user membership in a tenant.

Fields:

- `id`
- `tenant_id`
- `user_id`
- `status`
- `created_at`
- `updated_at`

Rules:

- A tenant must have at least one owner.
- TenantMember is the membership fact source, not the role-grant fact source.
- Member changes must be audited.

## PermissionSpec

Represents a stable permission declared by an app.

Fields:

- `resource`
- `action`
- `scope`
- `description`
- `risk_level`
- `app_label`

Rules:

- Permission specs are registered from app modules.
- Permission names must be unique by app/resource/action/scope.

## RoleTemplate

Represents a versioned role definition.

Fields:

- `id`
- `scope`
- `name`
- `version`
- `permissions`
- `created_at`
- `updated_at`

Rules:

- Platform roles and tenant roles are separate.
- Built-in roles are seeded idempotently.
- Platform administrator capability is represented by platform-scope role grants, not by a `CurrentUser.is_platform_admin` bypass flag.

## RoleGrant

Represents a role assignment fact.

Fields:

- `id`
- `tenant_id`
- `subject_type`
- `subject_id`
- `role_template_id`
- `policy_version`
- `created_at`
- `updated_at`

Rules:

- Role grants are source-of-truth facts.
- Policy projection is generated from role grants.
- Revoking a role removes the RoleGrant fact and removes projected policies for that grant through the outbox projector path.
- Tenant membership status and role grants are reconciled together when projecting policies.
- Platform-scope grants may have no tenant_id; tenant-scope grants must have tenant_id.
- Authorization decisions are made through the core authorization service, not by business apps querying projected policies directly.
- Permission denials write security audit records when an audit recorder is provided.

## OutboxEvent

Represents a reliable event pending dispatch.

Fields:

- `id`
- `tenant_id`
- `event_type`
- `event_version`
- `aggregate_type`
- `aggregate_id`
- `payload`
- `status`
- `attempt_count`
- `max_attempts`
- `next_retry_at`
- `locked_by`
- `locked_until`
- `last_error`
- `published_at`
- `dead_letter_reason`
- `created_at`

Rules:

- Written in the same transaction as business state.
- Consumers must be idempotent by `event_id`.
- Dispatcher owns conditional claim, retry, timeout recovery, dead-letter behavior, and replay.

## MigrationRecord

Represents a migration known or applied by the framework.

Fields:

- `id`
- `app_label`
- `migration_id`
- `phase`
- `migration_type`
- `status`
- `checksum`
- `lock_risk`
- `backfill_plan`
- `rollback_strategy`
- `applied_at`
- `applied_by`
- `approved_by`
- `approved_at`
- `metadata`

Rules:

- Production migrations require preflight.
- Destructive migrations require explicit classification.
- Destructive or backup-restore-required migrations require approval and backup readiness evidence.

## FileObject

Represents file metadata independent from storage provider.

Fields:

- `id`
- `tenant_id`
- `owner_type`
- `owner_id`
- `bucket`
- `object_key`
- `file_name`
- `content_type`
- `size`
- `checksum`
- `file_type`
- `status`
- `created_at`
- `deleted_at`

Rules:

- Business apps reference files by `file_id`.
- Downloads require tenant and permission checks.

## AuditLog

Represents an append-oriented audit record.

Fields:

- `id`
- `tenant_id`
- `actor_id`
- `actor_type`
- `auth_provider`
- `session_id`
- `action`
- `resource_type`
- `resource_id`
- `result`
- `reason`
- `policy_version`
- `request_id`
- `ip_address`
- `user_agent`
- `payload`
- `hash_prev`
- `hash`
- `created_at`

Rules:

- Security-critical audit cannot be best-effort.
- Audit payloads must be redacted before storage.
- Production audit should support tamper-evidence through hash chain or external WORM/SIEM integration.
- Role grants, user disabling, tenant lifecycle transitions, and authorization denials must be able to write audit records in the same transaction as the state change.
