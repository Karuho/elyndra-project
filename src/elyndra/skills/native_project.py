from __future__ import annotations

import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.process import ProcessResult, run_controlled_process
from elyndra.skills.tool_resolution import ToolResolution, resolve_project_tool

_C_EXTENSIONS = {".c"}
_CPP_EXTENSIONS = {".cc", ".cpp", ".cxx"}
_HEADER_EXTENSIONS = {".h", ".hh", ".hpp", ".hxx"}
_ALL_EXTENSIONS = _C_EXTENSIONS | _CPP_EXTENSIONS | _HEADER_EXTENSIONS
_PROJECT_MARKERS = (
    "CMakeLists.txt",
    "Makefile",
    "makefile",
    "meson.build",
    "compile_commands.json",
)
_DEFAULT_EXCLUDES = {
    ".git",
    ".idea",
    ".vscode",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "dist",
    "out",
    "vendor",
}
_DANGEROUS_CMAKE_TOKENS = {
    "execute_process(": "execute_process",
    "file(download": "file(DOWNLOAD)",
    "file(upload": "file(UPLOAD)",
    "externalproject_add(": "ExternalProject_Add",
    "fetchcontent_makeavailable(": "FetchContent_MakeAvailable",
}


class NativeProjectInspectSkill:
    name = "native.project_inspect"
    description = "Inspecciona un proyecto C/C++ sin ejecutar compiladores ni CMake."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            context.config.native_tool_timeout_seconds,
            ["inspect-native-project", str(root)],
            "Solo se leen nombres, manifiestos y metadatos acotados.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        inventory = _inspect_project(root, settings)
        return SkillResult(
            True,
            _format_inventory(inventory, authorization),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "inventory": inventory,
                **authorization,
            },
        )


class NativeDescriptorValidateSkill:
    name = "native.descriptor_validate"
    description = "Valida CMakeLists.txt y manifiestos C/C++ sin ejecutarlos."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            context.config.native_tool_timeout_seconds,
            ["validate-native-descriptors", str(root)],
            "No se evalúa CMake, Make, Meson ni código del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        report = _validate_descriptors(root)
        ok = not report["errors"]
        lines = [
            "Descriptores C/C++ válidos." if ok else "Descriptores C/C++ con errores.",
            "",
            f"- Proyecto: `{root}`",
            f"- Build principal: `{report['build_tool']}`",
            f"- Errores: `{len(report['errors'])}`",
            f"- Advertencias: `{len(report['warnings'])}`",
        ]
        for label, items in (("Errores", report["errors"]), ("Advertencias", report["warnings"])):
            if items:
                lines.extend(("", f"{label}:"))
                lines.extend(f"- {item}" for item in items)
        return SkillResult(
            ok,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "report": report,
                "stage_status": "passed" if ok else "failed",
                **authorization,
            },
        )


class CSyntaxCheckSkill:
    name = "native.c_syntax_check"
    description = "Comprueba sintaxis C con GCC o Clang sin enlazar ni ejecutar."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["cc", "-fsyntax-only", f"-std={settings['c_standard']}", "@<archivos>"],
            "No se enlaza ni ejecuta el programa.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_syntax(context, params, language="c", skill_name=self.name)


class CppSyntaxCheckSkill:
    name = "native.cpp_syntax_check"
    description = "Comprueba sintaxis C++ con G++ o Clang++ sin enlazar ni ejecutar."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            [
                "c++",
                "-fsyntax-only",
                f"-std={settings['cpp_standard']}",
                "@<archivos>",
            ],
            "No se enlaza ni ejecuta el programa.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_syntax(context, params, language="cpp", skill_name=self.name)


class NativeStaticAnalyseSkill:
    name = "native.static_analyse"
    description = "Ejecuta cppcheck local o global con argumentos fijos."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["cppcheck", "--enable=warning,style,performance,portability", str(root)],
            "cppcheck solo analiza archivos; no compila ni ejecuta el proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        tool = resolve_project_tool(root, "cppcheck")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "cppcheck", authorization)
        argv = [
            str(tool.path),
            "--enable=warning,style,performance,portability",
            "--error-exitcode=1",
            "--inline-suppr",
            "--quiet",
            str(root),
        ]
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
        )
        return _process_result(
            self.name,
            root,
            tool,
            argv,
            result,
            authorization,
            success="Análisis estático C/C++ correcto.",
            failure="cppcheck encontró problemas.",
        )


