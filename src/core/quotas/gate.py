from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from core.quotas.provider import QuotaDecision, QuotaService
from core.quotas.rules import QuotaRule, QuotaSubject

T = TypeVar("T")
MutationHandler = Callable[[], Awaitable[T] | T]
TaskReservationResolver = Callable[[Any], Sequence["QuotaReservation"]]


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    rule: QuotaRule
    subject: QuotaSubject
    amount: int = 1


@dataclass(frozen=True, slots=True)
class QuotaMutationResult(Generic[T]):
    value: T
    reservations: tuple[QuotaReservation, ...]
    decisions: tuple[QuotaDecision, ...]


class TaskSubmitter(Protocol):
    async def submit(
        self,
        envelope: Any,
        *,
        tenant_status: Any = "active",
    ) -> Any: ...


class QuotaMutationGate:
    def __init__(self, quota_service: QuotaService) -> None:
        self.quota_service = quota_service

    async def run_mutation(
        self,
        reservations: Sequence[QuotaReservation],
        handler: MutationHandler[T],
    ) -> QuotaMutationResult[T]:
        return await self._reserve_and_run(
            reservations=reservations,
            handler=handler,
            release_on_error=True,
            release_on_success=False,
        )

    async def submit_task(
        self,
        reservations: Sequence[QuotaReservation],
        handler: MutationHandler[T],
    ) -> QuotaMutationResult[T]:
        return await self._reserve_and_run(
            reservations=reservations,
            handler=handler,
            release_on_error=True,
            release_on_success=False,
        )

    async def release(self, reservations: Sequence[QuotaReservation]) -> None:
        await self._release_reversed(tuple(reservations))

    async def _reserve_and_run(
        self,
        *,
        reservations: Sequence[QuotaReservation],
        handler: MutationHandler[T],
        release_on_error: bool,
        release_on_success: bool,
    ) -> QuotaMutationResult[T]:
        resolved = tuple(reservations)
        reserved: list[QuotaReservation] = []
        decisions: list[QuotaDecision] = []
        try:
            for reservation in resolved:
                decisions.append(
                    await self.quota_service.require_reserve(
                        reservation.rule,
                        reservation.subject,
                        amount=reservation.amount,
                    )
                )
                reserved.append(reservation)
        except Exception:
            await self._release_reversed(tuple(reserved))
            raise

        try:
            value = handler()
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            if release_on_error:
                await self._release_reversed(tuple(reserved))
            raise

        if release_on_success:
            await self._release_reversed(tuple(reserved))

        return QuotaMutationResult(
            value=value,
            reservations=resolved,
            decisions=tuple(decisions),
        )

    async def _release_reversed(self, reservations: tuple[QuotaReservation, ...]) -> None:
        for reservation in reversed(reservations):
            await self.quota_service.release(
                reservation.rule,
                reservation.subject,
                amount=reservation.amount,
            )


class QuotaTaskSubmitter:
    def __init__(
        self,
        task_submitter: TaskSubmitter,
        *,
        quota_gate: QuotaMutationGate,
        reservations_for_envelope: TaskReservationResolver,
    ) -> None:
        self.task_submitter = task_submitter
        self.quota_gate = quota_gate
        self.reservations_for_envelope = reservations_for_envelope

    async def submit(
        self,
        envelope: Any,
        *,
        tenant_status: Any = "active",
    ) -> Any:
        result = await self.quota_gate.submit_task(
            self.reservations_for_envelope(envelope),
            lambda: self.task_submitter.submit(
                envelope,
                tenant_status=tenant_status,
            ),
        )
        return result.value
