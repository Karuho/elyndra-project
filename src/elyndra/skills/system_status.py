from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult


class SystemStatusSkill:
    name = "system.status"
    description = "Consulta CPU, carga, RAM, disco y uptime usando información local."
    risk = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        del context, params
        memory = _read_meminfo()
        total_bytes = memory.get("MemTotal", 0) * 1024
        available_bytes = memory.get("MemAvailable", 0) * 1024
        used_bytes = max(0, total_bytes - available_bytes)
        disk = shutil.disk_usage(Path.home())
        load = os.getloadavg()
        uptime_seconds = _read_uptime()
        cpu_count = os.cpu_count() or 1

        data = {
            "cpu_threads": cpu_count,
            "load_1m": round(load[0], 2),
            "load_5m": round(load[1], 2),
            "load_15m": round(load[2], 2),
            "ram_total_mb": round(total_bytes / 1024**2, 1),
            "ram_used_mb": round(used_bytes / 1024**2, 1),
            "ram_used_percent": round((used_bytes / total_bytes * 100) if total_bytes else 0, 1),
            "home_disk_used_percent": round(disk.used / disk.total * 100, 1),
            "uptime_hours": round(uptime_seconds / 3600, 2),
        }
        message = (
            f"CPU: {cpu_count} hilos, carga 1m {data['load_1m']}. "
            f"RAM: {data['ram_used_mb']} MB / {data['ram_total_mb']} MB "
            f"({data['ram_used_percent']}%). Disco HOME: "
            f"{data['home_disk_used_percent']}% usado. Uptime: {data['uptime_hours']} h."
        )
        return SkillResult(True, message, data)


def _read_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            number = raw.strip().split()[0]
            values[key] = int(number)
    except (OSError, ValueError, IndexError):
        pass
    return values


def _read_uptime() -> float:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return time.monotonic()