class NativeBuildSkill:
    name = "native.build_project"
    description = "Configura y compila CMake en una carpeta temporal y sin red intencional."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["cmake", "-S", str(root), "-B", "<temporal>", "&&", "cmake", "--build"],
            "CMake puede evaluar lógica del proyecto; se usa un build temporal.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_cmake(context, params, skill_name=self.name, tests=False)


class NativeTestSkill:
    name = "native.test_project"
    description = "Configura, compila y ejecuta CTest en una carpeta temporal."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["ctest", "--test-dir", "<temporal>", "--output-on-failure"],
            "Los tests ejecutan binarios del proyecto dentro del entorno local.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_cmake(context, params, skill_name=self.name, tests=True)


class NativeVerifyProjectSkill:
    name = "native.verify_project"
    description = "Ejecuta la verificación C/C++ completa y guarda historial comparable."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["native-verify", *_enabled_stage_names(settings), str(root)],
            "La compilación y los tests requieren aprobación explícita.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        started = time.perf_counter()
        plan = {
            "stages": _enabled_stage_names(settings),
            "compiler": settings["compiler"],
            "require_tools": settings["require_tools"],
            "fail_fast": settings["fail_fast"],
        }
        profile = settings.get("profile") or {}
        run_id = context.verification_runs.start(
            toolchain="native",
            project_root=root,
            actor=context.actor,
            profile_id=profile.get("id"),
            plan=plan,
        )
        stages: list[dict[str, Any]] = []
        stage_specs = (
            ("inspect", True, NativeProjectInspectSkill()),
            ("descriptor", settings["descriptor_enabled"], NativeDescriptorValidateSkill()),
            ("c_syntax", settings["c_syntax_enabled"], CSyntaxCheckSkill()),
            ("cpp_syntax", settings["cpp_syntax_enabled"], CppSyntaxCheckSkill()),
            ("static", settings["static_enabled"], NativeStaticAnalyseSkill()),
            ("build", settings["build_enabled"], NativeBuildSkill()),
            ("tests", settings["tests_enabled"], NativeTestSkill()),
        )
        for stage_name, enabled, skill in stage_specs:
            if not enabled:
                message = _disabled_stage_message(stage_name, settings)
                stages.append({"name": stage_name, "status": "skipped", "message": message})
                continue
            result = skill.execute(context, dict(params))
            status = _stage_status(result)
            if status == "unavailable" and settings["require_tools"]:
                status = "failed"
            stages.append(
                {
                    "name": stage_name,
                    "status": status,
                    "message": _bounded_text(result.message.splitlines()[0], 240),
                    "duration_ms": result.data.get("duration_ms"),
                    "returncode": result.data.get("returncode"),
                }
            )
            if settings["fail_fast"] and status == "failed":
                break
        status = _overall_status(stages)
        duration_ms = round((time.perf_counter() - started) * 1000)
        summary = {"stages": stages, "authorization": authorization}
        run = context.verification_runs.finish(
            run_id,
            status=status,
            duration_ms=duration_ms,
            summary=summary,
        )
        heading = {
            "passed": "Verificación C/C++ correcta.",
            "partial": "Verificación C/C++ parcial.",
            "failed": "Verificación C/C++ fallida.",
        }[status]
        lines = [
            heading,
            "",
            f"- Proyecto: `{root}`",
            f"- Ejecución: `{run_id}`",
            f"- Estado: `{status}`",
            f"- Duración: `{duration_ms} ms`",
            "",
            "Etapas:",
        ]
        for stage in stages:
            suffix = f" — {stage['message']}" if stage.get("message") else ""
            lines.append(f"- {stage['name']}: `{stage['status']}`{suffix}")
        return SkillResult(
            status != "failed",
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "verification_run": run,
                "stages": stages,
                "duration_ms": duration_ms,
                **authorization,
            },
        )


