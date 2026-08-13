from __future__ import annotations

from elyndra.skills.base import Skill
from elyndra.skills.code_validate import CodeValidateSkill
from elyndra.skills.dart_project import (
    DartAnalyzeSkill,
    DartDescriptorValidateSkill,
    DartFormatCheckSkill,
    DartProjectInspectSkill,
    DartTestSkill,
    DartVerifyProjectSkill,
    FlutterTestSkill,
)
from elyndra.skills.dictionary_lookup import DictionaryLookupSkill
from elyndra.skills.dotnet_project import (
    DotnetBuildSkill,
    DotnetDescriptorValidateSkill,
    DotnetFormatCheckSkill,
    DotnetProjectInspectSkill,
    DotnetTestSkill,
    DotnetVerifyProjectSkill,
)
from elyndra.skills.file_read import FileReadSkill
from elyndra.skills.files_search import FilesSearchSkill
from elyndra.skills.first_aid_lookup import FirstAidLookupSkill
from elyndra.skills.frontend_quality import (
    EslintLintSkill,
    FrameworkValidateSkill,
    StylelintLintSkill,
)
from elyndra.skills.go_project import (
    GoBuildSkill,
    GofmtCheckSkill,
    GoModuleValidateSkill,
    GoProjectInspectSkill,
    GoTestSkill,
    GoVerifyProjectSkill,
    GoVetSkill,
)
from elyndra.skills.java_project import (
    JavaBuildSkill,
    JavacCompileSkill,
    JavaDescriptorValidateSkill,
    JavaProjectInspectSkill,
    JavaTestSkill,
    JavaVerifyProjectSkill,
)
from elyndra.skills.knowledge_import import KnowledgeImportSkill
from elyndra.skills.knowledge_search import KnowledgeSearchSkill
from elyndra.skills.kotlin_project import (
    KotlinBuildSkill,
    KotlincCompileSkill,
    KotlinDescriptorValidateSkill,
    KotlinProjectInspectSkill,
    KotlinTestSkill,
    KotlinVerifyProjectSkill,
)
from elyndra.skills.memory_remember import MemoryRememberSkill
from elyndra.skills.native_project import (
    CppSyntaxCheckSkill,
    CSyntaxCheckSkill,
    NativeBuildSkill,
    NativeDescriptorValidateSkill,
    NativeProjectInspectSkill,
    NativeStaticAnalyseSkill,
    NativeTestSkill,
    NativeVerifyProjectSkill,
)
from elyndra.skills.php_project import (
    PhpProjectInspectSkill,
    PhpProjectSyntaxScanSkill,
    PhpVerifyProjectSkill,
)
from elyndra.skills.php_tools import (
    ComposerValidateSkill,
    PhpStanAnalyseSkill,
    PhpSyntaxValidateSkill,
    PhpUnitRunSkill,
)
from elyndra.skills.project_inspect import ProjectInspectSkill
from elyndra.skills.project_open import ProjectOpenSkill
from elyndra.skills.project_search_text import ProjectSearchTextSkill
from elyndra.skills.python_project import (
    MypyCheckSkill,
    PyProjectValidateSkill,
    PytestRunSkill,
    PythonCompileProjectSkill,
    PythonProjectInspectSkill,
    PythonVerifyProjectSkill,
    RuffCheckSkill,
)
from elyndra.skills.ruby_project import (
    RubocopCheckSkill,
    RubyBundleCheckSkill,
    RubyDescriptorValidateSkill,
    RubyProjectInspectSkill,
    RubySyntaxCheckSkill,
    RubyTestSkill,
    RubyVerifyProjectSkill,
)
from elyndra.skills.rust_project import (
    CargoCheckSkill,
    CargoClippySkill,
    CargoTestSkill,
    RustfmtCheckSkill,
    RustManifestValidateSkill,
    RustProjectInspectSkill,
    RustVerifyProjectSkill,
)
from elyndra.skills.sql_project import (
    SqliteQueryPlanSkill,
    SqliteSchemaInspectSkill,
    SqlMigrationValidateSkill,
    SqlProjectInspectSkill,
    SqlStaticValidateSkill,
    SqlVerifyProjectSkill,
)
from elyndra.skills.swift_project import (
    SwiftBuildSkill,
    SwiftFormatCheckSkill,
    SwiftManifestValidateSkill,
    SwiftProjectInspectSkill,
    SwiftSyntaxCheckSkill,
    SwiftTestSkill,
    SwiftVerifyProjectSkill,
)
from elyndra.skills.system_status import SystemStatusSkill
from elyndra.skills.web_project import (
    CssValidateSkill,
    HtmlValidateSkill,
    JavaScriptSyntaxSkill,
    TypeScriptCheckSkill,
    WebProjectInspectSkill,
    WebVerifyProjectSkill,
)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill duplicada: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        return [self._skills[name] for name in sorted(self._skills)]


