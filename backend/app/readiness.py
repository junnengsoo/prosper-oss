from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy.engine import make_url

from .config import ROOT_DIR, get_settings


PASS = "PASS"
WARNING = "WARN"
FAILURE = "FAIL"

PYTHON_DEPENDENCIES = {
    "fastapi": "fastapi",
    "httpx": "httpx",
    "pydantic": "pydantic",
    "pydantic-settings": "pydantic_settings",
    "python-multipart": "multipart",
    "sqlalchemy": "sqlalchemy",
}

EXPECTED_SCHEMA: dict[str, set[str]] = {
    "app_config": {"id", "key", "value", "created_at", "updated_at"},
    "contacts": {"id", "chat_jid", "display_name", "phone", "status", "status_reason", "created_at", "updated_at"},
    "conversations": {"id", "contact_id", "source", "status", "current_stage", "matched_property_id", "created_at", "updated_at"},
    "messages": {
        "id",
        "conversation_id",
        "chat_jid",
        "sender_jid",
        "message_id",
        "direction",
        "source",
        "raw_type",
        "text",
        "timestamp_ms",
        "created_at",
        "updated_at",
    },
    "properties": {
        "id",
        "property_id",
        "property_name",
        "status",
        "property_type",
        "bedrooms",
        "bathrooms",
        "asking_rent",
        "available_from",
        "full_address",
        "property_url",
        "propertyguru_listing_id",
        "tenant_facing_caveats",
        "created_at",
        "updated_at",
    },
    "property_media": {"id", "property_id", "media_type", "file_path", "caption", "sort_order", "enabled", "created_at", "updated_at"},
    "property_playbooks": {"id", "property_id", "initial_reply_blocks", "enabled", "created_at", "updated_at"},
    "stage_runs": {"id", "conversation_id", "stage", "input_snapshot", "output_json", "status", "error", "model", "created_at", "updated_at"},
}

PLACEHOLDER_MARKERS = (
    "changeme",
    "change-me",
    "example",
    "placeholder",
    "replace-me",
    "replace-with",
    "your-key",
    "your-secret",
)


@dataclass(frozen=True)
class DoctorOptions:
    strict_runtime: bool = False
    backend_url: str = "http://127.0.0.1:8000"
    dashboard_url: str = "http://127.0.0.1:5173"
    bridge_url: str | None = None
    timeout_seconds: float = 1.0


@dataclass(frozen=True)
class DoctorResult:
    status: str
    name: str
    message: str


def run_doctor(options: DoctorOptions | None = None) -> list[DoctorResult]:
    options = options or DoctorOptions()
    settings = get_settings()
    results: list[DoctorResult] = []

    results.extend(check_dependencies())
    results.extend(check_environment(settings))
    results.extend(check_bridge_token(settings, options.bridge_url))

    database_path = sqlite_database_path(settings.database_url)
    if database_path is None:
        results.append(
            DoctorResult(
                FAILURE,
                "database",
                "DATABASE_URL must point to a file-backed SQLite database for this local Prosper release.",
            )
        )
    elif not database_path.is_file():
        results.append(DoctorResult(FAILURE, "database", "Configured SQLite database file is missing."))
    else:
        results.extend(check_sqlite_database(database_path, settings.media_root, content_required=options.strict_runtime))

    results.extend(check_runtime(options, settings.bridge_base_url))
    return results


def check_dependencies() -> list[DoctorResult]:
    missing = [name for name, module in sorted(PYTHON_DEPENDENCIES.items()) if importlib.util.find_spec(module) is None]
    if missing:
        return [DoctorResult(FAILURE, "dependencies", "Missing Python package dependencies: " + ", ".join(missing) + ".")]
    return [DoctorResult(PASS, "dependencies", "Python package dependencies are importable.")]