def _execute_syntax(
    context: SkillContext,
    params: dict[str, Any],
    *,
    language: str,
    skill_name: str,
) -> SkillResult:
    target = _resolve_existing_path(params)
    root = _discover_project_root(target)
    settings = _pipeline_settings(context, root, params)
    authorization = _authorize_project(context, root, params, settings=settings)
    extensions = _C_EXTENSIONS if language == "c" else _CPP_EXTENSIONS
    files, truncated = _collect_native_files(
        target,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_native_files"],
        extensions=extensions,
    )
    if truncated:
        return _file_limit_result(skill_name, root, settings, authorization)
    if not files:
        label = "C" if language == "c" else "C++"
        return _skipped_result(
            skill_name,
            root,
            f"No se encontraron fuentes {label} para comprobar.",
            authorization,
        )
    tool_name = _compiler_name(root, settings, language)
    tool = resolve_project_tool(root, tool_name)
    if tool.path is None:
        return _tool_unavailable(skill_name, root, tool_name, authorization)
    standard = settings["c_standard"] if language == "c" else settings["cpp_standard"]
    include_args = _include_arguments(root)
    with tempfile.TemporaryDirectory(prefix="elyndra-native-syntax-") as temp_dir:
        response = Path(temp_dir) / "sources.rsp"
        response.write_text(
            "\n".join(_response_file_value(path) for path in files),
            encoding="utf-8",
        )
        argv = [
            str(tool.path),
            "-fsyntax-only",
            f"-std={standard}",
            *include_args,
            f"@{response}",
        ]
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
        )
    label = "C" if language == "c" else "C++"
    return _process_result(
        skill_name,
        root,
        tool,
        argv,
        result,
        authorization,
        success=f"Sintaxis {label} correcta.",
        failure=f"El compilador encontró problemas de sintaxis {label}.",
        extra={"files_examined": len(files), "standard": standard},
    )


