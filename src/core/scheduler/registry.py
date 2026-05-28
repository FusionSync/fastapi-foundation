from __future__ import annotations

from dataclasses import dataclass

from core.apps import AppRegistry, ScheduleSpec
from core.tasks import TaskRegistry


@dataclass(frozen=True, slots=True)
class RegisteredSchedule:
    app_label: str
    spec: ScheduleSpec


class ScheduleRegistry:
    def __init__(self) -> None:
        self._schedules: dict[str, RegisteredSchedule] = {}

    @classmethod
    def from_app_registry(
        cls,
        app_registry: AppRegistry,
        *,
        task_registry: TaskRegistry,
    ) -> ScheduleRegistry:
        registry = cls()
        for app_module in app_registry.modules:
            for spec in app_module.schedules:
                registry.register(app_module.label, spec, task_registry=task_registry)
        return registry

    @property
    def schedule_ids(self) -> set[str]:
        return set(self._schedules)

    @property
    def registered_schedules(self) -> tuple[RegisteredSchedule, ...]:
        return tuple(self._schedules.values())

    def register(
        self,
        app_label: str,
        spec: ScheduleSpec,
        *,
        task_registry: TaskRegistry,
    ) -> None:
        if spec.schedule_id in self._schedules:
            raise ValueError(f"Duplicate schedule {spec.schedule_id!r}")
        if not task_registry.has_task_type(spec.task_type):
            raise ValueError(
                f"Schedule {spec.schedule_id!r} references unknown task {spec.task_type!r}"
            )
        self._schedules[spec.schedule_id] = RegisteredSchedule(
            app_label=app_label,
            spec=spec,
        )

    def get(self, schedule_id: str) -> RegisteredSchedule:
        try:
            return self._schedules[schedule_id]
        except KeyError as exc:
            raise ValueError(f"No schedule registered for {schedule_id!r}") from exc

    def to_dict(self) -> dict[str, object]:
        return {
            "schedules": [
                {
                    "app_label": registered.app_label,
                    "schedule_id": registered.spec.schedule_id,
                    "task_type": registered.spec.task_type,
                    "trigger": registered.spec.trigger,
                    "trigger_config": registered.spec.trigger_config,
                    "misfire_policy": registered.spec.misfire_policy,
                }
                for registered in self._schedules.values()
            ]
        }
