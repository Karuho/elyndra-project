from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ElyndraPaths:
    config_dir: Path
    data_dir: Path
    state_dir: Path
    cache_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "elyndra.db"

    @property
    def language_config_file(self) -> Path:
        return self.config_dir / "language.toml"

    @property
    def persona_config_file(self) -> Path:
        return self.config_dir / "persona.toml"

    @property
    def tutors_config_file(self) -> Path:
        return self.config_dir / "tutors.toml"

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def alexandria_dir(self) -> Path:
        return self.data_dir / "alexandria"

    @property
    def language_packs_dir(self) -> Path:
        return self.alexandria_dir / "language-packs"

    @property
    def online_gateway_dir(self) -> Path:
        return self.cache_dir / "online-gateway"

    def for_account(self, public_id: str) -> ElyndraPaths:
        clean = "".join(char for char in public_id if char.isalnum() or char in {"-", "_"})
        if not clean:
            raise ValueError("Identificador de cuenta inválido.")
        return ElyndraPaths(
            config_dir=self.config_dir,
            data_dir=self.data_dir / "accounts" / clean,
            state_dir=self.state_dir / "accounts" / clean,
            cache_dir=self.cache_dir / "accounts" / clean,
        )

    @classmethod
    def from_environment(cls) -> ElyndraPaths:
        home_override = os.environ.get("ELYNDRA_HOME")
        if home_override:
            base = Path(home_override).expanduser().resolve()
            return cls(
                config_dir=base / "config",
                data_dir=base / "data",
                state_dir=base / "state",
                cache_dir=base / "cache",
            )

        home = Path.home()
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
        state_home = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
        cache_home = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))
        return cls(
            config_dir=config_home / "elyndra",
            data_dir=data_home / "elyndra",
            state_dir=state_home / "elyndra",
            cache_dir=cache_home / "elyndra",
        )

    def ensure(self) -> None:
        for directory in (
            self.config_dir,
            self.data_dir,
            self.state_dir,
            self.cache_dir,
            self.transcripts_dir,
            self.attachments_dir,
            self.alexandria_dir,
            self.language_packs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with suppress(PermissionError):
                directory.chmod(0o700)