def _execute_cmake(
    context: SkillContext,
    params: dict[str, Any],
    *,
    skill_name: str,
    tests: bool,
) -> SkillResult:
    root = _discover_project_root(_resolve_existing_path(params))
    settings = _pipeline_settings(context, root, params)
    authorization = _authorize_project(context, root, params, settings=settings)
    if not (root / "CMakeLists.txt").is_file():
        return _skipped_result(
            skill_name,
            root,
            "No existe CMakeLists.txt; Make y Meson no se ejecutan automáticamente.",
            authorization,
        )
    report = _validate_descriptors(root)
    if report["errors"]:
        return SkillResult(
            False,
            "CMakeLists.txt no supera la validación previa.",
            {
                "engine": "local-skill",
                "generated": False,
                "skill": skill_name,
                "project_root": str(root),
                "stage_status": "failed",
                "report": report,
                **authorization,
            },
        )
    cmake = resolve_project_tool(root, "cmake")
    if cmake.path is None:
        return _tool_unavailable(skill_name, root, "cmake", authorization)
    ctest = resolve_project_tool(root, "ctest") if tests else None
    if tests and ctest is not None and ctest.path is None:
        return _tool_unavailable(skill_name, root, "ctest", authorization)
    environment = {
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    with tempfile.TemporaryDirectory(prefix="elyndra-cmake-") as temp_dir:
        build_dir = Path(temp_dir) / "build"
        configure_argv = [
            str(cmake.path),
            "-S",
            str(root),
            "-B",
            str(build_dir),
            "-DCMAKE_BUILD_TYPE=Debug",
            "-DFETCHCONTENT_FULLY_DISCONNECTED=ON",
        ]
        configured = run_controlled_process(
            configure_argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
            environment=environment,
        )
        if configured.returncode != 0 or configured.timed_out:
            return _multi_process_result(
                skill_name,
                root,
                cmake,
                [configure_argv],
                [configured],
                authorization,
                success="Configuración CMake correcta.",
                failure="La configuración CMake encontró problemas.",
            )
        build_argv = [str(cmake.path), "--build", str(build_dir), "--parallel", "1"]
        built = run_controlled_process(
            build_argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
            environment=environment,
        )
        if not tests or built.returncode != 0 or built.timed_out:
            return _multi_process_result(
                skill_name,
                root,
                cmake,
                [configure_argv, build_argv],
                [configured, built],
                authorization,
                success="Build CMake finalizó correctamente.",
                failure="Build CMake encontró problemas.",
            )
        assert ctest is not None and ctest.path is not None
        test_argv = [
            str(ctest.path),
            "--test-dir",
            str(build_dir),
            "--output-on-failure",
            "--no-tests=ignore",
        ]
        tested = run_controlled_process(
            test_argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
            environment=environment,
        )
        return _multi_process_result(
            skill_name,
            root,
            ctest,
            [configure_argv, build_argv, test_argv],
            [configured, built, tested],
            authorization,
            success="Tests C/C++ finalizaron correctamente.",
            failure="CTest encontró problemas.",
        )


def _pipeline_settings(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    effective = context.native_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.native_tool_timeout_seconds,
        default_max_output_chars=context.config.native_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    has_cmake = (root / "CMakeLists.txt").is_file()
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_native_files": _bounded_files(
            params.get("max_files"),
            int(effective["max_native_files"]),
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "descriptor_enabled": _setting(params, profile, "descriptor_enabled", True),
        "c_syntax_enabled": _setting(
            params,
            profile,
            "c_syntax_enabled",
            not has_cmake,
        ),
        "cpp_syntax_enabled": _setting(
            params,
            profile,
            "cpp_syntax_enabled",
            not has_cmake,
        ),
        "static_enabled": _setting(params, profile, "static_enabled", True),
        "build_enabled": _setting(params, profile, "build_enabled", has_cmake),
        "tests_enabled": _setting(params, profile, "tests_enabled", has_cmake),
        "compiler": _choice_setting(
            params.get("compiler"),
            profile.get("compiler", "auto"),
            {"auto", "gcc", "clang"},
            "compiler",
        ),
        "c_standard": _choice_setting(
            params.get("c_standard"),
            profile.get("c_standard", "c17"),
            {"c11", "c17", "c23"},
            "c_standard",
        ),
        "cpp_standard": _choice_setting(
            params.get("cpp_standard"),
            profile.get("cpp_standard", "c++20"),
            {"c++17", "c++20", "c++23"},
            "cpp_standard",
        ),
        "fail_fast": _setting(params, profile, "fail_fast", False),
        "require_tools": _setting(params, profile, "require_tools", False),
        "managed_build": has_cmake,
    }


def _disabled_stage_message(stage_name: str, settings: dict[str, Any]) -> str:
    if stage_name in {"c_syntax", "cpp_syntax"} and settings["managed_build"]:
        return "Omitido por defecto: CMake administra includes y dependencias del proyecto."
    return "Etapa desactivada por configuración."


def _compiler_name(root: Path, settings: dict[str, Any], language: str) -> str:
    configured = settings["compiler"]
    if configured == "gcc":
        return "gcc" if language == "c" else "g++"
    if configured == "clang":
        return "clang" if language == "c" else "clang++"
    candidates = ("gcc", "clang") if language == "c" else ("g++", "clang++")
    for name in candidates:
        if resolve_project_tool(root, name).path is not None:
            return name
    return candidates[0]


def _include_arguments(root: Path) -> list[str]:
    result: list[str] = []
    for name in ("include", "src"):
        candidate = root / name
        if candidate.is_dir():
            result.extend(("-I", str(candidate)))
    return result


def _resolve_path(params: dict[str, Any]) -> Path:
    raw = str(params.get("path", "")).strip()
    if not raw:
        raise ValueError("Falta el parámetro path.")
    return Path(raw).expanduser().resolve(strict=False)


def _resolve_existing_path(params: dict[str, Any]) -> Path:
    path = _resolve_path(params)
    if not path.exists():
        raise ValueError(f"La ruta no existe: {path}")
    mode = path.stat().st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise ValueError(f"La ruta no es un archivo o directorio regular: {path}")
    return path


def _discover_project_root(path: Path) -> Path:
    start = path if path.is_dir() else path.parent
    current = start.resolve(strict=False)
    while True:
        if any((current / marker).exists() for marker in _PROJECT_MARKERS):
            return current
        if current.parent == current:
            return start.resolve(strict=False)
        current = current.parent


def _authorize_project(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
    *,
    settings: dict[str, Any],
) -> dict[str, Any]:
    decision = context.authorization.project(
        root,
        allow_once=params.get("allow_root_once") is True,
        source=str(params.get("authorization_source") or "explicit_approval"),
    )
    if not decision.allowed:
        raise PermissionError(
            f"{decision.reason} Autorízalo solo para esta ejecución con --allow-root-once."
        )
    profile = settings.get("profile") or {}
    return {
        **decision.as_data(),
        "timeout_seconds": settings["timeout_seconds"],
        "project_profile_id": profile.get("id"),
        "project_profile_applied": bool(profile),
    }


def _collect_native_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
    extensions: set[str] | None = None,
) -> tuple[list[Path], bool]:
    allowed = extensions or _ALL_EXTENSIONS
    if target.is_file():
        return ([target] if target.suffix.casefold() in allowed else []), False
    excluded = {
        (root / relative).resolve(strict=False)
        for relative in (*_DEFAULT_EXCLUDES, *exclude_paths)
    }
    files: list[Path] = []
    for current, directories, filenames in os.walk(target, followlinks=False):
        current_path = Path(current).resolve(strict=False)
        directories[:] = [
            name
            for name in directories
            if not _is_excluded((current_path / name).resolve(strict=False), excluded)
        ]
        for filename in sorted(filenames):
            candidate = (current_path / filename).resolve(strict=False)
            if candidate != root and root not in candidate.parents:
                continue
            if candidate.suffix.casefold() not in allowed:
                continue
            if _is_excluded(candidate, excluded):
                continue
            try:
                mode = candidate.stat().st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):
                continue
            files.append(candidate)
            if len(files) > max_files:
                return files, True
    files.sort(key=lambda item: item.relative_to(root).as_posix().casefold())
    return files, False