def check_environment(settings: Any) -> list[DoctorResult]:
    results: list[DoctorResult] = []
    if settings.auth_required:
        missing = []
        if not settings.access_password.strip():
            missing.append("ACCESS_PASSWORD")
        if not settings.session_secret.strip():
            missing.append("SESSION_SECRET")
        if missing:
            results.append(
                DoctorResult(
                    FAILURE,
                    "environment",
                    "Dashboard authentication is enabled but required secret settings are missing: " + ", ".join(missing) + ".",
                )
            )
        elif secret_looks_placeholder(settings.access_password) or secret_looks_placeholder(settings.session_secret):
            results.append(DoctorResult(FAILURE, "environment", "Dashboard authentication secrets appear to use placeholder values."))
        else:
            results.append(DoctorResult(PASS, "environment", "Dashboard authentication has the required secret settings."))
    else:
        results.append(DoctorResult(WARNING, "environment", "Dashboard authentication is disabled; use only for trusted local access."))

    if settings.deepseek_api_key.strip():
        if secret_looks_placeholder(settings.deepseek_api_key):
            results.append(DoctorResult(FAILURE, "deepseek", "DeepSeek API key appears to use a placeholder value."))
        else:
            results.append(DoctorResult(PASS, "deepseek", "DeepSeek API key is configured."))
    else:
        results.append(
            DoctorResult(
                WARNING,
                "deepseek",
                "DeepSeek API key is not configured; model-backed stages will fall back to Manual Review.",
            )
        )

    if settings.deepseek_base_url.strip() and settings.deepseek_model.strip():
        results.append(DoctorResult(PASS, "deepseek", "DeepSeek base URL and model settings are present."))
    else:
        results.append(DoctorResult(FAILURE, "deepseek", "DeepSeek base URL and model settings must be present."))
    return results


def check_bridge_token(settings: Any, bridge_url_override: str | None) -> list[DoctorResult]:
    bridge_url = bridge_url_override or settings.bridge_base_url
    host = urlparse(bridge_url).hostname or ""
    token = settings.bridge_token.strip()
    if token and secret_looks_placeholder(token):
        return [DoctorResult(FAILURE, "bridge-token", "Bridge token appears to use a placeholder value.")]
    if token:
        return [DoctorResult(PASS, "bridge-token", "Bridge token is configured.")]
    if host_is_loopback(host):
        return [DoctorResult(WARNING, "bridge-token", "Bridge token is not configured; keep the bridge on loopback or set PROSPER_BRIDGE_TOKEN.")]
    return [DoctorResult(FAILURE, "bridge-token", "Bridge token is required when the bridge is not on loopback.")]


def sqlite_database_path(database_url: str) -> Path | None:
    try:
        url = make_url(database_url)
    except Exception:
        return None
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    path = Path(database).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve(strict=False)


def check_sqlite_database(database_path: Path, media_root: Path, *, content_required: bool) -> list[DoctorResult]:
    results: list[DoctorResult] = []
    try:
        with sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if quick_check == "ok":
                results.append(DoctorResult(PASS, "database", "SQLite database exists and is readable."))
            else:
                results.append(DoctorResult(FAILURE, "database", "SQLite integrity check did not pass."))
            results.extend(check_schema(connection))
            if not has_schema_failures(results):
                results.extend(check_listing_playbook_coverage(connection, content_required=content_required))
                results.extend(check_media_references(connection, media_root))
    except sqlite3.Error:
        results.append(DoctorResult(FAILURE, "database", "Configured SQLite database could not be opened read-only."))
    return results


def check_schema(connection: sqlite3.Connection) -> list[DoctorResult]:
    results: list[DoctorResult] = [
        DoctorResult(
            PASS,
            "schema",
            "Prosper currently does not include schema migrations; doctor checks the current table shape directly.",
        )
    ]
    table_names = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    missing_tables = sorted(set(EXPECTED_SCHEMA) - table_names)
    if missing_tables:
        results.append(DoctorResult(FAILURE, "schema", "Missing required tables: " + ", ".join(missing_tables) + "."))
        return results

    missing_columns: list[str] = []
    for table, expected_columns in sorted(EXPECTED_SCHEMA.items()):
        columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        for column in sorted(expected_columns - columns):
            missing_columns.append(f"{table}.{column}")
    if missing_columns:
        results.append(DoctorResult(FAILURE, "schema", "Missing required columns: " + ", ".join(missing_columns) + "."))
    else:
        results.append(DoctorResult(PASS, "schema", "Current required tables and columns are present."))
    return results


