"""Persistent insert-only working catalog and entity lifecycle repository."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": dict(self.added),
            "kept": dict(self.kept),
            "warnings": list(self.warnings),
            "conflicts": [dict(conflict) for conflict in self.conflicts],
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
                "SELECT table_name,payload FROM catalog_rows ORDER BY table_name,row_key"
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

    def merge_tables(
        self,
        incoming: Mapping[str, Iterable[Mapping[str, Any]]],
        *,
        dry_run: bool = False,
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
                if entity_id in archived[entity_type]:
                    kept[table_name] += len(rows)
                    warnings.append(
                        f"{label} {entity_id} присутствует в загруженном файле, "
                        "но ранее была удалена из активного каталога."
                    )
                    continue
                if entity_id in existing_groups:
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
            )

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
            added, kept, tuple(dict.fromkeys(warnings)), tuple(conflicts)
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