def _is_excluded(path: Path, excluded: set[Path]) -> bool:
    return any(path == item or item in path.parents for item in excluded)


def _inspect_project(root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    files, truncated = _collect_native_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_native_files"],
    )
    c_count = sum(path.suffix.casefold() in _C_EXTENSIONS for path in files)
    cpp_count = sum(path.suffix.casefold() in _CPP_EXTENSIONS for path in files)
    header_count = sum(path.suffix.casefold() in _HEADER_EXTENSIONS for path in files)
    cmake_text = _read_text(root / "CMakeLists.txt", limit=300_000)
    languages: list[str] = []
    if c_count:
        languages.append("C")
    if cpp_count:
        languages.append("C++")
    return {
        "project_root": str(root),
        "c_files": c_count,
        "cpp_files": cpp_count,
        "headers": header_count,
        "languages": languages,
        "truncated": truncated,
        "build_tool": "cmake" if cmake_text else "direct",
        "cmake": bool(cmake_text),
        "makefile": any((root / name).is_file() for name in ("Makefile", "makefile")),
        "meson": (root / "meson.build").is_file(),
        "dangerous_cmake_features": _dangerous_cmake_features(cmake_text),
        "tools": {
            name: resolve_project_tool(root, name).path is not None
            for name in ("gcc", "g++", "clang", "clang++", "cmake", "ctest", "cppcheck")
        },
    }


