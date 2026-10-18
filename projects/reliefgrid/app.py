"""ReliefGrid: a small, simulation-only incident coordination demo."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlsplit

from notifications import TwilioNotifier

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
DEFAULT_DB = os.environ.get("RELIEFGRID_DB", str(ROOT / "reliefgrid.db"))
MAX_BODY_BYTES = 16_384
ACKNOWLEDGEMENT_DELAY_SECONDS = 120
SEVERITIES = ("critical", "high", "medium", "low")
STATUSES = ("active", "resolved")
LOG = logging.getLogger("reliefgrid")

SEED_INCIDENTS = (
    ("RG-1042", "Flooding", "Waterlogging reported near the underpass.", "North District", "high"),
    ("RG-1041", "Medical", "First-aid support requested at the community shelter.", "Central District", "critical"),
    ("RG-1040", "Supply request", "Drinking water and dry food requested for 18 people.", "East District", "medium"),
)
SEED_RESOURCES = (
    ("Medical kits", "medical", 42, 60, "Central Warehouse"),
    ("Drinking water", "water", 128, 180, "North Depot"),
    ("Food packs", "food", 76, 120, "East Depot"),
)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_after(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def enqueue_critical_alerts(connection: sqlite3.Connection, incident_id: int, now: str) -> None:
    call_due_at = utc_after(now, ACKNOWLEDGEMENT_DELAY_SECONDS)
    connection.executemany(
        """INSERT OR IGNORE INTO notification_jobs
           (incident_id, channel, due_at, status, created_at, updated_at)
           VALUES (?, ?, ?, 'pending', ?, ?)""",
        (
            (incident_id, "sms", now, now, now),
            (incident_id, "call", call_due_at, now, now),
        ),
    )


def clean_text(value: Any, label: str, *, minimum: int = 1, maximum: int = 120) -> str:
    if not isinstance(value, str):
        raise ApiError(f"{label} must be text.")
    result = value.strip()
    if len(result) < minimum:
        raise ApiError(f"{label} must be at least {minimum} characters.")
    if len(result) > maximum:
        raise ApiError(f"{label} must be {maximum} characters or fewer.")
    return result


class ReliefGridApp:
    """SQLite-backed application logic, shared by the HTTP server and tests."""

    def __init__(self, database_path: str | Path = DEFAULT_DB) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        connection = self.connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    description TEXT NOT NULL,
                    district TEXT NOT NULL,
                    severity TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')),
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','resolved')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS incidents_status_severity
                    ON incidents(status, severity, created_at);
                CREATE TABLE IF NOT EXISTS notification_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                    channel TEXT NOT NULL CHECK (channel IN ('sms', 'call')),
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'cancelled')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    sent_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (incident_id, channel)
                );
                CREATE INDEX IF NOT EXISTS notification_jobs_due
                    ON notification_jobs(status, due_at);
                CREATE TABLE IF NOT EXISTS resources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    available INTEGER NOT NULL CHECK (available >= 0),
                    capacity INTEGER NOT NULL CHECK (capacity >= 0),
                    location TEXT NOT NULL
                );
                """
            )
            incident_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(incidents)").fetchall()
            }
            if "acknowledged_at" not in incident_columns:
                connection.execute("ALTER TABLE incidents ADD COLUMN acknowledged_at TEXT")
            if connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0:
                now = utc_now()
                connection.executemany(
                    """INSERT INTO incidents
                    (code, kind, description, district, severity, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                    [(*incident, now, now) for incident in SEED_INCIDENTS],
                )
            if connection.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0:
                connection.executemany(
                    "INSERT INTO resources(name, category, available, capacity, location) VALUES (?, ?, ?, ?, ?)",
                    SEED_RESOURCES,
                )
            connection.commit()
        finally:
            connection.close()

    def health(self) -> dict[str, Any]:
        with self.session() as connection:
            connection.execute("SELECT 1").fetchone()
        return {
            "ok": True,
            "service": "ReliefGrid",
            "mode": "demo",
            "outbound_alerts_configured": TwilioNotifier.from_environment() is not None,
        }

    def dashboard(self) -> dict[str, int]:
        today = utc_now()[:10]
        with self.session() as connection:
            active = connection.execute("SELECT COUNT(*) FROM incidents WHERE status = 'active'").fetchone()[0]
            critical = connection.execute(
                "SELECT COUNT(*) FROM incidents WHERE status = 'active' AND severity = 'critical'"
            ).fetchone()[0]
            resolved = connection.execute("SELECT COUNT(*) FROM incidents WHERE status = 'resolved'").fetchone()[0]
            today_count = connection.execute(
                "SELECT COUNT(*) FROM incidents WHERE substr(created_at, 1, 10) = ?", (today,)
            ).fetchone()[0]
            units = connection.execute("SELECT COALESCE(SUM(available), 0) FROM resources").fetchone()[0]
        return {
            "active_incidents": active,
            "critical_incidents": critical,
            "resolved_incidents": resolved,
            "reports_today": today_count,
            "available_units": units,
        }

    def list_incidents(self, query: dict[str, list[str]]) -> dict[str, Any]:
        search = query.get("q", [""])[0].strip()
        if len(search) > 120:
            raise ApiError("Search must be 120 characters or fewer.")
        status = query.get("status", ["active"])[0]
        severity = query.get("severity", ["all"])[0]
        if status not in ("all", *STATUSES):
            raise ApiError("Choose a valid status filter.")
        if severity not in ("all", *SEVERITIES):
            raise ApiError("Choose a valid priority filter.")
        try:
            limit = min(max(int(query.get("limit", ["100"])[0]), 1), 200)
            offset = min(max(int(query.get("offset", ["0"])[0]), 0), 10_000)
        except ValueError as exc:
            raise ApiError("Limit and offset must be whole numbers.") from exc

        clauses: list[str] = []
        values: list[Any] = []
        if status != "all":
            clauses.append("status = ?")
            values.append(status)
        if severity != "all":
            clauses.append("severity = ?")
            values.append(severity)
        if search:
            clauses.append("(code LIKE ? OR kind LIKE ? OR description LIKE ? OR district LIKE ?)")
            values.extend([f"%{search}%"] * 4)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.session() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM incidents{where}", values).fetchone()[0]
            rows = connection.execute(
                f"""SELECT id, code, kind, description, district, severity, status,
                           acknowledged_at, created_at, updated_at
                    FROM incidents{where}
                    ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,
                             CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                           WHEN 'medium' THEN 2 ELSE 3 END,
                             updated_at DESC
                    LIMIT ? OFFSET ?""",
                [*values, limit, offset],
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def list_resources(self) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                "SELECT id, name, category, available, capacity, location FROM resources ORDER BY category, name"
            ).fetchall()
        return [dict(row) for row in rows]

    def create_incident(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiError("Report must be a JSON object.")
        kind = clean_text(payload.get("kind"), "Incident type", maximum=80)
        description = clean_text(payload.get("description"), "Description", minimum=8, maximum=2000)
        district = clean_text(payload.get("district"), "District / area", maximum=120)
        severity = payload.get("severity")
        if severity not in SEVERITIES:
            raise ApiError("Choose a valid priority.")
        now = utc_now()
        code = f"RG-{secrets.token_hex(3).upper()}"
        with self.session() as connection:
            cursor = connection.execute(
                """INSERT INTO incidents(code, kind, description, district, severity, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (code, kind, description, district, severity, now, now),
            )
            if severity == "critical":
                enqueue_critical_alerts(connection, cursor.lastrowid, now)
            row = connection.execute("SELECT * FROM incidents WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def update_incident(self, incident_id: int, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or not payload:
            raise ApiError("Provide a status or priority update.")
        if set(payload) - {"status", "severity", "acknowledge"}:
            raise ApiError("Only status, priority, and acknowledgement can be updated.")
        updates: dict[str, str] = {}
        acknowledge = False
        if "status" in payload:
            if payload["status"] not in STATUSES:
                raise ApiError("Status must be active or resolved.")
            updates["status"] = payload["status"]
        if "severity" in payload:
            if payload["severity"] not in SEVERITIES:
                raise ApiError("Choose a valid priority.")
            updates["severity"] = payload["severity"]
        if "acknowledge" in payload:
            if payload["acknowledge"] is not True:
                raise ApiError("Acknowledgement must be true.")
            acknowledge = True
        with self.session() as connection:
            current = connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            if current is None:
                raise ApiError("Incident not found.", 404)
            now = utc_now()
            if acknowledge:
                updates["acknowledged_at"] = current["acknowledged_at"] or now
            updates["updated_at"] = now
            assignments = ", ".join(f"{column} = ?" for column in updates)
            values = [*updates.values(), incident_id]
            cursor = connection.execute(f"UPDATE incidents SET {assignments} WHERE id = ?", values)
            if cursor.rowcount == 0:
                raise ApiError("Incident not found.", 404)
            new_status = updates.get("status", current["status"])
            new_severity = updates.get("severity", current["severity"])
            if acknowledge:
                connection.execute(
                    """UPDATE notification_jobs
                       SET status = 'cancelled', updated_at = ?
                       WHERE incident_id = ? AND channel = 'call' AND status = 'pending'""",
                    (now, incident_id),
                )
            elif new_status != "active" or new_severity != "critical":
                connection.execute(
                    """UPDATE notification_jobs
                       SET status = 'cancelled', updated_at = ?
                       WHERE incident_id = ? AND status = 'pending'""",
                    (now, incident_id),
                )
            elif (
                new_status == "active"
                and new_severity == "critical"
                and (current["status"] != "active" or current["severity"] != "critical")
            ):
                enqueue_critical_alerts(connection, incident_id, now)
            row = connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        return dict(row)

    def dispatch_due_notifications(self, notifier: Any = None, now: datetime | None = None) -> int:
        """Send due SMS/call jobs once; tests can pass a fake notifier and clock."""
        sender = notifier if notifier is not None else TwilioNotifier.from_environment()
        if sender is None:
            return 0
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        now_text = current_time.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        claimed: list[dict[str, Any]] = []
        with self.session() as connection:
            rows = connection.execute(
                """SELECT jobs.id AS job_id, jobs.incident_id, jobs.channel,
                          incidents.code, incidents.kind, incidents.district,
                          incidents.severity, incidents.status AS incident_status,
                          incidents.acknowledged_at
                   FROM notification_jobs AS jobs
                   JOIN incidents ON incidents.id = jobs.incident_id
                   WHERE jobs.status = 'pending' AND datetime(jobs.due_at) <= datetime(?)
                   ORDER BY jobs.due_at, jobs.id
                   LIMIT 50""",
                (now_text,),
            ).fetchall()
            for row in rows:
                if (
                    row["incident_status"] != "active"
                    or row["severity"] != "critical"
                    or (row["acknowledged_at"] is not None and row["channel"] == "call")
                ):
                    connection.execute(
                        """UPDATE notification_jobs SET status = 'cancelled', updated_at = ?
                           WHERE id = ? AND status = 'pending'""",
                        (now_text, row["job_id"]),
                    )
                    continue
                result = connection.execute(
                    """UPDATE notification_jobs
                       SET status = 'sending', attempts = attempts + 1, updated_at = ?
                       WHERE id = ? AND status = 'pending'""",
                    (now_text, row["job_id"]),
                )
                if result.rowcount:
                    claimed.append(dict(row))

        for job in claimed:
            try:
                if job["channel"] == "sms":
                    sender.send_sms(job)
                else:
                    sender.place_call(job)
            except Exception as exc:
                LOG.warning(
                    "Outbound %s alert failed for %s (%s)",
                    job["channel"],
                    job["code"],
                    type(exc).__name__,
                )
                with self.session() as connection:
                    connection.execute(
                        """UPDATE notification_jobs
                           SET status = 'failed', last_error = 'delivery_failed', updated_at = ?
                           WHERE id = ? AND status = 'sending'""",
                        (utc_now(), job["job_id"]),
                    )
            else:
                sent_at = utc_now()
                with self.session() as connection:
                    connection.execute(
                        """UPDATE notification_jobs
                           SET status = 'sent', sent_at = ?, last_error = NULL, updated_at = ?
                           WHERE id = ? AND status = 'sending'""",
                        (sent_at, sent_at, job["job_id"]),
                    )
        return len(claimed)


class ReliefGridHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: ReliefGridApp) -> None:
        self.app = app
        self._notification_stop = threading.Event()
        self._notification_thread: threading.Thread | None = None
        super().__init__(address, ReliefGridRequestHandler)

    def _notification_loop(self) -> None:
        while not self._notification_stop.wait(1):
            try:
                self.app.dispatch_due_notifications()
            except Exception:
                LOG.exception("Notification dispatcher encountered an unexpected error")

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self._notification_stop.clear()
        self._notification_thread = threading.Thread(
            target=self._notification_loop, name="reliefgrid-notifications", daemon=True
        )
        self._notification_thread.start()
        try:
            super().serve_forever(poll_interval)
        finally:
            self._notification_stop.set()
            self._notification_thread.join(timeout=3)


class ReliefGridRequestHandler(BaseHTTPRequestHandler):
    server: ReliefGridHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; script-src 'self'; "
            "style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )

    def _send_bytes(self, data: bytes, status: int, content_type: str) -> None:
        self.send_response(status)
        self._headers(content_type, len(data))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, status, "application/json; charset=utf-8")

    def _read_json(self) -> Any:
        if "application/json" not in self.headers.get("Content-Type", "").lower():
            raise ApiError("Content-Type must be application/json.", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError("Invalid Content-Length header.") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError("Request body is too large.", 413)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError("Request body must contain valid JSON.") from exc

    def _static(self, filename: str, content_type: str) -> None:
        try:
            data = (STATIC / filename).read_bytes()
        except OSError:
            self._send_json({"error": "Static asset not found."}, 404)
            return
        self._send_bytes(data, 200, content_type)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            if path == "/":
                self._static("index.html", "text/html; charset=utf-8")
            elif path == "/static/app.css":
                self._static("app.css", "text/css; charset=utf-8")
            elif path == "/static/app.js":
                self._static("app.js", "text/javascript; charset=utf-8")
            elif path == "/api/health":
                self._send_json(self.server.app.health())
            elif path == "/api/dashboard":
                self._send_json(self.server.app.dashboard())
            elif path == "/api/incidents":
                self._send_json(self.server.app.list_incidents(parse_qs(parsed.query)))
            elif path == "/api/resources":
                self._send_json(self.server.app.list_resources())
            else:
                self._send_json({"error": "Route not found."}, 404)
        except ApiError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except sqlite3.Error:
            LOG.exception("Database read failed")
            self._send_json({"error": "The database could not complete the request."}, 500)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/incidents":
            self._send_json({"error": "Route not found."}, 404)
            return
        try:
            incident = self.server.app.create_incident(self._read_json())
            self._send_json(incident, 201)
        except ApiError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except sqlite3.Error:
            LOG.exception("Incident create failed")
            self._send_json({"error": "The report could not be saved. Please retry."}, 500)

    def do_PATCH(self) -> None:
        match = re.fullmatch(r"/api/incidents/(\d+)", urlsplit(self.path).path)
        if not match:
            self._send_json({"error": "Route not found."}, 404)
            return
        try:
            incident = self.server.app.update_incident(int(match.group(1)), self._read_json())
            self._send_json(incident)
        except ApiError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except sqlite3.Error:
            LOG.exception("Incident update failed")
            self._send_json({"error": "The report could not be updated. Please retry."}, 500)

    def do_OPTIONS(self) -> None:
        self._send_json({"error": "Cross-origin requests are not supported."}, 405)

    def do_DELETE(self) -> None:
        self._send_json({"error": "Method not allowed."}, 405)

    def do_PUT(self) -> None:
        self._send_json({"error": "Method not allowed."}, 405)


def create_server(host: str = HOST, port: int = PORT, database_path: str | Path = DEFAULT_DB) -> ReliefGridHTTPServer:
    return ReliefGridHTTPServer((host, port), ReliefGridApp(database_path))


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    server = create_server()
    LOG.info("ReliefGrid demo listening at http://%s:%s", *server.server_address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
