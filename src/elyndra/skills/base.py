from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from elyndra.config import AppConfig
from elyndra.knowledge import KnowledgeRepository
from elyndra.memory import MemoryRepository, ProjectRepository
from elyndra.policy import (
    AuthorizationPolicy,
    DartProjectProfileRepository,
    DotnetProjectProfileRepository,
    GoProjectProfileRepository,
    JavaProjectProfileRepository,
    KotlinProjectProfileRepository,
    NativeProjectProfileRepository,
    PhpProjectProfileRepository,
    PythonProjectProfileRepository,
    RiskLevel,
    RubyProjectProfileRepository,
    RustProjectProfileRepository,
    SqlProjectProfileRepository,
    SwiftProjectProfileRepository,
    WebProjectProfileRepository,
)
from elyndra.verification import VerificationRunRepository

if TYPE_CHECKING:
    from elyndra.alexandria.structured_packs import StructuredPackRepository


@dataclass(frozen=True, slots=True)
class SkillContext:
    config: AppConfig
    memories: MemoryRepository
    projects: ProjectRepository
    knowledge: KnowledgeRepository
    authorization: AuthorizationPolicy
    php_profiles: PhpProjectProfileRepository
    web_profiles: WebProjectProfileRepository
    python_profiles: PythonProjectProfileRepository
    java_profiles: JavaProjectProfileRepository
    kotlin_profiles: KotlinProjectProfileRepository
    dotnet_profiles: DotnetProjectProfileRepository
    dart_profiles: DartProjectProfileRepository
    native_profiles: NativeProjectProfileRepository
    ruby_profiles: RubyProjectProfileRepository
    go_profiles: GoProjectProfileRepository
    rust_profiles: RustProjectProfileRepository
    sql_profiles: SqlProjectProfileRepository
    swift_profiles: SwiftProjectProfileRepository
    verification_runs: VerificationRunRepository
    actor: str
    structured_packs: StructuredPackRepository | None = None


@dataclass(frozen=True, slots=True)
class SkillResult:
    ok: bool
    message: str
    data: dict[str, Any]


class Skill(Protocol):
    name: str
    description: str
    risk: RiskLevel

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        """Execute one explicit capability without a shell."""
