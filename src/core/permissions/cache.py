from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PermissionCache:
    version: int = 0

    def invalidate(self) -> None:
        self.version += 1
