from __future__ import annotations

import tomllib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from elyndra.languages import resolve_language, validate_language_code
from elyndra.paths import ElyndraPaths


class LanguageConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str
    threads: int
    threads_batch: int
    context_size: int
    max_tokens: int
    timeout_seconds: int
    temperature: float


PROFILES: dict[str, ModelProfile] = {
    "eco": ModelProfile(
        name="eco",
        threads=3,
        threads_batch=4,
        context_size=2048,
        max_tokens=160,
        timeout_seconds=120,
        temperature=0.2,
    ),
    "normal": ModelProfile(
        name="normal",
        threads=4,
        threads_batch=6,
        context_size=4096,
        max_tokens=256,
        timeout_seconds=180,
        temperature=0.3,
    ),
    "work": ModelProfile(
        name="work",
        threads=6,
        threads_batch=8,
        context_size=8192,
        max_tokens=512,
        timeout_seconds=300,
        temperature=0.2,
    ),
}


@dataclass(frozen=True, slots=True)
class LanguageConfig:
    enabled: bool
    backend: str
    binary: Path | None
    model: Path | None
    profile: ModelProfile
    endpoint: str | None = None
    model_name: str | None = None
    license_id: str = "unverified"
    role: str = "runtime"
    teacher_allowed: bool = False
    auditor_allowed: bool = False
    redistribution_allowed: bool = False
    connectivity: str = "local-only"
    interaction_mode: str = "auto"
    preferred_language: str = "es"

    @classmethod
    def disabled(cls) -> LanguageConfig:
        return cls(False, "none", None, None, PROFILES["eco"])

    @classmethod
    def load(cls, paths: ElyndraPaths) -> LanguageConfig:
        target = paths.language_config_file
        if not target.exists():
            return cls.disabled()

        try:
            with target.open("rb") as handle:
                raw = tomllib.load(handle)
            language = raw.get("language", {})
            provenance = raw.get("provenance", {})
            privacy = raw.get("privacy", {})
            interaction = raw.get("interaction", {})
            enabled = bool(language.get("enabled", False))
            backend = str(language.get("backend", "llama-cli")).strip().casefold()
            profile_name = str(language.get("profile", "eco")).strip().casefold()
            if profile_name not in PROFILES:
                raise ValueError(f"perfil desconocido: {profile_name}")
            binary = _optional_path(language.get("binary"))
            model = _optional_path(language.get("model"))
            endpoint = _optional_text(language.get("endpoint"))
            model_name = _optional_text(language.get("model_name"))
            license_id = str(provenance.get("license_id", "unverified")).strip()
            role = str(provenance.get("role", "runtime")).strip().casefold()
            teacher_allowed = bool(provenance.get("teacher_allowed", False))
            auditor_allowed = bool(provenance.get("auditor_allowed", False))
            redistribution_allowed = bool(
                provenance.get("redistribution_allowed", False)
            )
            connectivity = str(privacy.get("connectivity", "local-only")).strip().casefold()
            interaction_mode = str(interaction.get("mode", "auto")).strip().casefold()
            preferred_language = validate_language_code(
                str(interaction.get("preferred_language", "es"))
            )
        except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
            raise LanguageConfigError(
                f"Configuración lingüística inválida en {target}: {exc}"
            ) from exc

        config = cls(
            enabled=enabled,
            backend=backend,
            binary=binary,
            model=model,
            profile=PROFILES[profile_name],
            endpoint=endpoint,
            model_name=model_name,
            license_id=license_id or "unverified",
            role=role,
            teacher_allowed=teacher_allowed,
            auditor_allowed=auditor_allowed,
            redistribution_allowed=redistribution_allowed,
            connectivity=connectivity,
            interaction_mode=interaction_mode,
            preferred_language=preferred_language,
        )
        if enabled:
            config.validate()
        return config

    def validate(self) -> None:
        if self.interaction_mode not in {"auto", "fixed"}:
            raise LanguageConfigError(
                f"Modo de interacción no soportado: {self.interaction_mode}"
            )
        validate_language_code(self.preferred_language)
        if self.role not in {"runtime", "teacher", "auditor", "both"}:
            raise LanguageConfigError(f"Rol de modelo no soportado: {self.role}")
        if self.role in {"teacher", "both"} and not self.teacher_allowed:
            raise LanguageConfigError(
                "El rol teacher/both requiere teacher_allowed = true."
            )
        if self.role in {"auditor", "both"} and not self.auditor_allowed:
            raise LanguageConfigError(
                "El rol auditor/both requiere auditor_allowed = true."
            )
        if self.connectivity != "local-only":
            raise LanguageConfigError(
                "Elyndra 0.3.1 solo admite connectivity = 'local-only'."
            )
        if self.backend == "llama-cli":
            self._validate_llama_cli()
            return
        if self.backend == "ollama-local":
            self._validate_ollama_local()
            return
        raise LanguageConfigError(f"Backend lingüístico no soportado: {self.backend}")

    def _validate_llama_cli(self) -> None:
        if self.binary is None or not self.binary.is_file():
            raise LanguageConfigError(f"No existe el binario llama-cli: {self.binary}")
        if (self.binary.stat().st_mode & 0o111) == 0:
            raise LanguageConfigError(f"El binario no es ejecutable: {self.binary}")
        if self.model is None or not self.model.is_file():
            raise LanguageConfigError(f"No existe el modelo GGUF: {self.model}")
        if self.model.suffix.casefold() != ".gguf":
            raise LanguageConfigError(f"El modelo debe ser un archivo .gguf: {self.model}")

    def _validate_ollama_local(self) -> None:
        if not self.endpoint:
            raise LanguageConfigError("Falta endpoint para Ollama local.")
        validate_loopback_endpoint(self.endpoint)
        if not self.model_name:
            raise LanguageConfigError("Falta model_name para Ollama local.")
        if any(character.isspace() for character in self.model_name):
            raise LanguageConfigError("model_name no puede contener espacios.")