def _validate_descriptors(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    cmake_path = root / "CMakeLists.txt"
    cmake_text = _read_text(cmake_path, limit=300_000)
    if cmake_path.is_file() and not cmake_text:
        errors.append("CMakeLists.txt está vacío o no puede leerse como UTF-8.")
    elif cmake_path.is_file():
        errors.extend(_balanced_cmake(cmake_text))
        dangerous = _dangerous_cmake_features(cmake_text)
        if dangerous:
            warnings.append(
                "CMake contiene funciones con efectos externos: "
                + ", ".join(dangerous)
                + "."
            )
    if (root / "meson.build").is_file():
        warnings.append("Meson fue detectado, pero Elyndra no lo ejecuta en 0.7.8.")
    if any((root / name).is_file() for name in ("Makefile", "makefile")):
        warnings.append("Makefile fue detectado, pero Elyndra no ejecuta make automáticamente.")
    return {
        "build_tool": "cmake" if cmake_path.is_file() else "direct",
        "errors": errors,
        "warnings": warnings,
        "dangerous_cmake_features": _dangerous_cmake_features(cmake_text),
    }


def _read_text(path: Path, *, limit: int) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="strict")[:limit]
    except (OSError, UnicodeError):
        return ""


def _balanced_cmake(text: str) -> list[str]:
    balance = 0
    quoted = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if char == "(":
            balance += 1
        elif char == ")":
            balance -= 1
            if balance < 0:
                return ["CMakeLists.txt contiene un paréntesis de cierre inesperado."]
    errors: list[str] = []
    if quoted:
        errors.append("CMakeLists.txt contiene una cadena sin cerrar.")
    if balance:
        errors.append("CMakeLists.txt contiene paréntesis sin cerrar.")
    return errors


def _dangerous_cmake_features(text: str) -> list[str]:
    lowered = text.casefold().replace(" ", "")
    return sorted(
        label
        for token, label in _DANGEROUS_CMAKE_TOKENS.items()
        if token in lowered
    )


def _format_inventory(inventory: dict[str, Any], authorization: dict[str, Any]) -> str:
    languages = ", ".join(inventory["languages"]) or "no detectado"
    dangerous = ", ".join(inventory["dangerous_cmake_features"]) or "ninguna"
    return "\n".join(
        (
            "Inspección C/C++ completada sin ejecutar código.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- Archivos C: `{inventory['c_files']}`",
            f"- Archivos C++: `{inventory['cpp_files']}`",
            f"- Cabeceras: `{inventory['headers']}`",
            f"- Lenguajes: `{languages}`",
            f"- Build principal: `{inventory['build_tool']}`",
            f"- Funciones CMake sensibles: `{dangerous}`",
            f"- Autorización: `{authorization['authorization_scope']}`",
        )
    )


def _process_result(
    skill_name: str,
    root: Path,
    tool: ToolResolution,
    argv: list[str],
    result: ProcessResult,
    authorization: dict[str, Any],
    *,
    success: str,
    failure: str,
    extra: dict[str, Any] | None = None,
) -> SkillResult:
    ok = result.returncode == 0 and not result.timed_out
    lines = [
        success if ok else failure,
        "",
        f"- Proyecto: `{root}`",
        f"- Herramienta: `{tool.path}`",
        f"- Exit code: `{result.returncode}`",
        f"- Timeout: `{'sí' if result.timed_out else 'no'}`",
        f"- Duración: `{result.duration_ms} ms`",
    ]
    if result.output.strip():
        lines.extend(("", result.output.strip()))
    return SkillResult(
        ok,
        "\n".join(lines),
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "tool_path": str(tool.path),
            "tool_source": tool.source,
            "command_argv": argv,
            "cwd": result.cwd,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "shell": False,
            **(extra or {}),
            **authorization,
        },
    )


def _multi_process_result(
    skill_name: str,
    root: Path,
    tool: ToolResolution,
    commands: list[list[str]],
    results: list[ProcessResult],
    authorization: dict[str, Any],
    *,
    success: str,
    failure: str,
) -> SkillResult:
    ok = all(result.returncode == 0 and not result.timed_out for result in results)
    output = "\n".join(result.output.strip() for result in results if result.output.strip())
    duration_ms = sum(result.duration_ms for result in results)
    lines = [
        success if ok else failure,
        "",
        f"- Proyecto: `{root}`",
        f"- Herramienta: `{tool.path}`",
        f"- Etapas ejecutadas: `{len(results)}`",
        f"- Timeout: `{'sí' if any(result.timed_out for result in results) else 'no'}`",
        f"- Duración: `{duration_ms} ms`",
    ]
    if output:
        lines.extend(("", output))
    last = results[-1]
    return SkillResult(
        ok,
        "\n".join(lines),
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "tool_path": str(tool.path),
            "tool_source": tool.source,
            "command_argv": commands,
            "returncode": last.returncode,
            "duration_ms": duration_ms,
            "timed_out": any(result.timed_out for result in results),
            "stdout": "\n".join(result.stdout for result in results),
            "stderr": "\n".join(result.stderr for result in results),
            "stdout_truncated": any(result.stdout_truncated for result in results),
            "stderr_truncated": any(result.stderr_truncated for result in results),
            "shell": False,
            **authorization,
        },
    )