def build_default_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(SystemStatusSkill())
    registry.register(DictionaryLookupSkill())
    registry.register(FirstAidLookupSkill())
    registry.register(DartProjectInspectSkill())
    registry.register(DartDescriptorValidateSkill())
    registry.register(DartFormatCheckSkill())
    registry.register(DartAnalyzeSkill())
    registry.register(DartTestSkill())
    registry.register(FlutterTestSkill())
    registry.register(DartVerifyProjectSkill())
    registry.register(DotnetProjectInspectSkill())
    registry.register(DotnetDescriptorValidateSkill())
    registry.register(DotnetFormatCheckSkill())
    registry.register(DotnetBuildSkill())
    registry.register(DotnetTestSkill())
    registry.register(DotnetVerifyProjectSkill())
    registry.register(FilesSearchSkill())
    registry.register(FileReadSkill())
    registry.register(ProjectOpenSkill())
    registry.register(ProjectInspectSkill())
    registry.register(ProjectSearchTextSkill())
    registry.register(MemoryRememberSkill())
    registry.register(KnowledgeImportSkill())
    registry.register(KnowledgeSearchSkill())
    registry.register(CodeValidateSkill())
    registry.register(PhpSyntaxValidateSkill())
    registry.register(ComposerValidateSkill())
    registry.register(PhpStanAnalyseSkill())
    registry.register(PhpUnitRunSkill())
    registry.register(PhpProjectInspectSkill())
    registry.register(PhpProjectSyntaxScanSkill())
    registry.register(PhpVerifyProjectSkill())
    registry.register(WebProjectInspectSkill())
    registry.register(HtmlValidateSkill())
    registry.register(CssValidateSkill())
    registry.register(JavaScriptSyntaxSkill())
    registry.register(TypeScriptCheckSkill())
    registry.register(FrameworkValidateSkill())
    registry.register(EslintLintSkill())
    registry.register(StylelintLintSkill())
    registry.register(WebVerifyProjectSkill())
    registry.register(PythonProjectInspectSkill())
    registry.register(PyProjectValidateSkill())
    registry.register(PythonCompileProjectSkill())
    registry.register(RuffCheckSkill())
    registry.register(MypyCheckSkill())
    registry.register(PytestRunSkill())
    registry.register(PythonVerifyProjectSkill())
    registry.register(JavaProjectInspectSkill())
    registry.register(JavaDescriptorValidateSkill())
    registry.register(JavacCompileSkill())
    registry.register(JavaBuildSkill())
    registry.register(JavaTestSkill())
    registry.register(JavaVerifyProjectSkill())
    registry.register(KotlinProjectInspectSkill())
    registry.register(KotlinDescriptorValidateSkill())
    registry.register(KotlincCompileSkill())
    registry.register(KotlinBuildSkill())
    registry.register(KotlinTestSkill())
    registry.register(KotlinVerifyProjectSkill())
    registry.register(NativeProjectInspectSkill())
    registry.register(NativeDescriptorValidateSkill())
    registry.register(CSyntaxCheckSkill())
    registry.register(CppSyntaxCheckSkill())
    registry.register(NativeStaticAnalyseSkill())
    registry.register(NativeBuildSkill())
    registry.register(NativeTestSkill())
    registry.register(NativeVerifyProjectSkill())
    registry.register(RubyProjectInspectSkill())
    registry.register(RubyDescriptorValidateSkill())
    registry.register(RubyBundleCheckSkill())
    registry.register(RubySyntaxCheckSkill())
    registry.register(RubocopCheckSkill())
    registry.register(RubyTestSkill())
    registry.register(RubyVerifyProjectSkill())
    registry.register(GoProjectInspectSkill())
    registry.register(GoModuleValidateSkill())
    registry.register(GofmtCheckSkill())
    registry.register(GoVetSkill())
    registry.register(GoBuildSkill())
    registry.register(GoTestSkill())
    registry.register(GoVerifyProjectSkill())
    registry.register(RustProjectInspectSkill())
    registry.register(RustManifestValidateSkill())
    registry.register(RustfmtCheckSkill())
    registry.register(CargoCheckSkill())
    registry.register(CargoClippySkill())
    registry.register(CargoTestSkill())
    registry.register(RustVerifyProjectSkill())
    registry.register(SqlProjectInspectSkill())
    registry.register(SqlStaticValidateSkill())
    registry.register(SqlMigrationValidateSkill())
    registry.register(SqliteSchemaInspectSkill())
    registry.register(SqliteQueryPlanSkill())
    registry.register(SqlVerifyProjectSkill())
    registry.register(SwiftProjectInspectSkill())
    registry.register(SwiftManifestValidateSkill())
    registry.register(SwiftSyntaxCheckSkill())
    registry.register(SwiftFormatCheckSkill())
    registry.register(SwiftBuildSkill())
    registry.register(SwiftTestSkill())
    registry.register(SwiftVerifyProjectSkill())
    return registry
