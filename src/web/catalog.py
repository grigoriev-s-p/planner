"""Persistent insert-only working catalog and entity lifecycle repository."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from numbers import Real
from typing import Any, Iterable, Iterator, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DB = PROJECT_ROOT / "data" / "catalog" / "catalog.sqlite3"

ENTITY_TABLES = {
    "task": "tasks",
    "engineer": "engineers",
    "team": "team_history",
}

ARCHIVE_REASON_LABELS: dict[str, dict[str, str]] = {
    "engineer": {
        "EMPLOYEE_LEFT": "Сотрудник уволился",
        "TRANSFERRED": "Перешёл в другую команду / подразделение",
        "CREATED_BY_MISTAKE": "Данные добавлены ошибочно",
        "DUPLICATE": "Дубликат профиля",
        "NO_LONGER_PLANNED": "Больше не участвует в планировании",
        "OTHER": "Другая причина",
    },
    "team": {
        "TEAM_DISBANDED": "Команда расформирована",
        "MERGED": "Команда объединена с другой",
        "REORGANIZATION": "Реорганизация",
        "CREATED_BY_MISTAKE": "Данные добавлены ошибочно",
        "DUPLICATE": "Дубликат команды",
        "NO_LONGER_USED": "Команда больше не используется",
        "OTHER": "Другая причина",
    },
    "task": {
        "TASK_CANCELLED": "Задача отменена",
        "NO_LONGER_RELEVANT": "Задача больше не актуальна",
        "REPLACED": "Задача заменена другой",
        "CREATED_BY_MISTAKE": "Данные добавлены ошибочно",
        "DUPLICATE": "Дубликат задачи",
        "MOVED_OUT_OF_CATALOG": "Перенесена за пределы используемого каталога",
        "OTHER": "Другая причина",
    },
}


class CatalogError(RuntimeError):
    """Base catalog operation failure."""


class CatalogNotFoundError(CatalogError):
    """Requested catalog entity does not exist."""


class CatalogConflictError(CatalogError):
    """A catalog mutation conflicts with an existing entity or membership."""


class ArchiveValidationError(CatalogError):
    """Archive request has an unsupported or incomplete reason."""


class ArchiveConflictError(CatalogError):
    """Entity cannot be archived while active references exist."""


@dataclass(frozen=True)
class CatalogMergeResult:
    added: dict[str, int]
    kept: dict[str, int]
    warnings: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...] = ()
    archived_entities: tuple[dict[str, Any], ...] = ()
    restored: dict[str, int] | None = None
    restored_updated: dict[str, int] | None = None
    kept_archived: dict[str, int] | None = None
    updated: dict[str, int] | None = None
    summary: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": dict(self.added),
            "kept": dict(self.kept),
            "warnings": list(self.warnings),
            "conflicts": [dict(conflict) for conflict in self.conflicts],
            "archived_entities": [dict(entity) for entity in self.archived_entities],
            "restored": dict(self.restored or {}),
            "restored_updated": dict(self.restored_updated or {}),
            "kept_archived": dict(self.kept_archived or {}),
            "updated": dict(self.updated or {}),
            "summary": dict(self.summary or {}),
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"true", "1", "yes"}


def _json_payload(row: Mapping[str, Any]) -> str:
    return json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)


def _comparison_payload(row: Mapping[str, Any]) -> str:
    """Canonicalize harmless JSON numeric differences for conflict detection."""

    def normalize(value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, Real):
            number = float(value)
            return None if math.isnan(number) else number
        if isinstance(value, Mapping):
            return {str(key): normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    return json.dumps(normalize(dict(row)), ensure_ascii=False, sort_keys=True, default=str)


def _row_identity(table_name: str, row: Mapping[str, Any]) -> str:
    fields = {
        "tasks": ("task_id",),
        "task_role_effort": ("task_id", "role"),
        "task_dependencies": (
            "blocking_task_id",
            "blocked_task_id",
            "dependency_type",
        ),
        "engineers": ("engineer_id", "team_id", "role"),
        "engineer_skills": ("engineer_id", "technology"),
        "team_history": ("team_id", "snapshot_date", "velocity_achieved", "planned_sp"),
        "task_skill_requirements": ("task_id", "role", "technology"),
        "planning_results": ("value",),
        "plan_mismatch_reasons": ("value",),
        "task_closure_results": ("value",),
    }.get(table_name)
    if fields:
        identity = "\x1f".join(_text(row.get(field)) for field in fields)
        if identity.strip("\x1f"):
            return identity
    return _json_payload(row)


class CatalogRepository:
    """SQLite repository seeded once from processed normalized tables."""

    def __init__(
        self,
        database_path: Path,
        *,
        seed_tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    ):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if seed_tables:
            self._seed_if_empty(seed_tables)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_rows (
                    table_name TEXT NOT NULL,
                    row_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (table_name, row_key)
                );
                CREATE TABLE IF NOT EXISTS entity_lifecycle (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    archived_at TEXT,
                    archive_reason_code TEXT,
                    archive_reason_text TEXT,
                    PRIMARY KEY (entity_type, entity_id)
                );
                CREATE TABLE IF NOT EXISTS catalog_row_overrides (
                    table_name TEXT NOT NULL,
                    row_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (table_name, row_key),
                    FOREIGN KEY (table_name, row_key)
                        REFERENCES catalog_rows(table_name, row_key)
                );
                CREATE TABLE IF NOT EXISTS plan_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS current_plan_snapshot (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    snapshot_id INTEGER NOT NULL,
                    FOREIGN KEY (snapshot_id) REFERENCES plan_snapshots(snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS replan_workflows (
                    workflow_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS engineer_availability (
                    engineer_id TEXT NOT NULL,
                    sprint INTEGER NOT NULL CHECK (sprint BETWEEN 1 AND 6),
                    availability_rate REAL NOT NULL CHECK (availability_rate IN (0, 0.5, 1)),
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (engineer_id, sprint, source_type, source_id)
                );
                CREATE TABLE IF NOT EXISTS catalog_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def reset_all_data(self) -> dict[str, int]:
        """Remove every mutable catalog and planning record atomically.

        The bootstrap marker is deliberately retained.  Without it, the next
        process start would interpret the empty catalog as a fresh install and
        silently restore the processed seed dataset.
        """

        tables = (
            "current_plan_snapshot",
            "replan_workflows",
            "plan_snapshots",
            "engineer_availability",
            "catalog_row_overrides",
            "entity_lifecycle",
            "catalog_rows",
        )
        with self._connect() as connection:
            deleted = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }
            for table in tables:
                connection.execute(f"DELETE FROM {table}")
            connection.execute(
                "DELETE FROM sqlite_sequence WHERE name='plan_snapshots'"
            )
            connection.execute(
                "INSERT INTO catalog_metadata(key,value) VALUES('bootstrap_state','reset') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
        return deleted

    def availability_constraints(self) -> list[dict[str, Any]]:
        """Load all capacity constraints once for an in-memory planning run."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT engineer_id,sprint,availability_rate,source_type,source_id,"
                "reason,created_at,updated_at FROM engineer_availability "
                "ORDER BY sprint,engineer_id,source_type,source_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_availability_constraints(
        self, rows: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Upsert future constraints atomically; rate 1 resets a source override."""

        from src.planning.availability import validate_availability_rate

        prepared = []
        now = datetime.now(timezone.utc).isoformat()
        for source in rows:
            engineer_id = _text(source.get("engineer_id"))
            sprint = int(source.get("sprint") or 0)
            rate = validate_availability_rate(source.get("availability_rate"))
            source_type = _text(source.get("source_type")) or "MANUAL"
            source_id = _text(source.get("source_id")) or "manual"
            if not engineer_id or not 1 <= sprint <= 6:
                raise ValueError("engineer_id and sprint 1..6 are required")
            prepared.append((engineer_id, sprint, rate, source_type, source_id,
                             _text(source.get("reason")) or None))
        with self._connect() as connection:
            for engineer_id, sprint, rate, source_type, source_id, reason in prepared:
                if rate == 1.0:
                    connection.execute(
                        "DELETE FROM engineer_availability WHERE engineer_id=? AND sprint=? "
                        "AND source_type=? AND source_id=?",
                        (engineer_id, sprint, source_type, source_id),
                    )
                    continue
                connection.execute(
                    "INSERT INTO engineer_availability(engineer_id,sprint,availability_rate,"
                    "source_type,source_id,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(engineer_id,sprint,source_type,source_id) DO UPDATE SET "
                    "availability_rate=excluded.availability_rate,reason=excluded.reason,"
                    "updated_at=excluded.updated_at",
                    (engineer_id, sprint, rate, source_type, source_id, reason, now, now),
                )
        return self.availability_constraints()

    def save_approved_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Append an immutable approved snapshot and make it current."""

        saved = deepcopy(dict(payload))
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM plan_snapshots"
            ).fetchone()[0]
            revision = int(previous) + 1
            cursor = connection.execute(
                "INSERT INTO plan_snapshots(revision,payload,created_at) VALUES(?,?,?)",
                (revision, _json_payload(saved), now),
            )
            snapshot_id = int(cursor.lastrowid)
            saved["approved_snapshot"] = {
                "snapshot_id": snapshot_id,
                "revision": revision,
                "created_at": now,
            }
            connection.execute(
                "UPDATE plan_snapshots SET payload=? WHERE snapshot_id=?",
                (_json_payload(saved), snapshot_id),
            )
            connection.execute(
                "INSERT INTO current_plan_snapshot(singleton,snapshot_id) VALUES(1,?) "
                "ON CONFLICT(singleton) DO UPDATE SET snapshot_id=excluded.snapshot_id",
                (snapshot_id,),
            )
        return saved

    def load_approved_plan(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshots.payload FROM current_plan_snapshot current "
                "JOIN plan_snapshots snapshots ON snapshots.snapshot_id=current.snapshot_id "
                "WHERE current.singleton=1"
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def approved_plan_history(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM plan_snapshots ORDER BY revision"
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def save_replan_workflow(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Persist one sprint-close/replan lifecycle independently of Approved Plan."""

        saved = deepcopy(dict(payload))
        workflow_id = _text(saved.get("workflow_id"))
        if not workflow_id:
            raise ValueError("workflow_id is required")
        now = datetime.now(timezone.utc).isoformat()
        saved["updated_at"] = now
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO replan_workflows(workflow_id,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET "
                "payload=excluded.payload,updated_at=excluded.updated_at",
                (workflow_id, _json_payload(saved), now),
            )
        return saved

    def load_replan_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM replan_workflows WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def latest_replan_workflow(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM replan_workflows ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def _seed_if_empty(
        self, tables: Mapping[str, Iterable[Mapping[str, Any]]]
    ) -> None:
        with self._connect() as connection:
            bootstrap = connection.execute(
                "SELECT value FROM catalog_metadata WHERE key='bootstrap_state'"
            ).fetchone()
            if bootstrap:
                return
            count = connection.execute("SELECT COUNT(*) FROM catalog_rows").fetchone()[0]
            if count:
                connection.execute(
                    "INSERT INTO catalog_metadata(key,value) VALUES('bootstrap_state','seeded')"
                )
                return
            now = datetime.now(timezone.utc).isoformat()
            for table_name, rows in tables.items():
                for index, source in enumerate(rows):
                    row = dict(source)
                    base_key = _row_identity(table_name, row)
                    row_key = base_key
                    suffix = 1
                    while connection.execute(
                        "SELECT 1 FROM catalog_rows WHERE table_name=? AND row_key=?",
                        (table_name, row_key),
                    ).fetchone():
                        suffix += 1
                        row_key = f"{base_key}\x1e{suffix}"
                    connection.execute(
                        "INSERT INTO catalog_rows(table_name,row_key,payload,created_at) "
                        "VALUES(?,?,?,?)",
                        (table_name, row_key, _json_payload(row), now),
                    )
            connection.execute(
                "INSERT INTO catalog_metadata(key,value) VALUES('bootstrap_state','seeded')"
            )

    def tables(self) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source.table_name,COALESCE(changes.payload,source.payload) payload "
                "FROM catalog_rows source LEFT JOIN catalog_row_overrides changes "
                "ON changes.table_name=source.table_name AND changes.row_key=source.row_key "
                "ORDER BY source.table_name,source.row_key"
            ).fetchall()
        for row in rows:
            result.setdefault(row["table_name"], []).append(json.loads(row["payload"]))
        for required in (
            "tasks",
            "task_role_effort",
            "task_dependencies",
            "engineers",
            "engineer_skills",
            "team_history",
            "task_skill_requirements",
        ):
            result.setdefault(required, [])
        return result

    @staticmethod
    def _connection_rows(
        connection: sqlite3.Connection, table_name: str
    ) -> list[tuple[str, dict[str, Any]]]:
        rows = connection.execute(
            "SELECT source.row_key,COALESCE(changes.payload,source.payload) payload "
            "FROM catalog_rows source LEFT JOIN catalog_row_overrides changes "
            "ON changes.table_name=source.table_name AND changes.row_key=source.row_key "
            "WHERE source.table_name=? ORDER BY source.row_key",
            (table_name,),
        ).fetchall()
        return [(row["row_key"], json.loads(row["payload"])) for row in rows]

    @staticmethod
    def _insert_row(
        connection: sqlite3.Connection,
        table_name: str,
        row: Mapping[str, Any],
        now: str,
    ) -> None:
        payload = dict(row)
        base_key = _row_identity(table_name, payload)
        row_key = base_key
        suffix = 1
        while connection.execute(
            "SELECT 1 FROM catalog_rows WHERE table_name=? AND row_key=?",
            (table_name, row_key),
        ).fetchone():
            suffix += 1
            row_key = f"{base_key}\x1e{suffix}"
        connection.execute(
            "INSERT INTO catalog_rows(table_name,row_key,payload,created_at) VALUES(?,?,?,?)",
            (table_name, row_key, _json_payload(payload), now),
        )

    @staticmethod
    def _delete_rows(
        connection: sqlite3.Connection, table_name: str, predicate
    ) -> int:
        rows = CatalogRepository._connection_rows(connection, table_name)
        keys = [row_key for row_key, payload in rows if predicate(payload)]
        for row_key in keys:
            connection.execute(
                "DELETE FROM catalog_row_overrides WHERE table_name=? AND row_key=?",
                (table_name, row_key),
            )
            connection.execute(
                "DELETE FROM catalog_rows WHERE table_name=? AND row_key=?",
                (table_name, row_key),
            )
        return len(keys)

    @staticmethod
    def _bump_revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM catalog_metadata WHERE key='catalog_revision'"
        ).fetchone()
        revision = int(row[0]) + 1 if row else 1
        connection.execute(
            "INSERT INTO catalog_metadata(key,value) VALUES('catalog_revision',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(revision),),
        )
        return revision

    def revision(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM catalog_metadata WHERE key='catalog_revision'"
            ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _active_entity_in_connection(
        connection: sqlite3.Connection, entity_type: str, entity_id: str
    ) -> bool:
        table_name = ENTITY_TABLES[entity_type]
        field = {"task": "task_id", "engineer": "engineer_id", "team": "team_id"}[entity_type]
        exists = any(
            _text(payload.get(field)) == entity_id
            for _, payload in CatalogRepository._connection_rows(connection, table_name)
        )
        if entity_type == "team" and not exists:
            exists = any(
                _text(payload.get("team_id")) == entity_id
                for name in ("engineers", "tasks")
                for _, payload in CatalogRepository._connection_rows(connection, name)
            )
        lifecycle = connection.execute(
            "SELECT archived FROM entity_lifecycle WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id),
        ).fetchone()
        return exists and not (lifecycle and bool(lifecycle[0]))

    @staticmethod
    def _canonical_role_in_connection(
        connection: sqlite3.Connection, candidate: str
    ) -> str:
        """Reuse an exact normalized catalog spelling for case/space variants."""

        from src.data.normalization import normalize_role_name

        normalized = normalize_role_name(candidate) or ""
        existing = {
            (normalize_role_name(payload.get("role")) or "").casefold():
                normalize_role_name(payload.get("role"))
            for table_name in ("engineers", "task_role_effort")
            for _, payload in CatalogRepository._connection_rows(connection, table_name)
            if normalize_role_name(payload.get("role"))
        }
        return existing.get(normalized.casefold()) or normalized

    def create_team(
        self,
        *,
        team_id: str,
        name: str | None = None,
        engineer_ids: Iterable[str] = (),
        capacity_sp: float | None = None,
    ) -> dict[str, Any]:
        """Create a team, optional memberships and initial manual capacity."""

        team_id = _text(team_id)
        if not team_id:
            raise CatalogConflictError("ID команды не может быть пустым.")
        selected = list(dict.fromkeys(_text(value) for value in engineer_ids if _text(value)))
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._active_entity_in_connection(connection, "team", team_id):
                raise CatalogConflictError(f"Команда с ID {team_id} уже существует.")
            archived = connection.execute(
                "SELECT archived FROM entity_lifecycle WHERE entity_type='team' AND entity_id=?",
                (team_id,),
            ).fetchone()
            if archived and bool(archived[0]):
                raise CatalogConflictError(
                    f"Команда {team_id} находится в архиве. Используйте восстановление."
                )
            engineer_rows = self._connection_rows(connection, "engineers")
            by_engineer: dict[str, list[tuple[str, dict[str, Any]]]] = {}
            for row_key, payload in engineer_rows:
                by_engineer.setdefault(_text(payload.get("engineer_id")), []).append((row_key, payload))
            missing = [
                value for value in selected
                if value not in by_engineer
                or not self._active_entity_in_connection(connection, "engineer", value)
            ]
            if missing:
                raise CatalogNotFoundError("Неизвестные инженеры: " + ", ".join(missing))
            history_row: dict[str, Any] = {
                "team_id": team_id,
                "team_name": _text(name) or team_id,
            }
            if capacity_sp is not None:
                try:
                    numeric_capacity = float(capacity_sp)
                except (TypeError, ValueError) as error:
                    raise CatalogConflictError("Capacity команды должна быть числом.") from error
                if not math.isfinite(numeric_capacity) or numeric_capacity < 0:
                    raise CatalogConflictError("Capacity команды не может быть отрицательной.")
                history_row["capacity_sp"] = numeric_capacity
            self._insert_row(connection, "team_history", history_row, now)
            for engineer_id in selected:
                rows = by_engineer[engineer_id]
                total = sum(float(payload.get("capacity_rate") or 0) for _, payload in rows)
                free_row = next(
                    ((row_key, payload) for row_key, payload in rows if not _text(payload.get("team_id"))),
                    None,
                )
                available = max(1.0 - total, 0.0)
                if free_row is not None:
                    row_key, payload = free_row
                    replacement = {**payload, "team_id": team_id}
                    connection.execute(
                        "INSERT INTO catalog_row_overrides(table_name,row_key,payload,updated_at) "
                        "VALUES('engineers',?,?,?) ON CONFLICT(table_name,row_key) DO UPDATE SET "
                        "payload=excluded.payload,updated_at=excluded.updated_at",
                        (row_key, _json_payload(replacement), now),
                    )
                elif available > 1e-9:
                    template = rows[0][1]
                    self._insert_row(
                        connection,
                        "engineers",
                        {**template, "team_id": team_id, "capacity_rate": available},
                        now,
                    )
                else:
                    raise CatalogConflictError(
                        f"У инженера {engineer_id} нет свободной доли участия."
                    )
            revision = self._bump_revision(connection)
        return {
            "team_id": team_id,
            "name": _text(name) or team_id,
            "engineer_ids": selected,
            "capacity_sp": capacity_sp,
            "catalog_revision": revision,
        }

    def create_engineer(
        self,
        *,
        engineer_id: str,
        name: str | None,
        role: str,
        grade: str | None,
        capacity_rate: float,
        team_id: str | None,
        skills: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Persist an engineer profile, optional membership and canonical skills."""

        from src.data.normalization import normalize_role_name
        from src.skills import canonicalize_skill

        engineer_id = _text(engineer_id)
        role_raw = _text(role)
        canonical_role = normalize_role_name(role_raw)
        team_id = _text(team_id) or None
        if not engineer_id or not canonical_role:
            raise CatalogConflictError("Укажите ID инженера и роль.")
        if not 0 < float(capacity_rate) <= 1:
            raise CatalogConflictError("Доступность инженера должна быть от 0 до 1.")
        normalized_skills = sorted({
            value for item in skills if (value := canonicalize_skill(item))
        }, key=str.casefold)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._active_entity_in_connection(connection, "engineer", engineer_id):
                raise CatalogConflictError(f"Инженер с ID {engineer_id} уже существует.")
            archived = connection.execute(
                "SELECT archived FROM entity_lifecycle WHERE entity_type='engineer' AND entity_id=?",
                (engineer_id,),
            ).fetchone()
            if archived and bool(archived[0]):
                raise CatalogConflictError(
                    f"Инженер {engineer_id} находится в архиве. Используйте восстановление."
                )
            if team_id and not self._active_entity_in_connection(connection, "team", team_id):
                raise CatalogNotFoundError(f"Неизвестная команда: {team_id}")
            canonical_role = self._canonical_role_in_connection(connection, role_raw)
            self._insert_row(connection, "engineers", {
                "engineer_id": engineer_id,
                "name": _text(name) or engineer_id,
                "team_id": team_id,
                "role_raw": role_raw,
                "role": canonical_role,
                "grade": _text(grade) or None,
                "capacity_rate": float(capacity_rate),
            }, now)
            for technology in normalized_skills:
                self._insert_row(connection, "engineer_skills", {
                    "engineer_id": engineer_id, "technology": technology,
                }, now)
            revision = self._bump_revision(connection)
        return {
            "engineer_id": engineer_id,
            "name": _text(name) or engineer_id,
            "role": canonical_role,
            "team_id": team_id,
            "grade": _text(grade) or None,
            "capacity_rate": float(capacity_rate),
            "skills": normalized_skills,
            "catalog_revision": revision,
        }

    def add_engineer_membership(
        self,
        *,
        engineer_id: str,
        team_id: str,
        capacity_rate: float,
        role: str | None = None,
        grade: str | None = None,
    ) -> dict[str, Any]:
        """Attach an existing engineer without exceeding one total FTE."""

        from src.data.normalization import normalize_role_name

        engineer_id, team_id = _text(engineer_id), _text(team_id)
        rate = float(capacity_rate)
        if not engineer_id or not team_id or not 0 < rate <= 1:
            raise CatalogConflictError("Укажите инженера, команду и долю участия от 0 до 1.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._active_entity_in_connection(connection, "engineer", engineer_id):
                raise CatalogNotFoundError(f"Неизвестный инженер: {engineer_id}")
            if not self._active_entity_in_connection(connection, "team", team_id):
                raise CatalogNotFoundError(f"Неизвестная команда: {team_id}")
            rows = [
                (key, payload) for key, payload in self._connection_rows(connection, "engineers")
                if _text(payload.get("engineer_id")) == engineer_id
            ]
            if any(_text(payload.get("team_id")) == team_id for _, payload in rows):
                raise CatalogConflictError(f"Инженер {engineer_id} уже состоит в команде {team_id}.")
            membership_total = sum(
                float(payload.get("capacity_rate") or 0)
                for _, payload in rows if _text(payload.get("team_id"))
            )
            free_row = next(((key, payload) for key, payload in rows if not _text(payload.get("team_id"))), None)
            available = (
                float(free_row[1].get("capacity_rate") or 0)
                if free_row else max(1.0 - membership_total, 0.0)
            )
            if rate > available + 1e-9 or membership_total + rate > 1 + 1e-9:
                raise CatalogConflictError(
                    f"У инженера {engineer_id} доступно только {available:g} FTE."
                )
            template = free_row[1] if free_row else rows[0][1]
            canonical_role = self._canonical_role_in_connection(connection, role or template.get("role"))
            if not canonical_role:
                raise CatalogConflictError("Для membership требуется роль инженера.")
            membership = {
                **template,
                "team_id": team_id,
                "role_raw": _text(role) or template.get("role_raw") or canonical_role,
                "role": canonical_role,
                "grade": _text(grade) or template.get("grade") or None,
                "capacity_rate": rate,
            }
            if free_row and abs(rate - available) <= 1e-9:
                row_key, _ = free_row
                connection.execute(
                    "INSERT INTO catalog_row_overrides(table_name,row_key,payload,updated_at) "
                    "VALUES('engineers',?,?,?) ON CONFLICT(table_name,row_key) DO UPDATE SET "
                    "payload=excluded.payload,updated_at=excluded.updated_at",
                    (row_key, _json_payload(membership), now),
                )
            else:
                if free_row:
                    row_key, payload = free_row
                    remainder = {**payload, "capacity_rate": available - rate}
                    connection.execute(
                        "INSERT INTO catalog_row_overrides(table_name,row_key,payload,updated_at) "
                        "VALUES('engineers',?,?,?) ON CONFLICT(table_name,row_key) DO UPDATE SET "
                        "payload=excluded.payload,updated_at=excluded.updated_at",
                        (row_key, _json_payload(remainder), now),
                    )
                self._insert_row(connection, "engineers", membership, now)
            revision = self._bump_revision(connection)
        return {"engineer_id": engineer_id, "team_id": team_id, "capacity_rate": rate, "catalog_revision": revision}

    def create_task(
        self,
        *,
        task: Mapping[str, Any],
        role_requirements: Iterable[Mapping[str, Any]],
        skill_requirements: Iterable[Mapping[str, Any]] = (),
        depends_on: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Create a task and every requirement/dependency atomically."""

        from src.data.normalization import normalize_role_name
        from src.skills import canonicalize_skill

        task_id = _text(task.get("task_id"))
        if not task_id:
            raise CatalogConflictError("ID задачи не может быть пустым.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._active_entity_in_connection(connection, "task", task_id):
                raise CatalogConflictError(f"Задача с ID {task_id} уже существует.")
            archived = connection.execute(
                "SELECT archived FROM entity_lifecycle WHERE entity_type='task' AND entity_id=?",
                (task_id,),
            ).fetchone()
            if archived and bool(archived[0]):
                raise CatalogConflictError(
                    f"Задача {task_id} находится в архиве. Используйте восстановление."
                )
            team_id = _text(task.get("team_id")) or None
            if team_id and not self._active_entity_in_connection(connection, "team", team_id):
                raise CatalogNotFoundError(f"Неизвестная команда: {team_id}")
            row = dict(task)
            row["task_id"] = task_id
            row["summary"] = _text(row.get("summary") or row.get("title"))
            row.pop("title", None)
            self._insert_row(connection, "tasks", row, now)
            for source in role_requirements:
                role_raw = _text(source.get("role"))
                role = self._canonical_role_in_connection(connection, role_raw)
                hours = float(source.get("required_hours") or 0)
                if not role or hours <= 0:
                    raise CatalogConflictError("Для задачи укажите роль и положительные трудозатраты.")
                self._insert_row(connection, "task_role_effort", {
                    "task_id": task_id, "role_raw": role_raw, "role": role,
                    "required_hours": hours,
                }, now)
            for source in skill_requirements:
                technology = canonicalize_skill(source.get("technology"))
                if not technology:
                    continue
                payload = dict(source)
                payload.update({"task_id": task_id, "technology": technology})
                self._insert_row(connection, "task_skill_requirements", payload, now)
            for blocker in dict.fromkeys(_text(value) for value in depends_on if _text(value)):
                if blocker == task_id:
                    raise CatalogConflictError("Задача не может зависеть сама от себя.")
                if not self._active_entity_in_connection(connection, "task", blocker):
                    raise CatalogNotFoundError(f"Неизвестная блокирующая задача: {blocker}")
                self._insert_row(connection, "task_dependencies", {
                    "blocking_task_id": blocker,
                    "blocked_task_id": task_id,
                    "dependency_type": "FinishToStart",
                }, now)
            revision = self._bump_revision(connection)
        return {"task_id": task_id, "catalog_revision": revision}

    @staticmethod
    def _write_lifecycle(
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        reason_code: str,
        reason_text: str | None,
        archived_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO entity_lifecycle(
                entity_type,entity_id,archived,archived_at,
                archive_reason_code,archive_reason_text
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(entity_type,entity_id) DO UPDATE SET
                archived=excluded.archived,
                archived_at=excluded.archived_at,
                archive_reason_code=excluded.archive_reason_code,
                archive_reason_text=excluded.archive_reason_text
            """,
            (entity_type, entity_id, 1, archived_at, reason_code, reason_text),
        )

    @staticmethod
    def _override_rows(
        connection: sqlite3.Connection,
        table_name: str,
        predicate,
        transform,
        updated_at: str,
    ) -> int:
        rows = connection.execute(
            "SELECT source.row_key,COALESCE(changes.payload,source.payload) payload "
            "FROM catalog_rows source LEFT JOIN catalog_row_overrides changes "
            "ON changes.table_name=source.table_name AND changes.row_key=source.row_key "
            "WHERE source.table_name=? ORDER BY source.row_key",
            (table_name,),
        ).fetchall()
        changed = 0
        for source in rows:
            payload = json.loads(source["payload"])
            if not predicate(payload):
                continue
            replacement = transform(dict(payload))
            connection.execute(
                "INSERT INTO catalog_row_overrides(table_name,row_key,payload,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(table_name,row_key) DO UPDATE SET "
                "payload=excluded.payload,updated_at=excluded.updated_at",
                (table_name, source["row_key"], _json_payload(replacement), updated_at),
            )
            changed += 1
        return changed

    @staticmethod
    def _validate_reason(
        entity_type: str, reason_code: str, reason_text: str | None
    ) -> tuple[str, str | None]:
        if entity_type not in ENTITY_TABLES:
            raise ArchiveValidationError(f"Unsupported entity type: {entity_type}")
        normalized_code = _text(reason_code).upper()
        normalized_text = _text(reason_text) or None
        if normalized_code not in ARCHIVE_REASON_LABELS[entity_type]:
            raise ArchiveValidationError("Выберите допустимую причину удаления.")
        if normalized_code == "OTHER" and not normalized_text:
            raise ArchiveValidationError("Для другой причины укажите пояснение.")
        return normalized_code, normalized_text

    def deletion_impact(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        tables = self.tables()
        if entity_type not in ENTITY_TABLES:
            raise ArchiveValidationError(f"Unsupported entity type: {entity_type}")
        if not self._entity_exists(tables, entity_type, entity_id):
            raise CatalogNotFoundError(f"Unknown {entity_type}_id: {entity_id}")
        archived_tasks = self.archived_ids("task")
        archived_engineers = self.archived_ids("engineer")
        if entity_type == "team":
            engineer_ids = sorted({
                _text(row.get("engineer_id"))
                for row in tables["engineers"]
                if _text(row.get("team_id")) == entity_id
                and _text(row.get("engineer_id")) not in archived_engineers
            })
            task_ids = sorted({
                _text(row.get("task_id"))
                for row in tables["tasks"]
                if _text(row.get("team_id")) == entity_id
                and _text(row.get("task_id")) not in archived_tasks
                and _text(row.get("status")).casefold() != "done"
            })
            removed = set(task_ids)
            tasks_by_id = {
                _text(row.get("task_id")): row for row in tables["tasks"]
            }
            external_blocked = sorted({
                blocked_id
                for row in tables["task_dependencies"]
                if _text(row.get("blocking_task_id")) in removed
                and (blocked_id := _text(row.get("blocked_task_id"))) not in removed
                and blocked_id not in archived_tasks
                and _text(tasks_by_id.get(blocked_id, {}).get("status")).casefold() != "done"
            })
            return {
                "engineer_ids": engineer_ids,
                "task_ids": task_ids,
                "external_blocked_task_ids": external_blocked,
            }
        if entity_type == "task":
            task_row = next(
                row for row in tables["tasks"]
                if _text(row.get("task_id")) == entity_id
            )
            blocked = [] if _text(task_row.get("status")).casefold() == "done" else sorted({
                    _text(row.get("blocked_task_id"))
                    for row in tables["task_dependencies"]
                    if _text(row.get("blocking_task_id")) == entity_id
                    and _text(row.get("blocked_task_id")) not in archived_tasks
                })
            return {"task_ids": [entity_id], "blocked_task_ids": blocked}
        return {"engineer_ids": [entity_id]}

    def archive_with_relations(
        self,
        entity_type: str,
        entity_id: str,
        reason_code: str,
        reason_text: str | None = None,
        *,
        team_engineer_action: str | None = None,
        team_task_action: str | None = None,
    ) -> dict[str, Any]:
        """Atomically soft-delete an entity and resolve direct team relations."""

        reason_code, reason_text = self._validate_reason(
            entity_type, reason_code, reason_text
        )
        tables = self.tables()
        if not self._entity_exists(tables, entity_type, entity_id):
            raise CatalogNotFoundError(f"Unknown {entity_type}_id: {entity_id}")
        impact = self.deletion_impact(entity_type, entity_id)
        affected_engineers: list[str] = []
        affected_tasks: list[str] = []

        if entity_type == "task":
            active_blocked = impact.get("blocked_task_ids", [])
            task_row = next(
                row for row in tables["tasks"] if _text(row.get("task_id")) == entity_id
            )
            if active_blocked and _text(task_row.get("status")).casefold() != "done":
                raise ArchiveConflictError(
                    "Задача является активным blocker для: "
                    + ", ".join(active_blocked)
                    + ". Сначала измените зависимости."
                )
            affected_tasks = [entity_id]

        if entity_type == "engineer":
            affected_engineers = [entity_id]

        if entity_type == "team":
            affected_engineers = impact["engineer_ids"]
            affected_tasks = impact["task_ids"]
            if affected_engineers and team_engineer_action not in {"UNASSIGN", "DEACTIVATE"}:
                raise ArchiveValidationError(
                    "Выберите, оставить сотрудников без команды или сделать неактивными."
                )
            if affected_tasks and team_task_action not in {"REMOVE", "REDISTRIBUTE"}:
                raise ArchiveValidationError(
                    "Выберите, убрать задачи из плана или перераспределить."
                )
            if team_task_action == "REMOVE":
                removed = set(affected_tasks)
                archived_tasks = self.archived_ids("task")
                tasks_by_id = {
                    _text(row.get("task_id")): row for row in tables["tasks"]
                }
                external_blocked = sorted({
                    blocked_id
                    for row in tables["task_dependencies"]
                    if _text(row.get("blocking_task_id")) in removed
                    and (blocked_id := _text(row.get("blocked_task_id"))) not in removed
                    and blocked_id not in archived_tasks
                    and _text(tasks_by_id.get(blocked_id, {}).get("status")).casefold() != "done"
                })
                if external_blocked:
                    raise ArchiveConflictError(
                        "Задачи команды блокируют активные задачи: "
                        + ", ".join(external_blocked)
                        + ". Сначала измените зависимости или выберите перераспределение."
                    )

        archived_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if entity_type == "team":
                if team_engineer_action == "UNASSIGN":
                    self._override_rows(
                        connection,
                        "engineers",
                        lambda row: _text(row.get("team_id")) == entity_id,
                        lambda row: {**row, "team_id": None},
                        archived_at,
                    )
                elif team_engineer_action == "DEACTIVATE":
                    for engineer_id in affected_engineers:
                        self._write_lifecycle(
                            connection, "engineer", engineer_id,
                            "NO_LONGER_PLANNED", f"Деактивирован вместе с командой {entity_id}",
                            archived_at,
                        )
                if team_task_action == "REDISTRIBUTE":
                    active_task_ids = set(affected_tasks)
                    self._override_rows(
                        connection,
                        "tasks",
                        lambda row: _text(row.get("task_id")) in active_task_ids,
                        lambda row: {**row, "team_id": None},
                        archived_at,
                    )
                elif team_task_action == "REMOVE":
                    for task_id in affected_tasks:
                        self._write_lifecycle(
                            connection, "task", task_id,
                            "NO_LONGER_RELEVANT", f"Убрана вместе с командой {entity_id}",
                            archived_at,
                        )
            self._write_lifecycle(
                connection, entity_type, entity_id,
                reason_code, reason_text, archived_at,
            )
            self._bump_revision(connection)

        lifecycle = self.lifecycle(entity_type, entity_id)
        lifecycle.update({"entity_type": entity_type, "entity_id": entity_id})
        return {
            "entity": lifecycle,
            "affected_engineer_ids": affected_engineers,
            "affected_task_ids": affected_tasks,
            "team_engineer_action": team_engineer_action,
            "team_task_action": team_task_action,
        }

    def delete_entity(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        """Permanently remove one catalog entity and its catalog relations.

        This is intentionally separate from archive_with_relations: archive is
        the recoverable business operation, while this method is used only by
        the explicit "delete forever" control on an entity profile.
        """

        entity_id = _text(entity_id)
        if entity_type not in ENTITY_TABLES:
            raise ArchiveValidationError(f"Unsupported entity type: {entity_type}")
        if not entity_id:
            raise CatalogNotFoundError("ID сущности не может быть пустым.")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            tables = {
                table_name: self._connection_rows(connection, table_name)
                for table_name in (
                    "tasks", "task_role_effort", "task_dependencies",
                    "engineers", "engineer_skills", "team_history",
                    "task_skill_requirements",
                )
            }
            if not self._entity_exists(
                {name: [payload for _, payload in rows] for name, rows in tables.items()},
                entity_type,
                entity_id,
            ):
                raise CatalogNotFoundError(f"Unknown {entity_type}_id: {entity_id}")

            removed = 0
            if entity_type == "task":
                removed += self._delete_rows(
                    connection, "tasks",
                    lambda row: _text(row.get("task_id")) == entity_id,
                )
                for table_name in ("task_role_effort", "task_skill_requirements"):
                    removed += self._delete_rows(
                        connection, table_name,
                        lambda row: _text(row.get("task_id")) == entity_id,
                    )
                removed += self._delete_rows(
                    connection, "task_dependencies",
                    lambda row: _text(row.get("blocking_task_id")) == entity_id
                    or _text(row.get("blocked_task_id")) == entity_id,
                )
            elif entity_type == "engineer":
                removed += self._delete_rows(
                    connection, "engineers",
                    lambda row: _text(row.get("engineer_id")) == entity_id,
                )
                removed += self._delete_rows(
                    connection, "engineer_skills",
                    lambda row: _text(row.get("engineer_id")) == entity_id,
                )
            else:
                # Keep engineers and tasks as catalog entities, but remove the
                # deleted team's membership/ownership links. A membership row
                # becomes an unassigned bucket unless that engineer already
                # has one, in which case the redundant row is removed.
                engineer_rows = tables["engineers"]
                for row_key, payload in engineer_rows:
                    if _text(payload.get("team_id")) != entity_id:
                        continue
                    engineer_id = _text(payload.get("engineer_id"))
                    has_free = any(
                        _text(other.get("engineer_id")) == engineer_id
                        and not _text(other.get("team_id"))
                        for _, other in engineer_rows
                    )
                    if has_free:
                        connection.execute(
                            "DELETE FROM catalog_row_overrides WHERE table_name=? AND row_key=?",
                            ("engineers", row_key),
                        )
                        connection.execute(
                            "DELETE FROM catalog_rows WHERE table_name=? AND row_key=?",
                            ("engineers", row_key),
                        )
                    else:
                        replacement = {**payload, "team_id": None}
                        now = datetime.now(timezone.utc).isoformat()
                        connection.execute(
                            "INSERT INTO catalog_row_overrides(table_name,row_key,payload,updated_at) "
                            "VALUES('engineers',?,?,?) ON CONFLICT(table_name,row_key) DO UPDATE SET "
                            "payload=excluded.payload,updated_at=excluded.updated_at",
                            (row_key, _json_payload(replacement), now),
                        )
                    removed += 1
                # Tasks remain in the catalog but no longer point at a deleted
                # owner team.
                for row_key, payload in tables["tasks"]:
                    if _text(payload.get("team_id")) != entity_id:
                        continue
                    replacement = {**payload, "team_id": None}
                    now = datetime.now(timezone.utc).isoformat()
                    connection.execute(
                        "INSERT INTO catalog_row_overrides(table_name,row_key,payload,updated_at) "
                        "VALUES('tasks',?,?,?) ON CONFLICT(table_name,row_key) DO UPDATE SET "
                        "payload=excluded.payload,updated_at=excluded.updated_at",
                        (row_key, _json_payload(replacement), now),
                    )
                removed += self._delete_rows(
                    connection, "team_history",
                    lambda row: _text(row.get("team_id")) == entity_id,
                )

            connection.execute(
                "DELETE FROM entity_lifecycle WHERE entity_type=? AND entity_id=?",
                (entity_type, entity_id),
            )
            self._bump_revision(connection)
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "removed_rows": removed,
            "permanently_deleted": True,
        }

    def lifecycle(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM entity_lifecycle WHERE entity_type=? AND entity_id=?",
                (entity_type, entity_id),
            ).fetchone()
        if row is None:
            return {
                "archived": False,
                "archived_at": None,
                "archive_reason_code": None,
                "archive_reason_text": None,
                "archive_reason_label": None,
            }
        payload = dict(row)
        payload["archived"] = bool(payload["archived"])
        payload["archive_reason_label"] = ARCHIVE_REASON_LABELS.get(
            entity_type, {}
        ).get(payload.get("archive_reason_code"))
        return payload

    def archived_ids(self, entity_type: str) -> set[str]:
        with self._connect() as connection:
            return {
                row[0]
                for row in connection.execute(
                    "SELECT entity_id FROM entity_lifecycle "
                    "WHERE entity_type=? AND archived=1",
                    (entity_type,),
                )
            }

    def _entity_exists(self, tables: Mapping[str, list[dict[str, Any]]], entity_type: str, entity_id: str) -> bool:
        if entity_type == "team":
            return any(
                _text(row.get("team_id")) == entity_id
                for table_name in ("team_history", "engineers", "tasks")
                for row in tables.get(table_name, [])
            )
        table = ENTITY_TABLES[entity_type]
        field = {"task": "task_id", "engineer": "engineer_id"}[entity_type]
        return any(_text(row.get(field)) == entity_id for row in tables.get(table, []))

    def active_planning_tables(self) -> dict[str, list[dict[str, Any]]]:
        tables = self.tables()
        archived_tasks = self.archived_ids("task")
        archived_engineers = self.archived_ids("engineer")
        archived_teams = self.archived_ids("team")
        raw_tasks = {
            _text(row.get("task_id")): row for row in tables["tasks"]
        }
        planning_task_ids = {
            task_id
            for task_id, row in raw_tasks.items()
            if (
                task_id not in archived_tasks
                and _text(row.get("team_id")) not in archived_teams
            )
            or _text(row.get("status")).casefold() == "done"
        }
        active_task_ids = {
            task_id
            for task_id, row in raw_tasks.items()
            if task_id not in archived_tasks
            and _text(row.get("team_id")) not in archived_teams
        }
        active_engineer_ids = {
            _text(row.get("engineer_id"))
            for row in tables["engineers"]
            if _text(row.get("engineer_id")) not in archived_engineers
            and _text(row.get("team_id")) not in archived_teams
        }
        result = dict(tables)
        result["tasks"] = [
            row for row in tables["tasks"]
            if _text(row.get("task_id")) in planning_task_ids
        ]
        result["task_role_effort"] = [
            row for row in tables["task_role_effort"]
            if _text(row.get("task_id")) in active_task_ids
        ]
        result["task_skill_requirements"] = [
            row for row in tables["task_skill_requirements"]
            if _text(row.get("task_id")) in active_task_ids
        ]
        result["engineers"] = [
            row for row in tables["engineers"]
            if _text(row.get("engineer_id")) in active_engineer_ids
            and _text(row.get("team_id")) not in archived_teams
        ]
        result["engineer_skills"] = [
            row for row in tables["engineer_skills"]
            if _text(row.get("engineer_id")) in active_engineer_ids
        ]
        result["team_history"] = [
            row for row in tables["team_history"]
            if _text(row.get("team_id")) not in archived_teams
        ]
        result["task_dependencies"] = [
            row for row in tables["task_dependencies"]
            if _text(row.get("blocked_task_id")) in active_task_ids
            and _text(row.get("blocking_task_id")) in planning_task_ids
        ]
        result["engineer_availability"] = [
            row for row in self.availability_constraints()
            if _text(row.get("engineer_id")) in active_engineer_ids
        ]
        return result

    @staticmethod
    def _groups(rows: Iterable[Mapping[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for source in rows:
            row = dict(source)
            entity_id = _text(row.get(field))
            if entity_id:
                grouped.setdefault(entity_id, []).append(row)
        return grouped

    @staticmethod
    def _archive_decision_key(entity_type: str, entity_id: str) -> str:
        return f"{entity_type}:{entity_id}"

    @staticmethod
    def _archive_field_label(field: str) -> str | None:
        return {
            "summary": "Название",
            "name": "Имя",
            "team_name": "Название команды",
            "team_id": "Команда",
            "status": "Статус",
            "rung": "Приоритет Rung",
            "story_points": "Story Points",
            "initiative_id": "Инициатива",
            "role": "Роль",
            "grade": "Грейд",
            "capacity_rate": "Занятость",
            "snapshot_date": "Дата снимка",
            "velocity_achieved": "Фактическая производительность",
            "capacity_sp": "Ручная capacity за спринт",
            "planned_sp": "Запланировано Story Points",
        }.get(field)

    @classmethod
    def _archive_differences(
        cls,
        saved_rows: Iterable[Mapping[str, Any]],
        excel_rows: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        saved = [dict(row) for row in saved_rows]
        excel = [dict(row) for row in excel_rows]
        fields = sorted(
            set().union(*(row.keys() for row in [*saved, *excel])) if [*saved, *excel] else set()
        )
        result = []
        for field in fields:
            label = cls._archive_field_label(field)
            if not label:
                continue
            current = saved[0].get(field) if saved else None
            incoming = excel[0].get(field) if excel else None
            if current == incoming:
                continue
            result.append({"field": field, "label": label, "current": current, "incoming": incoming})
        return result

    def _archived_entity_records(
        self,
        current: Mapping[str, list[dict[str, Any]]],
        materialized: Mapping[str, list[dict[str, Any]]],
        archived: Mapping[str, set[str]],
    ) -> list[dict[str, Any]]:
        specs = {
            "tasks": ("task", "task_id", "Задача"),
            "engineers": ("engineer", "engineer_id", "Инженер"),
            "team_history": ("team", "team_id", "Команда"),
        }
        records: list[dict[str, Any]] = []
        for table_name, (entity_type, field, label) in specs.items():
            current_groups = self._groups(current.get(table_name, []), field)
            incoming_groups = self._groups(materialized.get(table_name, []), field)
            for entity_id in sorted(set(incoming_groups) & archived.get(entity_type, set())):
                lifecycle = self.lifecycle(entity_type, entity_id)
                saved_rows = current_groups.get(entity_id, [])
                incoming_rows = incoming_groups.get(entity_id, [])
                display = next((
                    _text(row.get(key)) for row in [*incoming_rows, *saved_rows]
                    for key in ("summary", "name", "team_name") if _text(row.get(key))
                ), entity_id)
                records.append({
                    "decision_key": self._archive_decision_key(entity_type, entity_id),
                    "entity_type": entity_type,
                    "entity_label": label,
                    "entity_id": entity_id,
                    "display_name": display,
                    "archived_at": lifecycle.get("archived_at"),
                    "archive_reason_label": lifecycle.get("archive_reason_label"),
                    "archive_reason_text": lifecycle.get("archive_reason_text"),
                    "current": deepcopy(saved_rows),
                    "incoming": deepcopy(incoming_rows),
                    "differences": self._archive_differences(saved_rows, incoming_rows),
                })
        # A workbook can reference an archived team without carrying a
        # Team_history row for it. Keep that decision explicit as well.
        referenced_teams = {
            _text(row.get("team_id"))
            for table_name in ("tasks", "engineers", "team_history")
            for row in materialized.get(table_name, [])
            if _text(row.get("team_id")) in archived.get("team", set())
        }
        known = {(item["entity_type"], item["entity_id"]) for item in records}
        for team_id in sorted(referenced_teams):
            if ("team", team_id) in known:
                continue
            lifecycle = self.lifecycle("team", team_id)
            saved_rows = self._groups(current.get("team_history", []), "team_id").get(team_id, [])
            records.append({
                "decision_key": self._archive_decision_key("team", team_id),
                "entity_type": "team", "entity_label": "Команда", "entity_id": team_id,
                "display_name": team_id, "archived_at": lifecycle.get("archived_at"),
                "archive_reason_label": lifecycle.get("archive_reason_label"),
                "archive_reason_text": lifecycle.get("archive_reason_text"),
                "current": deepcopy(saved_rows), "incoming": [], "differences": [],
                "referenced_only": True,
            })
        return sorted(records, key=lambda item: (item["entity_label"], item["entity_id"]))

    def merge_tables(
        self,
        incoming: Mapping[str, Iterable[Mapping[str, Any]]],
        *,
        dry_run: bool = False,
        archive_decisions: Mapping[str, str] | None = None,
        existing_decisions: Mapping[str, str] | None = None,
    ) -> CatalogMergeResult:
        """Atomically add entities, or inspect the exact merge without writing."""

        current = self.tables()
        materialized = {name: [dict(row) for row in rows] for name, rows in incoming.items()}
        added = {name: 0 for name in materialized}
        kept = {name: 0 for name in materialized}
        warnings: list[str] = []
        conflicts: list[dict[str, Any]] = []
        archived = {
            entity_type: self.archived_ids(entity_type)
            for entity_type in ENTITY_TABLES
        }
        archived_entities = self._archived_entity_records(current, materialized, archived)
        decisions = {str(key): str(value).upper() for key, value in (archive_decisions or {}).items()}
        active_decisions = {str(key): str(value).upper() for key, value in (existing_decisions or {}).items()}
        allowed_decisions = {"KEEP_ARCHIVED", "RESTORE", "RESTORE_UPDATE"}
        known_archive_keys = {item["decision_key"] for item in archived_entities}
        active_entity_keys = {
            self._archive_decision_key(entity_type, entity_id)
            for table_name, (entity_type, field, _label) in {
                "tasks": ("task", "task_id", "Задача"),
                "engineers": ("engineer", "engineer_id", "Инженер"),
                "team_history": ("team", "team_id", "Команда"),
            }.items()
            for entity_id in self._groups(current.get(table_name, []), field)
        }
        unknown_decisions = set(decisions) - known_archive_keys - active_entity_keys
        if unknown_decisions:
            raise ArchiveValidationError("Решение относится к неизвестной архивной записи.")
        invalid_decisions = {key: value for key, value in decisions.items() if value not in allowed_decisions}
        if invalid_decisions:
            raise ArchiveValidationError("Выберите допустимое действие для архивной записи.")
        decisions = {
            item["decision_key"]: decisions.get(item["decision_key"], "KEEP_ARCHIVED")
            for item in archived_entities
        }
        allowed_active_decisions = {"KEEP_CURRENT", "UPDATE"}
        unknown_active_decisions = set(active_decisions) - active_entity_keys
        if unknown_active_decisions:
            raise ArchiveValidationError("Решение относится к неизвестной активной записи.")
        if any(value not in allowed_active_decisions for value in active_decisions.values()):
            raise ArchiveValidationError("Выберите допустимое действие для существующей записи.")
        entity_summary = {"new": 0, "active_existing": 0, "archived": len(archived_entities)}

        referenced_archived_teams = {
            _text(row.get("team_id"))
            for table_name in ("tasks", "engineers", "team_history")
            for row in materialized.get(table_name, [])
            if _text(row.get("team_id")) in archived["team"]
        }
        for team_id in sorted(referenced_archived_teams):
            warnings.append(
                f"Команда {team_id} присутствует в загруженном файле, "
                "но ранее была удалена из активного каталога."
            )

        entity_specs = {
            "tasks": ("task", "task_id", "Задача"),
            "engineers": ("engineer", "engineer_id", "Инженер"),
            "team_history": ("team", "team_id", "Команда"),
        }
        new_entity_ids: dict[str, set[str]] = {}
        rows_to_insert: dict[str, list[dict[str, Any]]] = {}
        rows_to_delete: list[tuple[str, str]] = []
        updated_entities: dict[str, int] = {}
        for decision_key, decision in active_decisions.items():
            if decision != "UPDATE" or ":" not in decision_key:
                continue
            entity_type = decision_key.split(":", 1)[0]
            updated_entities[entity_type] = updated_entities.get(entity_type, 0) + 1
        for table_name, (entity_type, field, label) in entity_specs.items():
            existing_groups = self._groups(current.get(table_name, []), field)
            incoming_groups = self._groups(materialized.get(table_name, []), field)
            new_entity_ids[entity_type] = set(incoming_groups) - set(existing_groups)
            rows_to_insert[table_name] = []
            for entity_id, rows in incoming_groups.items():
                if entity_id in archived[entity_type] and decisions[self._archive_decision_key(entity_type, entity_id)] == "KEEP_ARCHIVED":
                    kept[table_name] += len(rows)
                    warnings.append(
                        f"{label} {entity_id} присутствует в загруженном файле, "
                        "но ранее была удалена из активного каталога."
                    )
                    continue
                if entity_id in archived[entity_type]:
                    kept[table_name] += len(rows)
                    continue
                if entity_id in existing_groups:
                    entity_summary["active_existing"] += 1
                    old = sorted(_comparison_payload(row) for row in existing_groups[entity_id])
                    new = sorted(_comparison_payload(row) for row in rows)
                    if old != new:
                        conflicts.append({
                            "table": table_name,
                            "entity_type": entity_type,
                            "entity_id": entity_id,
                            "saved": [dict(row) for row in existing_groups[entity_id]],
                            "excel": [dict(row) for row in rows],
                        })
                    decision = active_decisions.get(
                        self._archive_decision_key(entity_type, entity_id), "KEEP_CURRENT"
                    )
                    if decision == "UPDATE" and old != new:
                        rows_to_delete.extend(
                            (table_name, _row_identity(table_name, row))
                            for row in existing_groups[entity_id]
                        )
                        rows_to_insert[table_name].extend(rows)
                    else:
                        kept[table_name] += len(rows)
                        if old != new:
                            warnings.append(
                                f"{label} {entity_id} уже существует. "
                                "Оставлена текущая версия."
                            )
                    continue
                rows_to_insert[table_name].extend(rows)
                entity_summary["new"] += 1

        linked_specs = {
            "task_role_effort": ("task", "task_id"),
            "engineer_skills": ("engineer", "engineer_id"),
        }
        for table_name, (entity_type, field) in linked_specs.items():
            rows_to_insert[table_name] = []
            current_groups = self._groups(current.get(table_name, []), field)
            incoming_groups = self._groups(materialized.get(table_name, []), field)
            for row in materialized.get(table_name, []):
                entity_id = _text(row.get(field))
                if entity_id in new_entity_ids.get(entity_type, set()):
                    rows_to_insert[table_name].append(row)
                else:
                    kept[table_name] = kept.get(table_name, 0) + 1
            for entity_id, incoming_rows in incoming_groups.items():
                if entity_id in new_entity_ids.get(entity_type, set()):
                    continue
                saved_rows = current_groups.get(entity_id, [])
                decision = active_decisions.get(
                    self._archive_decision_key(entity_type, entity_id), "KEEP_CURRENT"
                )
                if decision == "UPDATE":
                    rows_to_delete.extend(
                        (table_name, _row_identity(table_name, row)) for row in saved_rows
                    )
                    rows_to_insert[table_name].extend(incoming_rows)
                    continue
                if sorted(_comparison_payload(row) for row in saved_rows) == sorted(
                    _comparison_payload(row) for row in incoming_rows
                ):
                    continue
                warnings.append(
                    f"Связанные данные {entity_id} в {table_name} отличаются. "
                    "Данные из Excel не применены."
                )
                conflicts.append({
                    "table": table_name,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "saved": [dict(row) for row in saved_rows],
                    "excel": [dict(row) for row in incoming_rows],
                })
            selected_update_ids = {
                key.split(":", 1)[1]
                for key, decision in active_decisions.items()
                if decision == "UPDATE" and key.startswith(f"{entity_type}:")
            }
            for entity_id in selected_update_ids - set(incoming_groups):
                rows_to_delete.extend(
                    (table_name, _row_identity(table_name, row))
                    for row in current_groups.get(entity_id, [])
                )

        # Automatic title extraction is refreshable, while explicit/manual
        # requirements and user rejections are durable overrides.  Re-importing
        # an existing task replaces only its previous ``source=title`` rows.
        table_name = "task_skill_requirements"
        rows_to_insert[table_name] = []
        current_groups = self._groups(current.get(table_name, []), "task_id")
        incoming_groups = self._groups(materialized.get(table_name, []), "task_id")
        for entity_id, incoming_rows in incoming_groups.items():
            if entity_id in new_entity_ids.get("task", set()):
                rows_to_insert[table_name].extend(incoming_rows)
                continue
            if entity_id in archived["task"]:
                kept[table_name] = kept.get(table_name, 0) + len(incoming_rows)
                continue
            saved_rows = current_groups.get(entity_id, [])
            if active_decisions.get(self._archive_decision_key("task", entity_id)) == "UPDATE":
                rows_to_delete.extend(
                    (table_name, _row_identity(table_name, row)) for row in saved_rows
                )
                rows_to_insert[table_name].extend(incoming_rows)
                continue
            saved_manual = [
                row for row in saved_rows
                if _text(row.get("source")).casefold() != "title"
                or _truthy(row.get("rejected"))
            ]
            protected_skills = {
                _text(row.get("technology")).casefold()
                for row in saved_manual
                if _text(row.get("technology"))
            }
            for row in saved_rows:
                if _text(row.get("source")).casefold() == "title" and not _truthy(row.get("rejected")):
                    rows_to_delete.append((table_name, _row_identity(table_name, row)))
            for row in incoming_rows:
                technology = _text(row.get("technology")).casefold()
                source = _text(row.get("source")).casefold()
                if source == "title" and technology not in protected_skills:
                    rows_to_insert[table_name].append(row)
                else:
                    kept[table_name] = kept.get(table_name, 0) + 1
        selected_task_updates = {
            key.split(":", 1)[1]
            for key, decision in active_decisions.items()
            if decision == "UPDATE" and key.startswith("task:")
        }
        for entity_id in selected_task_updates - set(incoming_groups):
            rows_to_delete.extend(
                (table_name, _row_identity(table_name, row))
                for row in current_groups.get(entity_id, [])
            )

        independent_tables = set(materialized) - set(rows_to_insert)
        for table_name in independent_tables:
            existing_keys = {
                _row_identity(table_name, row) for row in current.get(table_name, [])
            }
            rows_to_insert[table_name] = []
            for row in materialized[table_name]:
                key = _row_identity(table_name, row)
                if key in existing_keys:
                    kept[table_name] += 1
                else:
                    rows_to_insert[table_name].append(row)
                    existing_keys.add(key)

        if dry_run:
            return CatalogMergeResult(
                {name: len(rows) for name, rows in rows_to_insert.items()},
                kept,
                tuple(dict.fromkeys(warnings)),
                tuple(conflicts),
                tuple(archived_entities),
                {}, {}, {"archived": sum(1 for value in decisions.values() if value == "KEEP_ARCHIVED")},
                updated_entities,
                entity_summary,
            )

        now = datetime.now(timezone.utc).isoformat()
        restored: dict[str, int] = {}
        restored_updated: dict[str, int] = {}
        kept_archived: dict[str, int] = {}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for table_name, row_key in rows_to_delete:
                connection.execute(
                    "DELETE FROM catalog_row_overrides WHERE table_name=? AND row_key=?",
                    (table_name, row_key),
                )
                connection.execute(
                    "DELETE FROM catalog_rows WHERE table_name=? AND row_key=?",
                    (table_name, row_key),
                )
            for item in archived_entities:
                decision = decisions[item["decision_key"]]
                entity_type, entity_id = item["entity_type"], item["entity_id"]
                if decision == "KEEP_ARCHIVED":
                    kept_archived[entity_type] = kept_archived.get(entity_type, 0) + 1
                    continue
                connection.execute(
                    "UPDATE entity_lifecycle SET archived=0 WHERE entity_type=? AND entity_id=?",
                    (entity_type, entity_id),
                )
                target = restored_updated if decision == "RESTORE_UPDATE" else restored
                target[entity_type] = target.get(entity_type, 0) + 1
                if decision != "RESTORE_UPDATE":
                    continue
                table_name = ENTITY_TABLES[entity_type]
                identity_field = {"task": "task_id", "engineer": "engineer_id", "team": "team_id"}[entity_type]
                incoming_rows = self._groups(materialized.get(table_name, []), identity_field).get(entity_id, [])
                if not incoming_rows:
                    continue
                incoming_row = incoming_rows[0]
                protected = {
                    "task": {
                        "task_id", "status", "completed_in_quarter", "completion_sprint",
                        "planned_sprints", "allocations", "dependencies", "is_placeholder",
                        "original_team_id",
                    },
                    "engineer": {"engineer_id", "team_id", "role", "role_raw", "allocations"},
                    "team": {"team_id", "snapshot_date"},
                }[entity_type]
                rows = connection.execute(
                    "SELECT source.row_key,COALESCE(changes.payload,source.payload) payload "
                    "FROM catalog_rows source LEFT JOIN catalog_row_overrides changes "
                    "ON changes.table_name=source.table_name AND changes.row_key=source.row_key "
                    "WHERE source.table_name=?",
                    (table_name,),
                ).fetchall()
                for row in rows:
                    payload = json.loads(row["payload"])
                    if _text(payload.get(identity_field)) != entity_id:
                        continue
                    replacement = dict(payload)
                    replacement.update({key: value for key, value in incoming_row.items() if key not in protected})
                    connection.execute(
                        "INSERT INTO catalog_row_overrides(table_name,row_key,payload,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(table_name,row_key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                        (table_name, row["row_key"], _json_payload(replacement), now),
                    )
            for table_name, rows in rows_to_insert.items():
                for row in rows:
                    key = _row_identity(table_name, row)
                    connection.execute(
                        "INSERT OR IGNORE INTO catalog_rows"
                        "(table_name,row_key,payload,created_at) VALUES(?,?,?,?)",
                        (table_name, key, _json_payload(row), now),
                    )
                    if connection.execute("SELECT changes()").fetchone()[0]:
                        added[table_name] = added.get(table_name, 0) + 1
                    else:
                        kept[table_name] = kept.get(table_name, 0) + 1
            if rows_to_delete or any(added.values()) or restored or restored_updated:
                self._bump_revision(connection)
        return CatalogMergeResult(
            added, kept, tuple(dict.fromkeys(warnings)), tuple(conflicts),
            tuple(archived_entities), restored, restored_updated, kept_archived,
            updated_entities,
            entity_summary,
        )

    def archive(
        self,
        entity_type: str,
        entity_id: str,
        reason_code: str,
        reason_text: str | None = None,
        *,
        current_plan_references: int = 0,
    ) -> dict[str, Any]:
        if entity_type not in ENTITY_TABLES:
            raise ArchiveValidationError(f"Unsupported entity type: {entity_type}")
        reason_code = _text(reason_code).upper()
        reason_text = _text(reason_text) or None
        if reason_code not in ARCHIVE_REASON_LABELS[entity_type]:
            raise ArchiveValidationError("Выберите допустимую причину удаления.")
        if reason_code == "OTHER" and not reason_text:
            raise ArchiveValidationError("Для другой причины укажите пояснение.")
        tables = self.tables()
        if not self._entity_exists(tables, entity_type, entity_id):
            raise CatalogNotFoundError(f"Unknown {entity_type}_id: {entity_id}")

        if entity_type == "team":
            active_tasks = [
                row for row in tables["tasks"]
                if _text(row.get("team_id")) == entity_id
                and not self.lifecycle("task", _text(row.get("task_id")))["archived"]
                and _text(row.get("status")).casefold() != "done"
            ]
            active_engineers = {
                _text(row.get("engineer_id")) for row in tables["engineers"]
                if _text(row.get("team_id")) == entity_id
                and not self.lifecycle("engineer", _text(row.get("engineer_id")))["archived"]
            }
            if active_tasks or active_engineers or current_plan_references:
                raise ArchiveConflictError(
                    f"Команда используется {len(active_tasks)} задачами и "
                    f"{len(active_engineers)} инженерами. Сначала переназначьте "
                    "связанные объекты."
                )

        if entity_type == "task":
            task_row = next(
                row for row in tables["tasks"] if _text(row.get("task_id")) == entity_id
            )
            if _text(task_row.get("status")).casefold() != "done":
                archived_tasks = self.archived_ids("task")
                tasks_by_id = {
                    _text(row.get("task_id")): row for row in tables["tasks"]
                }
                active_blocked = {
                    _text(row.get("blocked_task_id"))
                    for row in tables["task_dependencies"]
                    if _text(row.get("blocking_task_id")) == entity_id
                    and _text(row.get("blocked_task_id")) not in archived_tasks
                    and _text(
                        tasks_by_id.get(_text(row.get("blocked_task_id")), {}).get(
                            "status"
                        )
                    ).casefold()
                    != "done"
                }
                if active_blocked:
                    raise ArchiveConflictError(
                        "Задача является активным blocker для: "
                        + ", ".join(sorted(active_blocked))
                        + ". Сначала измените зависимости."
                    )

        archived_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO entity_lifecycle(
                    entity_type,entity_id,archived,archived_at,
                    archive_reason_code,archive_reason_text
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(entity_type,entity_id) DO UPDATE SET
                    archived=excluded.archived,
                    archived_at=excluded.archived_at,
                    archive_reason_code=excluded.archive_reason_code,
                    archive_reason_text=excluded.archive_reason_text
                """,
                (entity_type, entity_id, 1, archived_at, reason_code, reason_text),
            )
            self._bump_revision(connection)
        result = self.lifecycle(entity_type, entity_id)
        result["entity_type"] = entity_type
        result["entity_id"] = entity_id
        result["current_plan_warning"] = bool(current_plan_references)
        return result


@lru_cache(maxsize=1)
def get_catalog_repository() -> CatalogRepository:
    from .service import PROCESSED_DIR, TABLE_FILES, get_planning_service

    # A fresh installation is allowed to have no generated CSV snapshot yet.
    # In that state the persistent SQLite catalog starts empty and the first
    # accepted Excel upload becomes its source of truth.  Only bootstrap from
    # processed data when the complete legacy snapshot is present; attempting
    # to construct PlanningAPIService with missing files used to make every API
    # endpoint fail with HTTP 503 before an upload was even possible.
    processed_snapshot_exists = all(
        (PROCESSED_DIR / filename).is_file()
        for filename in TABLE_FILES.values()
    )
    seed_tables = get_planning_service().tables if processed_snapshot_exists else None
    return CatalogRepository(CATALOG_DB, seed_tables=seed_tables)
