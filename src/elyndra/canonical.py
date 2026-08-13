from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from elyndra.persona import AgentPersona


@dataclass(frozen=True, slots=True)
class CanonicalAnswer:
    intent: str
    text: str


def canonical_answer(
    text: str,
    persona: AgentPersona,
    response_language: str,
    *,
    session_summary: str = "",
) -> CanonicalAnswer | None:
    normalized = _normalize(text)
    compact = normalized.replace(" ", "")

    if _matches_session_recap(normalized, compact) and session_summary.strip():
        return CanonicalAnswer(
            "session_recap",
            _session_recap_text(session_summary, response_language),
        )
    if _matches_identity(normalized, compact):
        return CanonicalAnswer(
            "identity",
            _identity_text(persona, response_language),
        )
    if _matches_owner(normalized, compact):
        return CanonicalAnswer(
            "ownership",
            _ownership_text(persona, response_language),
        )
    if _matches_principles(normalized, compact):
        return CanonicalAnswer(
            "principles",
            _principles_text(persona, response_language),
        )
    if _matches_requirements(normalized, compact):
        return CanonicalAnswer(
            "requirements",
            _requirements_text(response_language),
        )
    if _matches_programming(normalized, compact):
        return CanonicalAnswer(
            "programming_capability",
            _programming_text(response_language),
        )
    return None


def _matches_session_recap(text: str, compact: str) -> bool:
    latin = (
        "en que quedamos",
        "donde quedamos",
        "que habiamos quedado",
        "retomemos",
        "resume este chat",
        "resumen de este chat",
        "where did we leave off",
        "what did we decide",
        "recap this chat",
    )
    return any(item in text for item in latin) or (
        "我们聊到哪里" in compact or "总结这个聊天" in compact
    )


def _session_recap_text(summary: str, language: str) -> str:
    lines = [
        " ".join(line.strip().split())
        for line in summary.splitlines()
        if line.strip()
    ]
    recent = lines[-5:]
    if not recent:
        if language == "en":
            return "This chat does not have a persistent summary yet."
        if language == "zh":
            return "这个聊天还没有持久化摘要。"
        return "Este chat todavía no tiene un resumen persistente."
    body = "\n".join(f"- {line}" for line in recent)
    if language == "en":
        return "This is where we left off:\n" + body
    if language == "zh":
        return "我们上次聊到这里：\n" + body
    return "Quedamos en esto:\n" + body


def _matches_identity(text: str, compact: str) -> bool:
    latin = (
        "que es elyndra",
        "que eres",
        "quien eres",
        "what is elyndra",
        "who are you",
    )
    return any(item in text for item in latin) or (
        "elyndra是什么" in compact or "什么是elyndra" in compact or "你是谁" in compact
    )


def _matches_owner(text: str, compact: str) -> bool:
    latin = (
        "quien controla los datos",
        "quien es dueno de los datos",
        "de quien son los datos",
        "who owns the data",
        "who controls the data",
    )
    return any(item in text for item in latin) or (
        "谁控制" in compact and "数据" in compact
    )


def _matches_principles(text: str, compact: str) -> bool:
    return any(
        item in text
        for item in (
            "principios de elyndra",
            "principios principales",
            "main principles",
            "elyndra principles",
        )
    ) or "原则" in compact


def _matches_requirements(text: str, compact: str) -> bool:
    latin = (
        "requisitos para correr elyndra",
        "requisitos para ejecutar elyndra",
        "requisitos necesita un pc",
        "requisitos minimos",
        "system requirements",
        "requirements to run elyndra",
    )
    return any(item in text for item in latin) or (
        "elyndra" in compact and ("配置要求" in compact or "系统要求" in compact)
    )


def _matches_programming(text: str, compact: str) -> bool:
    latin = (
        "sabes programar",
        "puedes programar",
        "puedes escribir codigo",
        "can you code",
        "can you program",
        "do you know programming",
    )
    return any(item in text for item in latin) or (
        "你会编程" in compact or "你能编程" in compact
    )


