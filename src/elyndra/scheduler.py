from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from elyndra.automation import AutomationRepository
from elyndra.db import Database
from elyndra.paths import ElyndraPaths

_SCHEDULER_STATUSES = ("running", "stopped", "failed")
_NOTIFICATION_STATUSES = ("pending", "seen", "dismissed")
_MIN_INTERVAL_SECONDS = 15
_MAX_INTERVAL_SECONDS = 3_600
_MAX_NOTIFICATIONS = 500


class SchedulerAlreadyRunningError(RuntimeError):
    """Raised when another local scheduler owns the process lock."""


class LocalScheduler:
    """Optional local scheduler with an inter-process lock and durable notifications."""

    def __init__(
        self,
        database: Database,
        paths: ElyndraPaths,
        automation: AutomationRepository,
    ) -> None:
        self.database = database
        self.paths = paths
        self.automation = automation

    @property
    def lock_path(self) -> Path:
        return self.paths.state_dir / "automation-scheduler.lock"

    def status(self) -> dict[str, Any]:
        lock = _inspect_lock(self.lock_path)
        with self.database.connect() as connection:
            latest = connection.execute(
                """
                SELECT * FROM assistant_scheduler_sessions
                ORDER BY started_at DESC, id DESC LIMIT 1
                """
            ).fetchone()
            pending_notifications = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_local_notifications "
                    "WHERE status = 'pending'"
                ).fetchone()[0]
            )
        return {
            "available": True,
            "running": bool(lock["running"]),
            "lock_path": str(self.lock_path),
            "lock_metadata": lock["metadata"],
            "latest_session": _public_session(latest) if latest is not None else None,
            "pending_notifications": pending_notifications,
            "optional": True,
            "clean_shutdown": True,
            "interprocess_lock": True,
            "network_delivery": False,
            "system_service_installation": False,
            "browser_notifications": True,
            "terminal_notifications": True,
        }

    def render_overview(self) -> str:
        status = self.status()
        latest = status.get("latest_session") or {}
        lines = [
            "Scheduler local opcional",
            f"- Activo: {'sí' if status['running'] else 'no'}",
            f"- Notificaciones pendientes: {status['pending_notifications']}",
            f"- Última sesión: {latest.get('status', 'sin sesiones')}",
            "- Bloqueo exclusivo entre procesos: sí",
            "- Apagado limpio: sí",
            "- Servicio del sistema instalado: no",
            "- Red y entrega externa: no",
        ]
        return "\n".join(lines)

    def open(
        self,
        *,
        interval_seconds: int,
        actor: str,
        mode: str,
    ) -> SchedulerLease:
        interval = _bounded_interval(interval_seconds)
        handle = _acquire_lock(
            self.lock_path,
            {
                "pid": os.getpid(),
                "actor": actor,
                "mode": mode,
                "interval_seconds": interval,
                "started_at": _now(),
            },
        )
        public_id = uuid.uuid4().hex
        now = _now()
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO assistant_scheduler_sessions(
                        public_id, mode, status, pid, interval_seconds,
                        lock_path, started_at, heartbeat_at, stopped_at,
                        scans_count, runs_created, notifications_created,
                        last_error, created_by
                    ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, NULL, 0, 0, 0, '', ?)
                    """,
                    (
                        public_id,
                        mode,
                        os.getpid(),
                        interval,
                        str(self.lock_path),
                        now,
                        now,
                        actor,
                    ),
                )
        except Exception:
            _release_lock(handle, self.lock_path)
            raise
        return SchedulerLease(
            scheduler=self,
            handle=handle,
            public_id=public_id,
            interval_seconds=interval,
            actor=actor,
            mode=mode,
        )

    def run_cycle(
        self,
        *,
        actor: str,
        source: str,
        now_value: str | None = None,
    ) -> dict[str, Any]:
        scan = self.automation.scan_due(now_value=now_value, actor=actor)
        notifications = self._materialize_notifications()
        return {
            "source": source,
            "scan": scan,
            "notifications": notifications,
            "summary": {
                **scan["summary"],
                "notifications_created": len(notifications),
            },
            "network_delivery": False,
            "external_processes": False,
        }

    def list_notifications(
        self,
        *,
        status: str = "all",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean != "all" and clean not in _NOTIFICATION_STATUSES:
            raise ValueError("Estado de notificación inválido.")
        where = "" if clean == "all" else "WHERE n.status = ?"
        safe_limit = max(1, min(limit, _MAX_NOTIFICATIONS))
        params: tuple[Any, ...] = (
            (safe_limit,) if clean == "all" else (clean, safe_limit)
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT n.*, i.public_id AS inbox_public_id,
                       r.public_id AS run_public_id
                FROM assistant_local_notifications n
                JOIN assistant_local_inbox i ON i.id = n.inbox_id
                JOIN assistant_automation_runs r ON r.id = i.run_id
                """
                f"{where} ORDER BY n.created_at DESC, n.id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_public_notification(row) for row in rows]

    def update_notification_status(
        self,
        notification_id: str,
        *,
        status: str,
    ) -> dict[str, Any]:
        clean = status.strip().casefold()
        if clean not in _NOTIFICATION_STATUSES:
            raise ValueError("Estado de notificación inválido.")
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM assistant_local_notifications WHERE public_id = ?",
                (notification_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Notificación local no encontrada.")
            connection.execute(
                """
                UPDATE assistant_local_notifications
                SET status = ?, seen_at = CASE WHEN ? = 'seen' THEN ? ELSE seen_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (clean, clean, now, now, int(row["id"])),
            )
            updated = connection.execute(
                """
                SELECT n.*, i.public_id AS inbox_public_id,
                       r.public_id AS run_public_id
                FROM assistant_local_notifications n
                JOIN assistant_local_inbox i ON i.id = n.inbox_id
                JOIN assistant_automation_runs r ON r.id = i.run_id
                WHERE n.id = ?
                """,
                (int(row["id"]),),
            ).fetchone()
        return _public_notification(updated)

    def _materialize_notifications(self) -> list[dict[str, Any]]:
        created_ids: list[str] = []
        now = _now()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT i.id, i.title, i.body
                FROM assistant_local_inbox i
                LEFT JOIN assistant_local_notifications n ON n.inbox_id = i.id
                WHERE i.status = 'unread' AND n.id IS NULL
                ORDER BY i.visible_at ASC, i.id ASC
                LIMIT ?
                """,
                (_MAX_NOTIFICATIONS,),
            ).fetchall()
            for row in rows:
                public_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO assistant_local_notifications(
                        public_id, inbox_id, title, body, status,
                        created_at, seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, NULL, ?)
                    """,
                    (
                        public_id,
                        int(row["id"]),
                        str(row["title"])[:200],
                        str(row["body"])[:6000],
                        now,
                        now,
                    ),
                )
                created_ids.append(public_id)
        if not created_ids:
            return []
        created = self.list_notifications(status="pending", limit=len(created_ids) + 20)
        allowed = set(created_ids)
        return [item for item in created if item["public_id"] in allowed]

    def _heartbeat(
        self,
        session_id: str,
        *,
        cycle: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        runs_created = 0
        notifications_created = 0
        if cycle is not None:
            summary = cycle.get("summary", {})
            runs_created = int(summary.get("created", 0))
            notifications_created = int(summary.get("notifications_created", 0))
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE assistant_scheduler_sessions
                SET heartbeat_at = ?, scans_count = scans_count + ?,
                    runs_created = runs_created + ?,
                    notifications_created = notifications_created + ?,
                    last_error = ?
                WHERE public_id = ? AND status = 'running'
                """,
                (
                    _now(),
                    1 if cycle is not None else 0,
                    runs_created,
                    notifications_created,
                    error[:1000],
                    session_id,
                ),
            )

    def _finish(self, session_id: str, *, status: str, error: str = "") -> None:
        if status not in _SCHEDULER_STATUSES:
            raise ValueError("Estado de scheduler inválido.")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE assistant_scheduler_sessions
                SET status = ?, heartbeat_at = ?, stopped_at = ?, last_error = ?
                WHERE public_id = ?
                """,
                (status, _now(), _now(), error[:1000], session_id),
            )


class SchedulerLease:
    """An acquired scheduler lock and its durable session."""

    def __init__(
        self,
        *,
        scheduler: LocalScheduler,
        handle: TextIO,
        public_id: str,
        interval_seconds: int,
        actor: str,
        mode: str,
    ) -> None:
        self.scheduler = scheduler
        self.handle = handle
        self.public_id = public_id
        self.interval_seconds = interval_seconds
        self.actor = actor
        self.mode = mode
        self._closed = False

    def cycle(self, *, now_value: str | None = None) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("La sesión del scheduler ya está cerrada.")
        try:
            result = self.scheduler.run_cycle(
                actor=self.actor,
                source=self.mode,
                now_value=now_value,
            )
        except Exception as exc:
            self.scheduler._heartbeat(self.public_id, error=str(exc))
            raise
        self.scheduler._heartbeat(self.public_id, cycle=result)
        return result

    def run_forever(
        self,
        stop_event: threading.Event,
        *,
        on_cycle: Any | None = None,
    ) -> None:
        try:
            while not stop_event.is_set():
                result = self.cycle()
                if on_cycle is not None:
                    on_cycle(result)
                stop_event.wait(self.interval_seconds)
        except Exception as exc:
            self.close(status="failed", error=str(exc))
            raise
        self.close(status="stopped")

    def close(self, *, status: str = "stopped", error: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        self.scheduler._finish(self.public_id, status=status, error=error)
        _release_lock(self.handle, self.scheduler.lock_path)


class SchedulerController:
    """Own a scheduler thread inside the local web runtime."""

    def __init__(self, scheduler: LocalScheduler, *, actor: str) -> None:
        self.scheduler = scheduler
        self.actor = actor
        self._lock = threading.RLock()
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._lease: SchedulerLease | None = None
        self._last_error = ""

    def start(self, *, interval_seconds: int) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("El scheduler web ya está activo.")
            lease = self.scheduler.open(
                interval_seconds=interval_seconds,
                actor=self.actor,
                mode="web",
            )
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(lease, stop_event),
                name="elyndra-local-scheduler",
                daemon=False,
            )
            self._lease = lease
            self._stop_event = stop_event
            self._thread = thread
            self._last_error = ""
            thread.start()
        return self.status()

    def stop(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
        if thread is None or stop_event is None:
            return self.status()
        stop_event.set()
        thread.join(timeout=max(0.1, timeout_seconds))
        if thread.is_alive():
            raise RuntimeError("El scheduler no terminó dentro del tiempo esperado.")
        return self.status()

    def close(self) -> None:
        with suppress(Exception):
            self.stop(timeout_seconds=5.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
            last_error = self._last_error
        return {
            **self.scheduler.status(),
            "web_thread_running": thread_alive,
            "web_thread_error": last_error,
        }

    def _run(self, lease: SchedulerLease, stop_event: threading.Event) -> None:
        try:
            lease.run_forever(stop_event)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
        finally:
            with self._lock:
                self._lease = None
                self._stop_event = None
                self._thread = None


def scheduler_query(text: str) -> bool:
    clean = text.casefold()
    terms = (
        "scheduler",
        "programador local",
        "notificaciones locales",
        "avisos locales",
        "scheduler activo",
    )
    return any(term in clean for term in terms)


def _acquire_lock(path: Path, metadata: dict[str, Any]) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with suppress(PermissionError):
        path.parent.chmod(0o700)
    handle = path.open("a+", encoding="utf-8")
    with suppress(PermissionError):
        path.chmod(0o600)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.seek(0)
        current = handle.read().strip()
        handle.close()
        detail = f" Metadatos: {current}" if current else ""
        raise SchedulerAlreadyRunningError(
            "Ya existe un scheduler local activo." + detail
        ) from exc
    handle.seek(0)
    handle.truncate(0)
    handle.write(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def _release_lock(handle: TextIO, path: Path) -> None:
    with suppress(OSError):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    with suppress(OSError):
        handle.close()
    with suppress(OSError):
        path.unlink()


def _inspect_lock(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"running": False, "metadata": {}}
    handle = path.open("a+", encoding="utf-8")
    handle.seek(0)
    raw = handle.read().strip()
    metadata: dict[str, Any]
    try:
        parsed = json.loads(raw) if raw else {}
        metadata = parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        metadata = {}
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return {"running": True, "metadata": metadata}
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
    return {"running": False, "metadata": metadata}


def _bounded_interval(value: int) -> int:
    interval = int(value)
    if interval < _MIN_INTERVAL_SECONDS or interval > _MAX_INTERVAL_SECONDS:
        raise ValueError(
            f"El intervalo debe estar entre {_MIN_INTERVAL_SECONDS} y "
            f"{_MAX_INTERVAL_SECONDS} segundos."
        )
    return interval


def _public_session(row: Any) -> dict[str, Any]:
    return {
        "public_id": str(row["public_id"]),
        "mode": str(row["mode"]),
        "status": str(row["status"]),
        "pid": int(row["pid"]),
        "interval_seconds": int(row["interval_seconds"]),
        "lock_path": str(row["lock_path"]),
        "started_at": str(row["started_at"]),
        "heartbeat_at": str(row["heartbeat_at"]),
        "stopped_at": str(row["stopped_at"] or ""),
        "scans_count": int(row["scans_count"]),
        "runs_created": int(row["runs_created"]),
        "notifications_created": int(row["notifications_created"]),
        "last_error": str(row["last_error"]),
        "created_by": str(row["created_by"]),
    }


def _public_notification(row: Any) -> dict[str, Any]:
    return {
        "public_id": str(row["public_id"]),
        "inbox_public_id": str(row["inbox_public_id"]),
        "run_public_id": str(row["run_public_id"]),
        "title": str(row["title"]),
        "body": str(row["body"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "seen_at": str(row["seen_at"] or ""),
        "updated_at": str(row["updated_at"]),
        "external_delivery": False,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
