from __future__ import annotations

import os
import stat
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.process import run_controlled_process
from elyndra.skills.tool_resolution import ToolResolution, resolve_project_tool

_JAVA_EXTENSIONS = {".java"}
_PROJECT_MARKERS = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
)
_DEFAULT_EXCLUDES = {
    ".git",
    ".gradle",
    ".idea",
    "build",
    "out",
    "target",
}
_FRAMEWORK_HINTS = {
    "org.springframework": "Spring",
    "junit": "JUnit",
    "org.junit": "JUnit",
    "jakarta": "Jakarta",
    "javax": "Java EE / javax",
    "hibernate": "Hibernate",
    "quarkus": "Quarkus",
    "micronaut": "Micronaut",
}


class JavaProjectInspectSkill:
    name = "java.project_inspect"
    description = "Inspecciona un proyecto Java/JVM sin ejecutar código ni wrappers."
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
            context.config.java_tool_timeout_seconds,
            ["inspect-java-project", str(root)],
            "Solo se leen nombres y metadatos acotados; no se ejecuta el proyecto.",
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


class JavaDescriptorValidateSkill:
    name = "java.descriptor_validate"
    description = "Valida pom.xml y estructura Gradle sin ejecutar Maven, Gradle o wrappers."
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
            context.config.java_tool_timeout_seconds,
            ["validate-java-descriptors", str(root)],
            "Se analizan XML y nombres de archivos Gradle; no se evalúan scripts.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        report = _validate_descriptors(root)
        ok = not report["errors"]
        lines = [
            "Descriptores Java válidos." if ok else "Descriptores Java con errores.",
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


class JavacCompileSkill:
    name = "java.javac_compile"
    description = "Compila Java con javac -proc:none hacia una carpeta temporal."
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
            ["javac", "-proc:none", "-d", "<temporal>", "@<archivos>"],
            "No se ejecutan clases ni procesadores de anotaciones.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        files, truncated = _collect_java_files(
            target,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_java_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        if not files:
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos Java para compilar.",
                authorization,
            )
        tool = resolve_project_tool(root, "javac")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "javac", authorization)
        with tempfile.TemporaryDirectory(prefix="elyndra-javac-") as temp_dir:
            temp = Path(temp_dir)
            argfile = temp / "sources.txt"
            argfile.write_text(
                "\n".join(_javac_argfile_value(path) for path in files),
                encoding="utf-8",
            )
            argv = [
                str(tool.path),
                "-proc:none",
                "-encoding",
                "UTF-8",
                "-d",
                str(temp / "classes"),
            ]
            release = settings.get("java_release")
            if release is not None:
                argv.extend(("--release", str(release)))
            argv.append(f"@{argfile}")
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
            success="Compilación Java correcta.",
            failure="javac encontró problemas.",
            extra={"files_examined": len(files)},
        )


class JavaBuildSkill:
    name = "java.build_project"
    description = "Compila con Maven o Gradle global en modo offline y sin wrappers."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        tool_name = _selected_build_tool(root, settings)
        argv = _build_argv(tool_name, tests=False)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            argv,
            "Maven/Gradle pueden cargar plugins del proyecto; se fuerza modo offline.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_build_stage(context, params, skill_name=self.name, tests=False)


class JavaTestSkill:
    name = "java.test_project"
    description = "Ejecuta tests Maven o Gradle globales en modo offline y sin wrappers."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        tool_name = _selected_build_tool(root, settings)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            _build_argv(tool_name, tests=True),
            "Los tests ejecutan código del proyecto; se fuerza modo offline.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_build_stage(context, params, skill_name=self.name, tests=True)


class JavaVerifyProjectSkill:
    name = "java.verify_project"
    description = "Ejecuta la verificación Java/JVM completa y guarda un historial comparable."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        stages = _enabled_stage_names(settings)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["java-verify", *stages, str(root)],
            "La compilación y los tests requieren aprobación y no usan wrappers.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        started = time.perf_counter()
        plan = {
            "stages": _enabled_stage_names(settings),
            "build_tool": _selected_build_tool(root, settings),
            "require_tools": settings["require_tools"],
            "fail_fast": settings["fail_fast"],
        }
        profile = settings.get("profile") or {}
        run_id = context.verification_runs.start(
            toolchain="java",
            project_root=root,
            actor=context.actor,
            profile_id=profile.get("id"),
            plan=plan,
        )
        stages: list[dict[str, Any]] = []
        stage_specs = (
            ("inspect", True, JavaProjectInspectSkill()),
            ("descriptor", settings["descriptor_enabled"], JavaDescriptorValidateSkill()),
            ("javac", settings["javac_enabled"], JavacCompileSkill()),
            ("build", settings["build_enabled"], JavaBuildSkill()),
            ("tests", settings["tests_enabled"], JavaTestSkill()),
        )
        for stage_name, enabled, skill in stage_specs:
            if not enabled:
                message = _disabled_stage_message(stage_name, settings)
                stages.append(
                    {"name": stage_name, "status": "skipped", "message": message}
                )
                continue
            stage_params = dict(params)
            result = skill.execute(context, stage_params)
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
            "passed": "Verificación Java correcta.",
            "partial": "Verificación Java parcial.",
            "failed": "Verificación Java fallida.",
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


