import importlib.util
from pathlib import Path

_RUN_PATH = Path(__file__).resolve().parents[2] / "run.py"
_RUN_SPEC = importlib.util.spec_from_file_location("run_script", _RUN_PATH)
assert _RUN_SPEC is not None
assert _RUN_SPEC.loader is not None
run_script = importlib.util.module_from_spec(_RUN_SPEC)
_RUN_SPEC.loader.exec_module(run_script)


def test_run_script_defaults_local_redis_when_no_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEPENDENCIES__REDIS_URL", raising=False)
    monkeypatch.setattr(run_script, "ROOT", tmp_path)

    run_script._configure_local_defaults()

    assert run_script.os.environ["DEPENDENCIES__REDIS_URL"] == "redis://127.0.0.1:6379/0"


def test_run_script_keeps_declared_redis_url_in_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEPENDENCIES__REDIS_URL", raising=False)
    monkeypatch.setattr(run_script, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "DEPENDENCIES__REDIS_URL=redis://redis.internal:6379/2\n",
        encoding="utf-8",
    )

    run_script._configure_local_defaults()

    assert "DEPENDENCIES__REDIS_URL" not in run_script.os.environ
