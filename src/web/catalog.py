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
            "summary": dict(self.summary or {}),
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


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
                """
            )

    def _seed_if_empty(
        self, tables: Mapping[str, Iterable[Mapping[str, Any]]]
    ) -> None:
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM catalog_rows").fetchone()[0]
            if count:
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

        lifecycle = self.lifecycle(entity_type, entity_id)
        lifecycle.update({"entity_type": entity_type, "entity_id": entity_id})
        return {
            "entity": lifecycle,
            "affected_engineer_ids": affected_engineers,
            "affected_task_ids": affected_tasks,
            "team_engineer_action": team_engineer_action,
            "team_task_action": team_task_action,
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
                    kept[table_name] += len(rows)
                    old = sorted(_comparison_payload(row) for row in existing_groups[entity_id])
                    new = sorted(_comparison_payload(row) for row in rows)
                    if old != new:
                        warnings.append(
                            f"{label} {entity_id} уже существует. "
                            "Данные из Excel не применены."
                        )
                        conflicts.append({
                            "table": table_name,
                            "entity_type": entity_type,
                            "entity_id": entity_id,
                            "saved": [dict(row) for row in existing_groups[entity_id]],
                            "excel": [dict(row) for row in rows],
                        })
                    continue
                rows_to_insert[table_name].extend(rows)
                entity_summary["new"] += 1

        linked_specs = {
            "task_role_effort": ("task", "task_id"),
            "task_skill_requirements": ("task", "task_id"),
            "engineer_skills": ("engineer", "engineer_id"),
        }
        for table_name, (entity_type, field) in linked_specs.items():
            rows_to_insert[table_name] = []
            for row in materialized.get(table_name, []):
                entity_id = _text(row.get(field))
                if entity_id in new_entity_ids.get(entity_type, set()):
                    rows_to_insert[table_name].append(row)
                else:
                    kept[table_name] = kept.get(table_name, 0) + 1

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
                entity_summary,
            )

        now = datetime.now(timezone.utc).isoformat()
        restored: dict[str, int] = {}
        restored_updated: dict[str, int] = {}
        kept_archived: dict[str, int] = {}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
        return CatalogMergeResult(
            added, kept, tuple(dict.fromkeys(warnings)), tuple(conflicts),
            tuple(archived_entities), restored, restored_updated, kept_archived,
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
        result = self.lifecycle(entity_type, entity_id)
        result["entity_type"] = entity_type
        result["entity_id"] = entity_id
        result["current_plan_warning"] = bool(current_plan_references)
        return result


@lru_cache(maxsize=1)
def get_catalog_repository() -> CatalogRepository:
    from .service import get_planning_service

    return CatalogRepository(CATALOG_DB, seed_tables=get_planning_service().tables)