def _execute_build_stage(
    context: SkillContext,
    params: dict[str, Any],
    *,
    skill_name: str,
    tests: bool,
) -> SkillResult:
    root = _discover_project_root(_resolve_existing_path(params))
    settings = _pipeline_settings(context, root, params)
    authorization = _authorize_project(context, root, params, settings=settings)
    build_tool = _selected_build_tool(root, settings)
    if build_tool == "javac":
        if tests:
            return _skipped_result(
                skill_name,
                root,
                "No hay runner de tests configurado para un proyecto javac directo.",
                authorization,
            )
        return JavacCompileSkill().execute(context, params)
    if build_tool not in {"maven", "gradle"}:
        return _skipped_result(
            skill_name,
            root,
            "No se detectó pom.xml ni build.gradle(.kts).",
            authorization,
        )
    binary = "mvn" if build_tool == "maven" else "gradle"
    tool = resolve_project_tool(root, binary)
    if tool.path is None:
        return _tool_unavailable(skill_name, root, binary, authorization)
    argv = [str(tool.path), *_build_argv(build_tool, tests=tests)[1:]]
    result = run_controlled_process(
        argv,
        cwd=root,
        timeout_seconds=settings["timeout_seconds"],
        max_output_chars=settings["max_output_chars"],
        environment={
            "MAVEN_OPTS": "-Dstyle.color=never",
            "GRADLE_OPTS": "-Dorg.gradle.daemon=false",
        },
    )
    action = "Tests" if tests else "Build"
    return _process_result(
        skill_name,
        root,
        tool,
        argv,
        result,
        authorization,
        success=f"{action} Java finalizó correctamente.",
        failure=f"{action} Java encontró problemas.",
        extra={"build_tool": build_tool},
    )


def _build_argv(build_tool: str, *, tests: bool) -> list[str]:
    if build_tool == "maven":
        goal = "test" if tests else "compile"
        return ["mvn", "--offline", "--batch-mode", "--no-transfer-progress", goal]
    if build_tool == "gradle":
        task = "test" if tests else "classes"
        return ["gradle", "--offline", "--no-daemon", "--console=plain", task]
    return ["javac", "-proc:none", "@<archivos>"]


def _selected_build_tool(root: Path, settings: dict[str, Any]) -> str:
    configured = str(settings.get("build_tool", "auto"))
    if configured != "auto":
        return configured
    if (root / "pom.xml").is_file():
        return "maven"
    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        return "gradle"
    return "javac"


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
        if any((current / marker).is_file() for marker in _PROJECT_MARKERS):
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


def _pipeline_settings(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    effective = context.java_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.java_tool_timeout_seconds,
        default_max_output_chars=context.config.java_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    configured_build_tool = str(
        params.get("build_tool")
        if params.get("build_tool") is not None
        else profile.get("build_tool", "auto")
    ).strip().casefold()
    selected_build_tool = _selected_build_tool(
        root,
        {"build_tool": configured_build_tool},
    )
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_java_files": _bounded_files(
            params.get("max_files"),
            int(effective["max_java_files"]),
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "descriptor_enabled": _setting(params, profile, "descriptor_enabled", True),
        "javac_enabled": _setting(
            params,
            profile,
            "javac_enabled",
            selected_build_tool == "javac",
        ),
        "build_enabled": _setting(params, profile, "build_enabled", True),
        "tests_enabled": _setting(params, profile, "tests_enabled", True),
        "build_tool": configured_build_tool,
        "selected_build_tool": selected_build_tool,
        "java_release": _release_value(
            params.get("java_release"),
            profile.get("java_release"),
        ),
        "fail_fast": _setting(params, profile, "fail_fast", False),
        "require_tools": _setting(params, profile, "require_tools", False),
    }


def _disabled_stage_message(stage_name: str, settings: dict[str, Any]) -> str:
    if stage_name == "javac" and settings.get("selected_build_tool") in {
        "maven",
        "gradle",
    }:
        return (
            "Omitido por defecto: Maven/Gradle administra el classpath y las "
            "dependencias del proyecto."
        )
    return "Etapa desactivada por configuración."


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


def _release_value(value: Any, current: Any) -> int | None:
    selected = current if value is None else value
    if selected in (None, ""):
        return None
    release = int(selected)
    if not 8 <= release <= 99:
        raise ValueError("java_release debe estar entre 8 y 99.")
    return release


def _bounded_files(value: Any, default: int) -> int:
    resolved = default if value is None else int(value)
    if not 1 <= resolved <= 20_000:
        raise ValueError("max_files debe estar entre 1 y 20000.")
    return resolved