def _identity_text(persona: AgentPersona, language: str) -> str:
    if language == "en":
        return (
            f"{persona.project_name} is a local-first framework for building a private personal "
            f"agent. {persona.agent_name} is the configured assistant, and its memory, tools, "
            f"permissions and identity remain under {persona.owner_name}'s control."
        )
    if language == "zh":
        return (
            f"{persona.project_name} 是一个本地优先的私人个人智能代理框架。"
            f"{persona.agent_name} 是当前配置的助手，其记忆、工具、权限和身份由"
            f"{persona.owner_name} 控制。"
        )
    return (
        f"{persona.project_name} es un marco local-first para construir un agente personal "
        f"privado. {persona.agent_name} es el asistente configurado, y su memoria, herramientas, "
        f"permisos e identidad permanecen bajo el control de {persona.owner_name}."
    )


def _ownership_text(persona: AgentPersona, language: str) -> str:
    if language == "en":
        return (
            f"The data, memories and decisions belong to {persona.owner_name}, the owner running "
            f"{persona.project_name}. They must not be sent to third parties without explicit "
            "authorization."
        )
    if language == "zh":
        return (
            f"数据、记忆和决策属于运行 {persona.project_name} 的所有者 "
            f"{persona.owner_name}。未经明确授权，不得发送给第三方。"
        )
    return (
        f"Los datos, recuerdos y decisiones pertenecen a {persona.owner_name}, propietario de "
        f"{persona.project_name}. No deben enviarse a terceros sin autorización explícita."
    )


def _principles_text(persona: AgentPersona, language: str) -> str:
    lines = "\n".join(f"- {item}" for item in persona.principles)
    if language == "en":
        return "Elyndra's configured principles are:\n" + lines
    if language == "zh":
        return "Elyndra 当前配置的原则是：\n" + lines
    return "Los principios configurados de Elyndra son:\n" + lines


def _requirements_text(language: str) -> str:
    if language == "en":
        return (
            "Elyndra's core currently requires Linux, Python 3.11 or newer, and SQLite supplied "
            "by Python. Node.js, PHP, VS Code and a language model are optional. The core without "
            "a model is lightweight; memory requirements mainly depend on the chosen model. For "
            "the current 3B Q4 test model, 8 GB of system RAM is a preliminary practical floor, "
            "but formal benchmarks are still pending."
        )
    if language == "zh":
        return (
            "Elyndra 核心目前需要 Linux、Python 3.11 或更高版本，以及 Python 自带的 "
            "SQLite。Node.js、PHP、VS Code 和语言模型都是可选项。无模型时核心很轻量；"
            "内存需求主要取决于所选模型。对于当前测试的 3B Q4 模型，8 GB 系统内存是"
            "初步的实用下限，但仍需要正式基准测试。"
        )
    return (
        "El núcleo de Elyndra requiere actualmente Linux, Python 3.11 o superior y SQLite "
        "incluido con Python. Node.js, PHP, VS Code y el motor lingüístico son opcionales. Sin "
        "modelo, el núcleo es liviano; el consumo de memoria depende principalmente del modelo "
        "elegido. Para el modelo de prueba actual 3B Q4, 8 GB de RAM total es un mínimo práctico "
        "preliminar, pero todavía faltan benchmarks formales."
    )


def _programming_text(language: str) -> str:
    if language == "en":
        return (
            "Yes. I can analyze, explain, search and validate code inside authorized projects. "
            "I can also draft changes, but I should not claim that I edited files unless Elyndra "
            "actually executed an explicit, approved skill."
        )
    if language == "zh":
        return (
            "可以。我能在已授权的项目中分析、解释、搜索和验证代码，也能起草修改方案。"
            "但只有在 Elyndra 实际执行了明确且获准的技能时，我才会声称文件已被修改。"
        )
    return (
        "Sí. Puedo analizar, explicar, buscar y validar código dentro de proyectos autorizados. "
        "También puedo redactar cambios, pero no debo afirmar que edité archivos salvo que "
        "Elyndra haya ejecutado realmente una skill explícita y autorizada."
    )


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", without_marks).strip()
