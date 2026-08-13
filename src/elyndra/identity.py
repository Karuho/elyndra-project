from __future__ import annotations

import getpass
from dataclasses import dataclass

from elyndra.config import AppConfig


class IdentityError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class OwnerIdentity:
    display_name: str
    system_user: str


class IdentityGuard:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def verify(self) -> OwnerIdentity:
        current_user = getpass.getuser()
        if current_user != self.config.system_user:
            raise IdentityError(
                "Usuario local no autorizado. "
                f"Esperado={self.config.system_user!r}, actual={current_user!r}."
            )
        return OwnerIdentity(self.config.owner_name, current_user)