def check_listing_playbook_coverage(connection: sqlite3.Connection, *, content_required: bool) -> list[DoctorResult]:
    results: list[DoctorResult] = []
    missing_content_status = FAILURE if content_required else WARNING
    listing_count = int(connection.execute("SELECT count(*) FROM properties").fetchone()[0])
    enabled_playbook_count = int(connection.execute("SELECT count(*) FROM property_playbooks WHERE enabled = 1").fetchone()[0])
    available_missing_playbooks = [
        str(row["property_id"])
        for row in connection.execute(
            """
            SELECT p.property_id
            FROM properties p
            LEFT JOIN property_playbooks pb ON pb.property_id = p.property_id AND pb.enabled = 1
            WHERE p.status = 'available' AND pb.id IS NULL
            ORDER BY p.property_id
            """
        ).fetchall()
    ]
    enabled_orphan_playbooks = int(
        connection.execute(
            """
            SELECT count(*)
            FROM property_playbooks pb
            LEFT JOIN properties p ON p.property_id = pb.property_id
            WHERE pb.enabled = 1 AND p.id IS NULL
            """
        ).fetchone()[0]
    )

    if listing_count:
        results.append(DoctorResult(PASS, "rental-listings", f"{listing_count} Rental Listing row(s) are present."))
    else:
        results.append(
            DoctorResult(
                missing_content_status,
                "rental-listings",
                "At least one Rental Listing is required before live startup.",
            )
        )

    if enabled_playbook_count:
        results.append(DoctorResult(PASS, "playbooks", f"{enabled_playbook_count} enabled Playbook row(s) are present."))
    else:
        results.append(
            DoctorResult(
                missing_content_status,
                "playbooks",
                "At least one enabled Playbook is required before live startup.",
            )
        )

    if available_missing_playbooks:
        results.append(
            DoctorResult(
                missing_content_status,
                "playbooks",
                "Available Rental Listings without enabled Playbooks: " + ", ".join(available_missing_playbooks) + ".",
            )
        )
    else:
        results.append(DoctorResult(PASS, "playbooks", "Available Rental Listings have enabled Playbook coverage."))

    if enabled_orphan_playbooks:
        results.append(DoctorResult(WARNING, "playbooks", f"{enabled_orphan_playbooks} enabled Playbook row(s) do not match a Rental Listing."))
    return results


def check_media_references(connection: sqlite3.Connection, media_root: Path) -> list[DoctorResult]:
    rows = connection.execute(
        "SELECT property_id, file_path, enabled FROM property_media ORDER BY property_id, id"
    ).fetchall()
    if not rows:
        return [DoctorResult(PASS, "media", "No managed media references are configured.")]

    media_root_path = media_root.expanduser().resolve(strict=False)
    enabled_missing = 0
    enabled_outside_root = 0
    enabled_symlink = 0
    disabled_unavailable = 0
    for row in rows:
        raw_path = str(row["file_path"] or "").strip()
        enabled = bool(row["enabled"])
        path = Path(raw_path).expanduser() if raw_path else Path()
        if raw_path and not path.is_absolute():
            path = ROOT_DIR / path
        resolved = path.resolve(strict=False) if raw_path else path
        inside_root = path_inside(resolved, media_root_path)
        exists = raw_path and resolved.is_file()
        is_symlink = raw_path and path.is_symlink()
        if enabled and not exists:
            enabled_missing += 1
        if enabled and raw_path and not inside_root:
            enabled_outside_root += 1
        if enabled and is_symlink:
            enabled_symlink += 1
        if not enabled and (not exists or (raw_path and not inside_root) or is_symlink):
            disabled_unavailable += 1

    results: list[DoctorResult] = []
    failure_parts = []
    if enabled_missing:
        failure_parts.append(f"{enabled_missing} missing")
    if enabled_outside_root:
        failure_parts.append(f"{enabled_outside_root} outside media root")
    if enabled_symlink:
        failure_parts.append(f"{enabled_symlink} symlink")
    if failure_parts:
        results.append(DoctorResult(FAILURE, "media", "Enabled managed media references are not send-safe: " + ", ".join(failure_parts) + "."))
    else:
        results.append(DoctorResult(PASS, "media", "Enabled managed media references point to local files under the media root."))

    if disabled_unavailable:
        results.append(DoctorResult(WARNING, "media", f"{disabled_unavailable} disabled managed media reference(s) are unavailable."))
    return results


