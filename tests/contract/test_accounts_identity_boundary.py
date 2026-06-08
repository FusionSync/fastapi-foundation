from core.auth import CurrentUser, SessionPrincipal
from platform_apps.accounts import ExternalIdentity, User


def test_user_model_keeps_identity_provider_fields_out_of_global_user_table() -> None:
    user_columns = set(User.__table__.columns.keys())

    assert "auth_provider" not in user_columns
    assert "external_id" not in user_columns
    assert {"id", "email", "display_name", "status", "token_version"} <= user_columns


def test_external_identity_model_is_the_only_provider_subject_mapping() -> None:
    identity_columns = set(ExternalIdentity.__table__.columns.keys())

    assert {"user_id", "provider", "subject"} <= identity_columns
    assert "external_id" not in SessionPrincipal.__dataclass_fields__
    assert "external_id" not in CurrentUser.__dataclass_fields__