def _collect_java_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    if target.is_file():
        return ([target] if target.suffix.casefold() in _JAVA_EXTENSIONS else []), False
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
            if candidate.suffix.casefold() not in _JAVA_EXTENSIONS:
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
    files, truncated = _collect_java_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_java_files"],
    )
    pom = _read_pom(root / "pom.xml")
    gradle_text = _read_gradle_text(root)
    frameworks = sorted(_frameworks(pom, gradle_text))
    test_count = sum(
        1
        for path in files
        if "src/test" in path.relative_to(root).as_posix()
        or path.name.endswith(("Test.java", "Tests.java"))
    )
    wrappers = [
        name
        for name in ("mvnw", "mvnw.cmd", "gradlew", "gradlew.bat")
        if (root / name).exists()
    ]
    return {
        "project_root": str(root),
        "java_files": len(files),
        "test_files": test_count,
        "truncated": truncated,
        "build_tool": _selected_build_tool(root, settings),
        "pom": bool(pom),
        "artifact": pom.get("artifact", ""),
        "group": pom.get("group", ""),
        "version": pom.get("version", ""),
        "gradle": bool(gradle_text),
        "frameworks": frameworks,
        "wrappers_detected": wrappers,
        "wrappers_executed": False,
        "tools": {
            name: resolve_project_tool(root, name).path is not None
            for name in ("java", "javac", "mvn", "gradle")
        },
    }


def _read_pom(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="strict"))
    except (ET.ParseError, OSError, UnicodeError):
        return {"parse_error": True}
    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0] + "}"

    def text(name: str) -> str:
        node = root.find(f"{namespace}{name}")
        return str(node.text or "").strip() if node is not None else ""

    dependencies: list[str] = []
    for dependency in root.findall(f".//{namespace}dependency"):
        group = dependency.find(f"{namespace}groupId")
        artifact = dependency.find(f"{namespace}artifactId")
        value = ":".join(
            part
            for part in (
                str(group.text or "").strip() if group is not None else "",
                str(artifact.text or "").strip() if artifact is not None else "",
            )
            if part
        )
        if value:
            dependencies.append(value)
    return {
        "model_version": text("modelVersion"),
        "group": text("groupId"),
        "artifact": text("artifactId"),
        "version": text("version"),
        "packaging": text("packaging"),
        "dependencies": dependencies[:200],
    }


def _read_gradle_text(root: Path) -> str:
    chunks: list[str] = []
    for name in (
        "settings.gradle",
        "settings.gradle.kts",
        "build.gradle",
        "build.gradle.kts",
    ):
        path = root / name
        if not path.is_file():
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="strict")[:200_000])
        except (OSError, UnicodeError):
            continue
    return "\n".join(chunks)


def _frameworks(pom: dict[str, Any], gradle_text: str) -> set[str]:
    haystack = "\n".join([*pom.get("dependencies", []), gradle_text]).casefold()
    return {
        label
        for token, label in _FRAMEWORK_HINTS.items()
        if token.casefold() in haystack
    }


def _validate_descriptors(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    pom_path = root / "pom.xml"
    pom = _read_pom(pom_path)
    gradle_files = [
        path
        for path in (root / "build.gradle", root / "build.gradle.kts")
        if path.is_file()
    ]
    if pom_path.is_file():
        if pom.get("parse_error"):
            errors.append("pom.xml no es XML válido o no es UTF-8.")
        else:
            if not pom.get("artifact"):
                errors.append("pom.xml no declara artifactId.")
            if pom.get("model_version") not in {"", "4.0.0"}:
                warnings.append("modelVersion de pom.xml no es 4.0.0.")
    for path in gradle_files:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            errors.append(f"{path.name} no puede leerse como UTF-8.")
            continue
        if not text.strip():
            errors.append(f"{path.name} está vacío.")
    if pom_path.is_file() and gradle_files:
        warnings.append("Existen descriptores Maven y Gradle; confirma cuál es canónico.")
    wrappers = [name for name in ("mvnw", "gradlew") if (root / name).exists()]
    if wrappers:
        warnings.append("Se detectaron wrappers; Elyndra no los ejecutará.")
    return {
        "build_tool": _selected_build_tool(root, {"build_tool": "auto"}),
        "errors": errors,
        "warnings": warnings,
    }


def _format_inventory(inventory: dict[str, Any], authorization: dict[str, Any]) -> str:
    frameworks = ", ".join(inventory["frameworks"]) or "no detectado"
    wrappers = ", ".join(inventory["wrappers_detected"]) or "ninguno"
    return "\n".join(
        (
            "Inspección Java completada sin ejecutar código.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- Archivos Java: `{inventory['java_files']}`",
            f"- Tests detectados: `{inventory['test_files']}`",
            f"- Build principal: `{inventory['build_tool']}`",
            f"- Artefacto Maven: `{inventory['artifact'] or '-'}`",
            f"- Frameworks: `{frameworks}`",
            f"- Wrappers detectados: `{wrappers}` (no ejecutados)",
            f"- Autorización: `{authorization['authorization_scope']}`",
        )
    )


def _process_result(
    skill_name: str,
    root: Path,
    tool: ToolResolution,
    argv: list[str],
    result: Any,
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
        f"El proyecto supera el límite de {settings['max_java_files']} archivos Java.",
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
        ("javac", "javac_enabled"),
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


def _javac_argfile_value(path: Path) -> str:
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _bounded_text(value: str, limit: int) -> str:
    clean = value.strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"