def check_runtime(options: DoctorOptions, default_bridge_url: str) -> list[DoctorResult]:
    bridge_url = options.bridge_url or default_bridge_url
    required = options.strict_runtime
    return [
        check_endpoint("backend", options.backend_url, "/health", required, expect_key="app", expect_value="prosper", timeout=options.timeout_seconds),
        check_endpoint("dashboard", options.dashboard_url, "/", required, timeout=options.timeout_seconds),
        check_endpoint("bridge", bridge_url, "/health", required, expect_key="bridge", expect_value="prosper-bridge", timeout=options.timeout_seconds),
    ]


def check_endpoint(
    name: str,
    base_url: str,
    path: str,
    required: bool,
    *,
    expect_key: str | None = None,
    expect_value: str | None = None,
    timeout: float,
) -> DoctorResult:
    url = base_url.rstrip("/") + path
    try:
        request = Request(url, headers={"cache-control": "no-store"})
        with urlopen(request, timeout=timeout) as response:
            status_code = response.status
            content_type = response.headers.get("content-type", "")
            body = response.read(64 * 1024)
    except HTTPError as error:
        status = FAILURE if required else WARNING
        return DoctorResult(status, f"runtime-{name}", f"{name.title()} endpoint returned HTTP {error.code}.")
    except (OSError, URLError, ValueError):
        status = FAILURE if required else WARNING
        return DoctorResult(status, f"runtime-{name}", f"{name.title()} endpoint is not reachable.")

    if status_code >= 500:
        status = FAILURE if required else WARNING
        return DoctorResult(status, f"runtime-{name}", f"{name.title()} endpoint returned HTTP {status_code}.")
    if expect_key:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            status = FAILURE if required else WARNING
            return DoctorResult(status, f"runtime-{name}", f"{name.title()} endpoint did not return expected JSON.")
        if payload.get(expect_key) != expect_value:
            status = FAILURE if required else WARNING
            return DoctorResult(status, f"runtime-{name}", f"{name.title()} endpoint did not identify Prosper correctly.")
    elif name == "dashboard" and "text/html" not in content_type and status_code >= 400:
        status = FAILURE if required else WARNING
        return DoctorResult(status, f"runtime-{name}", f"{name.title()} endpoint did not return a dashboard page.")

    return DoctorResult(PASS, f"runtime-{name}", f"{name.title()} endpoint is reachable.")


def format_results(results: list[DoctorResult]) -> str:
    lines = ["Prosper doctor"]
    lines.extend(f"[{result.status}] {result.name}: {result.message}" for result in results)
    pass_count = sum(1 for result in results if result.status == PASS)
    warning_count = sum(1 for result in results if result.status == WARNING)
    failure_count = sum(1 for result in results if result.status == FAILURE)
    lines.append(f"Summary: {pass_count} passed, {warning_count} warning(s), {failure_count} failure(s).")
    return "\n".join(lines)


def exit_code(results: list[DoctorResult]) -> int:
    return 1 if any(result.status == FAILURE for result in results) else 0


def has_schema_failures(results: list[DoctorResult]) -> bool:
    return any(result.status == FAILURE and result.name == "schema" for result in results)


def host_is_loopback(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"localhost", "::1", "0:0:0:0:0:0:0:1"} or normalized.startswith("127.")


def path_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def secret_looks_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return bool(lowered) and any(marker in lowered for marker in PLACEHOLDER_MARKERS)