def write_language_config(
    paths: ElyndraPaths,
    *,
    binary: Path,
    model: Path,
    profile: str,
    enabled: bool = True,
    license_id: str = "unverified",
    role: str = "runtime",
    teacher_allowed: bool = False,
    auditor_allowed: bool = False,
    redistribution_allowed: bool = False,
) -> Path:
    """Write a llama-cli configuration. Kept for backward compatibility."""
    profile_name = _profile_name(profile)
    interaction_mode, preferred_language = _interaction_defaults(paths)
    binary_path = binary.expanduser().resolve()
    model_path = model.expanduser().resolve()
    config = LanguageConfig(
        enabled=enabled,
        backend="llama-cli",
        binary=binary_path,
        model=model_path,
        profile=PROFILES[profile_name],
        license_id=license_id,
        role=role,
        teacher_allowed=teacher_allowed,
        auditor_allowed=auditor_allowed,
        redistribution_allowed=redistribution_allowed,
        interaction_mode=interaction_mode,
        preferred_language=preferred_language,
    )
    if enabled:
        config.validate()
    return _write_config(paths, config)


def write_ollama_language_config(
    paths: ElyndraPaths,
    *,
    endpoint: str,
    model_name: str,
    profile: str,
    enabled: bool = True,
    license_id: str = "unverified",
    role: str = "runtime",
    teacher_allowed: bool = False,
    auditor_allowed: bool = False,
    redistribution_allowed: bool = False,
) -> Path:
    profile_name = _profile_name(profile)
    interaction_mode, preferred_language = _interaction_defaults(paths)
    normalized_endpoint = validate_loopback_endpoint(endpoint)
    config = LanguageConfig(
        enabled=enabled,
        backend="ollama-local",
        binary=None,
        model=None,
        profile=PROFILES[profile_name],
        endpoint=normalized_endpoint,
        model_name=model_name.strip(),
        license_id=license_id.strip() or "unverified",
        role=role.strip().casefold(),
        teacher_allowed=teacher_allowed,
        auditor_allowed=auditor_allowed,
        redistribution_allowed=redistribution_allowed,
        interaction_mode=interaction_mode,
        preferred_language=preferred_language,
    )
    if enabled:
        config.validate()
    return _write_config(paths, config)