def _tool_unavailable(
    skill_name: str,
    root: Path,
    tool_name: str,
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        f"No se encontró la herramienta requerida: {tool_name}.",
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "tool_name": tool_name,
            "tool_unavailable": True,
            "stage_status": "unavailable",
            "shell": False,
            **authorization,
        },
    )


def _skipped_result(
    skill_name: str,
    root: Path,
    message: str,
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        True,
        message,
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "stage_status": "skipped",
            "shell": False,
            **authorization,
        },
    )


def _file_limit_result(
    skill_name: str,
    root: Path,
    settings: dict[str, Any],
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        f"El proyecto supera el límite de {settings['max_native_files']} archivos C/C++.",
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "file_limit_exceeded": True,
            "stage_status": "failed",
            **authorization,
        },
    )


def _stage_status(result: SkillResult) -> str:
    explicit = str(result.data.get("stage_status", ""))
    if explicit in {"passed", "failed", "unavailable", "skipped"}:
        return explicit
    if result.data.get("tool_unavailable"):
        return "unavailable"
    return "passed" if result.ok else "failed"


def _overall_status(stages: list[dict[str, Any]]) -> str:
    statuses = {str(stage.get("status")) for stage in stages}
    if "failed" in statuses:
        return "failed"
    if "unavailable" in statuses:
        return "partial"
    return "passed"


def _enabled_stage_names(settings: dict[str, Any]) -> list[str]:
    names = ["inspect"]
    for name, key in (
        ("descriptor", "descriptor_enabled"),
        ("c_syntax", "c_syntax_enabled"),
        ("cpp_syntax", "cpp_syntax_enabled"),
        ("static", "static_enabled"),
        ("build", "build_enabled"),
        ("tests", "tests_enabled"),
    ):
        if settings[key]:
            names.append(name)
    return names


def _approval_details(
    skill_name: str,
    root: Path,
    scope: str,
    source: str,
    timeout_seconds: int,
    argv: list[str],
    risk_note: str,
) -> dict[str, Any]:
    return {
        "approval_summary": "\n".join(
            (
                f"Skill: {skill_name}",
                f"Proyecto: {root}",
                f"Ruta resuelta: {root}",
                f"Alcance de autorización: {scope}",
                f"Origen de autorización: {source}",
                f"Riesgo: medio. {risk_note}",
                f"Timeout: {timeout_seconds} segundos",
                f"Acción exacta: {' '.join(argv)}",
            )
        ),
        "resolved_path": str(root),
        "project_root": str(root),
        "authorization_scope": scope,
        "authorization_source": source,
        "timeout_seconds": timeout_seconds,
        "command_argv": argv,
        "risk_note": risk_note,
    }


def _setting(
    params: dict[str, Any],
    profile: dict[str, Any],
    name: str,
    default: bool,
) -> bool:
    if name in params:
        return params[name] is True
    if name in profile:
        return bool(profile[name])
    return default


def _choice_setting(value: Any, current: Any, allowed: set[str], field: str) -> str:
    selected = str(current if value is None else value).strip().casefold()
    if selected not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{field} debe ser uno de: {choices}.")
    return selected


def _bounded_files(value: Any, default: int) -> int:
    resolved = default if value is None else int(value)
    if not 1 <= resolved <= 20_000:
        raise ValueError("max_files debe estar entre 1 y 20000.")
    return resolved


def _response_file_value(path: Path) -> str:
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _bounded_text(value: str, limit: int) -> str:
    clean = value.strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"