def disable_language_config(paths: ElyndraPaths) -> Path:
    try:
        current = LanguageConfig.load(paths)
    except LanguageConfigError:
        current = LanguageConfig.disabled()
    disabled = LanguageConfig(
        enabled=False,
        backend=current.backend if current.backend != "none" else "ollama-local",
        binary=current.binary,
        model=current.model,
        profile=current.profile,
        endpoint=current.endpoint,
        model_name=current.model_name,
        license_id=current.license_id,
        role=current.role,
        teacher_allowed=current.teacher_allowed,
        auditor_allowed=current.auditor_allowed,
        redistribution_allowed=current.redistribution_allowed,
        connectivity="local-only",
        interaction_mode=current.interaction_mode,
        preferred_language=current.preferred_language,
    )
    return _write_config(paths, disabled)



def update_interaction_language(paths: ElyndraPaths, language: str) -> Path:
    current = LanguageConfig.load(paths)
    normalized = resolve_language(language, allow_auto=True)
    mode = "auto" if normalized == "auto" else "fixed"
    preferred = current.preferred_language if normalized == "auto" else normalized
    updated = LanguageConfig(
        enabled=current.enabled,
        backend=current.backend,
        binary=current.binary,
        model=current.model,
        profile=current.profile,
        endpoint=current.endpoint,
        model_name=current.model_name,
        license_id=current.license_id,
        role=current.role,
        teacher_allowed=current.teacher_allowed,
        auditor_allowed=current.auditor_allowed,
        redistribution_allowed=current.redistribution_allowed,
        connectivity=current.connectivity,
        interaction_mode=mode,
        preferred_language=preferred,
    )
    if updated.enabled:
        updated.validate()
    return _write_config(paths, updated)


def validate_loopback_endpoint(endpoint: str) -> str:
    value = endpoint.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "http":
        raise LanguageConfigError("El endpoint local debe usar http.")
    if parsed.username or parsed.password:
        raise LanguageConfigError("El endpoint local no admite credenciales en la URL.")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise LanguageConfigError(
            "El endpoint de Ollama debe apuntar exclusivamente a loopback."
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise LanguageConfigError("El endpoint debe contener solo esquema, host y puerto.")
    if parsed.port is None:
        raise LanguageConfigError("El endpoint local debe declarar un puerto.")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"http://{host}:{parsed.port}"


def _write_config(paths: ElyndraPaths, config: LanguageConfig) -> Path:
    paths.ensure()
    target = paths.language_config_file
    lines = [
        "[language]",
        f"enabled = {'true' if config.enabled else 'false'}",
        f'backend = "{_toml_escape(config.backend)}"',
        f'profile = "{_toml_escape(config.profile.name)}"',
    ]
    if config.backend == "llama-cli":
        lines.extend(
            [
                f'binary = "{_toml_escape(str(config.binary or ""))}"',
                f'model = "{_toml_escape(str(config.model or ""))}"',
            ]
        )
    elif config.backend == "ollama-local":
        lines.extend(
            [
                f'endpoint = "{_toml_escape(config.endpoint or "")}"',
                f'model_name = "{_toml_escape(config.model_name or "")}"',
            ]
        )
    lines.extend(
        [
            "",
            "[privacy]",
            'connectivity = "local-only"',
            "allow_remote = false",
            "",
            "[interaction]",
            f'mode = "{_toml_escape(config.interaction_mode)}"',
            f'preferred_language = "{_toml_escape(config.preferred_language)}"',
            "",
            "[provenance]",
            f'license_id = "{_toml_escape(config.license_id)}"',
            f'role = "{_toml_escape(config.role)}"',
            f"teacher_allowed = {'true' if config.teacher_allowed else 'false'}",
            f"auditor_allowed = {'true' if config.auditor_allowed else 'false'}",
            "redistribution_allowed = "
            f"{'true' if config.redistribution_allowed else 'false'}",
            "",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    with suppress(PermissionError):
        target.chmod(0o600)
    return target



def _interaction_defaults(paths: ElyndraPaths) -> tuple[str, str]:
    try:
        current = LanguageConfig.load(paths)
    except LanguageConfigError:
        return "auto", "es"
    return current.interaction_mode, current.preferred_language


def _profile_name(profile: str) -> str:
    profile_name = profile.strip().casefold()
    if profile_name not in PROFILES:
        raise LanguageConfigError(
            f"Perfil desconocido: {profile}. Disponibles: {', '.join(PROFILES)}"
        )
    return profile_name


def _optional_path(value: object) -> Path | None:
    text = _optional_text(value)
    return Path(text).expanduser().resolve() if text else None


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
