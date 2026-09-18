"""User-facing data-quality preflight for an uploaded planning workbook.

The checks adapt the existing normalized dataset validation to a structured UI
contract.  They do not repair data and never invoke the planner.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from scripts.data_audit import strongly_connected_components
from src.data.normalization import NormalizedDataset
from src.data.parser import ParsedDataset, RawTable
from src.data.validation import ValidationReport, is_number

from .catalog import CatalogMergeResult


CATEGORY_TITLES = {
    "structure": "Критические проблемы структуры данных",
    "dependencies": "Зависимости между задачами",
    "tasks": "Задачи",
    "teams": "Команды и ресурсы",
    "engineers": "Сотрудники",
    "effort": "Роли и трудозатраты",
    "history": "История производительности команд",
    "skills": "Навыки и профили сотрудников",
    "duplicates": "Повторяющиеся данные",
    "conflicts": "Конфликты с уже сохранёнными данными",
}
CATEGORY_ORDER = {name: index for index, name in enumerate(CATEGORY_TITLES)}
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_done(row: dict[str, Any]) -> bool:
    return _text(row.get("status")).casefold() in {"done", "completed"}


def _source_rows(table: RawTable, id_index: int) -> dict[str, list[int]]:
    result: dict[str, list[int]] = defaultdict(list)
    for offset, values in enumerate(table.rows, start=1):
        entity_id = _text(values[id_index] if id_index < len(values) else None)
        if entity_id:
            result[entity_id].append(table.header_row + offset)
    return result


def _issue(
    *,
    code: str,
    severity: str,
    category: str,
    title: str,
    message: str,
    entity_type: str = "dataset",
    entity_id: str | None = None,
    sheet: str | None = None,
    row: int | None = None,
    details: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "category": category,
        "title": title,
        "message": message,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "sheet": sheet,
        "row": row,
        "details": [str(value) for value in details if str(value).strip()],
    }


def _catalog_conflict_issues(preview: CatalogMergeResult) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    labels = {"Задача": "task", "Инженер": "engineer", "Команда": "team"}
    conflict_by_entity = {
        (str(item.get("entity_type")), str(item.get("entity_id"))): item
        for item in preview.conflicts
    }
    field_labels = {
        "summary": "Название", "team_id": "Команда", "status": "Статус",
        "rung": "Приоритет Rung", "story_points": "Story Points",
        "initiative_id": "Инициатива", "role": "Роль", "grade": "Грейд",
        "capacity_rate": "Занятость", "snapshot_date": "Дата снимка",
        "velocity_achieved": "Фактическая производительность",
        "planned_sp": "Запланировано Story Points",
    }

    def display(value: Any) -> str:
        return "не указано" if value is None or value == "" else str(value)

    def differences(conflict: dict[str, Any] | None) -> tuple[str, ...]:
        if not conflict:
            return ()
        saved_rows = conflict.get("saved") or []
        excel_rows = conflict.get("excel") or []
        all_rows = [*saved_rows, *excel_rows]
        fields = sorted(
            set().union(*(row.keys() for row in all_rows)) if all_rows else set(),
            key=lambda field: (field not in field_labels, field_labels.get(field, field)),
        )
        details: list[str] = []
        for field in fields:
            if field not in field_labels:
                continue
            saved_values = [row.get(field) for row in saved_rows]
            excel_values = [row.get(field) for row in excel_rows]
            if saved_values == excel_values:
                continue
            details.append(
                f"{field_labels[field]}: в Excel — "
                f"{', '.join(display(value) for value in excel_values)}; сохранено — "
                f"{', '.join(display(value) for value in saved_values)}"
            )
        return tuple(details)

    for warning in preview.warnings:
        words = warning.split()
        label = words[0] if words else ""
        entity_id = words[1] if len(words) > 1 and label in labels else None
        archived = "удалена из активного каталога" in warning
        conflict = conflict_by_entity.get((labels.get(label, "dataset"), entity_id or ""))
        detail_lines = differences(conflict)
        result.append(
            _issue(
                code="ARCHIVED_ENTITY_IN_UPLOAD" if archived else "CATALOG_ENTITY_CONFLICT",
                severity="warning",
                category="conflicts",
                title=(
                    "В файле снова появилась архивная сущность"
                    if archived
                    else "Данные отличаются от сохранённой версии"
                ),
                message=(
                    "Запись не будет автоматически возвращена в активное планирование."
                    if archived
                    else "Существующая запись не будет автоматически заменена данными из Excel."
                ),
                entity_type=labels.get(label, "dataset"),
                entity_id=entity_id,
                details=detail_lines or (warning,),
            )
        )
    return result


def build_data_preflight(
    parsed: ParsedDataset,
    normalized: NormalizedDataset,
    validation: ValidationReport,
    merge_preview: CatalogMergeResult,
) -> dict[str, Any]:
    """Return deterministic, Russian, UI-safe findings for one parsed upload."""

    tables = normalized.tables
    tasks = tables["tasks.csv"]
    effort = tables["task_role_effort.csv"]
    dependencies = tables["task_dependencies.csv"]
    engineers = tables["engineers.csv"]
    skills = tables["engineer_skills.csv"]
    history = tables["team_history.csv"]
    findings: list[dict[str, Any]] = []

    task_rows = _source_rows(parsed.tasks, 1)
    dependency_rows: dict[tuple[str, str], list[int]] = defaultdict(list)
    for offset, values in enumerate(parsed.dependencies.rows, start=1):
        dependency_rows[(_text(values[0]), _text(values[1]))].append(
            parsed.dependencies.header_row + offset
        )
    engineer_rows = _source_rows(parsed.engineers, 0)
    history_rows = _source_rows(parsed.team_history, 1)

    task_ids = [_text(row.get("task_id")) for row in tasks if _text(row.get("task_id"))]
    task_by_id = {_text(row.get("task_id")): row for row in tasks if _text(row.get("task_id"))}
    task_id_set = set(task_by_id)
    duplicate_tasks = {value for value, count in Counter(task_ids).items() if count > 1}
    for task_id in sorted(duplicate_tasks):
        rows = task_rows.get(task_id, [])
        findings.append(_issue(
            code="DUPLICATE_TASK_ID", severity="critical", category="duplicates",
            title="Одна и та же задача встречается несколько раз",
            message="Оставьте одну однозначную запись задачи в таблице Tasks.",
            entity_type="task", entity_id=task_id, sheet="Tasks",
            row=rows[0] if rows else None,
            details=(("Строки: " + ", ".join(map(str, rows))) if rows else "" ,),
        ))

    known_statuses = {"todo", "inprogress", "done"}
    for task in tasks:
        task_id = _text(task.get("task_id"))
        if not task_id or task.get("is_placeholder"):
            continue
        row = (task_rows.get(task_id) or [None])[0]
        status = _text(task.get("status"))
        if not status or status.casefold() not in known_statuses:
            findings.append(_issue(
                code="UNKNOWN_TASK_STATUS", severity="critical", category="tasks",
                title="Не удалось определить статус задачи",
                message="Укажите один из поддерживаемых статусов: ToDo, InProgress или Done.",
                entity_type="task", entity_id=task_id, sheet="Tasks", row=row,
                details=((f"Указано: {status}" if status else "Статус не указан"),),
            ))
        if _is_done(task):
            continue
        if not _text(task.get("team_id")):
            findings.append(_issue(
                code="TASK_TEAM_MISSING", severity="warning", category="tasks",
                title="У задачи не указана команда",
                message="Планировщик попробует подобрать команду, но результат может отличаться от ожидаемого.",
                entity_type="task", entity_id=task_id, sheet="Tasks", row=row,
            ))
        if task.get("rung") is None:
            findings.append(_issue(
                code="TASK_RUNG_MISSING", severity="warning", category="tasks",
                title="У задачи не указан приоритет Rung",
                message="Задача не сможет корректно участвовать в сравнении приоритетов.",
                entity_type="task", entity_id=task_id, sheet="Tasks", row=row,
            ))
        elif not is_number(task.get("rung")):
            findings.append(_issue(
                code="INVALID_TASK_RUNG", severity="critical", category="tasks",
                title="Некорректно указан приоритет Rung",
                message="Укажите числовое значение приоритета задачи.",
                entity_type="task", entity_id=task_id, sheet="Tasks", row=row,
                details=(f"Указано: {task.get('rung')!r}",),
            ))
        story_points = task.get("story_points")
        if not is_number(story_points) or float(story_points) < 0:
            findings.append(_issue(
                code="INVALID_STORY_POINTS", severity="critical", category="tasks",
                title="Некорректное количество Story Points",
                message="Укажите неотрицательное числовое значение Story Points.",
                entity_type="task", entity_id=task_id, sheet="Tasks", row=row,
                details=(f"Указано: {story_points!r}",),
            ))

    placeholder_ids = {
        _text(task.get("task_id"))
        for task in tasks
        if task.get("is_placeholder") and _text(task.get("task_id"))
    }
    for task_id in sorted(placeholder_ids):
        sources = normalized.placeholder_sources.get(task_id, ())
        dependency = next(
            (row for row in dependencies if task_id in {
                _text(row.get("blocking_task_id")), _text(row.get("blocked_task_id"))
            }),
            None,
        )
        is_dependency_reference = "Task_dependencies" in sources
        blocking = _text(dependency.get("blocking_task_id")) if dependency else ""
        blocked = _text(dependency.get("blocked_task_id")) if dependency else ""
        missing_blocker = is_dependency_reference and task_id == blocking
        findings.append(_issue(
            code="UNKNOWN_DEPENDENCY_TASK" if is_dependency_reference else "MISSING_TASK_RECORD",
            severity="critical" if is_dependency_reference else "warning",
            category="dependencies" if is_dependency_reference else "tasks",
            title=(
                f"Не найдена блокирующая задача {task_id}"
                if missing_blocker else
                (
                    f"Не найдена зависимая задача {task_id}"
                    if is_dependency_reference else "Не найдена исходная запись задачи"
                )
            ),
            message=(
                (
                    f"Задача {blocked} зависит от {blocking}, но {task_id} отсутствует в данных."
                    if dependency else
                    f"Задача {task_id} указана в зависимости, но отсутствует в таблице Tasks."
                )
                if is_dependency_reference
                else f"Для {task_id} есть трудозатраты, но нет строки в таблице Tasks."
            ),
            entity_type="task", entity_id=task_id,
            sheet="Task_dependencies" if is_dependency_reference else "Калькулятор сметы",
            row=(
                (dependency_rows.get((blocking, blocked)) or [None])[0]
                if dependency else None
            ),
            details=(
                *((
                    f"Блокирующая задача: {blocking}",
                    f"Зависимая задача: {blocked}",
                ) if dependency else ()),
                "Исправьте идентификатор или добавьте задачу в таблицу Tasks.",
            ),
        ))

    dependency_counts = Counter(
        (
            _text(row.get("blocking_task_id")),
            _text(row.get("blocked_task_id")),
            _text(row.get("dependency_type")),
        )
        for row in dependencies
    )
    for (blocking, blocked, _kind), count in sorted(dependency_counts.items()):
        rows = dependency_rows.get((blocking, blocked), [])
        row = rows[0] if rows else None
        if blocking and blocking == blocked:
            findings.append(_issue(
                code="SELF_DEPENDENCY", severity="critical", category="dependencies",
                title="Задача зависит сама от себя",
                message="Такую зависимость невозможно корректно запланировать.",
                entity_type="task", entity_id=blocked, sheet="Task_dependencies", row=row,
            ))
        if count > 1:
            findings.append(_issue(
                code="DUPLICATE_DEPENDENCY", severity="warning", category="dependencies",
                title="Связь между задачами указана несколько раз",
                message=f"Связь {blocking} → {blocked} повторяется {count} раза.",
                entity_type="task", entity_id=blocked, sheet="Task_dependencies", row=row,
                details=(("Строки: " + ", ".join(map(str, rows))) if rows else "",),
            ))

    edges = [
        (_text(row.get("blocking_task_id")), _text(row.get("blocked_task_id")))
        for row in dependencies
        if _text(row.get("blocking_task_id")) in task_id_set
        and _text(row.get("blocked_task_id")) in task_id_set
        and _text(row.get("blocking_task_id")) != _text(row.get("blocked_task_id"))
    ]
    for component in strongly_connected_components(edges):
        path = " → ".join([*component, component[0]])
        findings.append(_issue(
            code="DEPENDENCY_CYCLE", severity="critical", category="dependencies",
            title="Обнаружен цикл зависимостей",
            message="Эти задачи невозможно корректно упорядочить.",
            entity_type="task", entity_id=component[0], sheet="Task_dependencies",
            details=(path,),
        ))

    history_teams = {_text(row.get("team_id")) for row in history if _text(row.get("team_id"))}
    engineer_teams = {
        _text(row.get("team_id")) for row in engineers if _text(row.get("team_id"))
    }
    known_team_ids = history_teams | engineer_teams
    active_tasks = [task for task in tasks if not task.get("is_placeholder") and not _is_done(task)]
    for task in active_tasks:
        task_id = _text(task.get("task_id"))
        team_id = _text(task.get("team_id"))
        if team_id and team_id not in known_team_ids:
            findings.append(_issue(
                code="UNKNOWN_TASK_TEAM", severity="critical", category="teams",
                title="Не найдена команда",
                message=f"Для задачи указана неизвестная команда {team_id}.",
                entity_type="task", entity_id=task_id, sheet="Tasks",
                row=(task_rows.get(task_id) or [None])[0], details=(f"Команда: {team_id}",),
            ))

    engineer_ids = {_text(row.get("engineer_id")) for row in engineers if _text(row.get("engineer_id"))}
    membership_counts = Counter(
        (_text(row.get("engineer_id")), _text(row.get("team_id")), _text(row.get("role")))
        for row in engineers
    )
    rates_by_engineer_team: dict[str, dict[str, float]] = defaultdict(dict)
    for engineer in engineers:
        engineer_id = _text(engineer.get("engineer_id"))
        team_id = _text(engineer.get("team_id"))
        row = (engineer_rows.get(engineer_id) or [None])[0]
        if not team_id:
            findings.append(_issue(
                code="ENGINEER_WITHOUT_TEAM", severity="warning", category="engineers",
                title="Сотрудник не привязан ни к одной команде",
                message="Он не сможет участвовать в автоматическом планировании.",
                entity_type="engineer", entity_id=engineer_id, sheet="Engineers_profiles", row=row,
            ))
        rate = engineer.get("capacity_rate")
        if not is_number(rate) or not 0 < float(rate) <= 1:
            findings.append(_issue(
                code="INVALID_CAPACITY_RATE", severity="critical", category="engineers",
                title="Некорректно указана занятость сотрудника",
                message="Значение должно быть больше 0 и не превышать 1.0 ставки.",
                entity_type="engineer", entity_id=engineer_id, sheet="Engineers_profiles", row=row,
                details=(f"Указано: {rate!r}",),
            ))
        elif team_id:
            rates_by_engineer_team[engineer_id][team_id] = min(
                float(rate), rates_by_engineer_team[engineer_id].get(team_id, float(rate))
            )
    for (engineer_id, team_id, role), count in sorted(membership_counts.items()):
        if engineer_id and team_id and role and count > 1:
            findings.append(_issue(
                code="DUPLICATE_MEMBERSHIP", severity="warning", category="duplicates",
                title="Сотрудник несколько раз указан в одной команде",
                message="Проверьте повторяющиеся строки профиля и доступность сотрудника.",
                entity_type="engineer", entity_id=engineer_id, sheet="Engineers_profiles",
                row=(engineer_rows.get(engineer_id) or [None])[0], details=(f"Команда: {team_id}", f"Роль: {role}"),
            ))
    for engineer_id, team_rates in sorted(rates_by_engineer_team.items()):
        if sum(team_rates.values()) > 1 + 1e-9:
            findings.append(_issue(
                code="ENGINEER_TOTAL_CAPACITY_ABOVE_ONE", severity="critical", category="engineers",
                title="Суммарная занятость сотрудника превышает полную ставку",
                message="Уменьшите Capacity Rate между командами так, чтобы сумма не превышала 1.0.",
                entity_type="engineer", entity_id=engineer_id, sheet="Engineers_profiles",
                row=(engineer_rows.get(engineer_id) or [None])[0],
                details=(f"Суммарно: {sum(team_rates.values()):g}",),
            ))

    available_roles = {_text(row.get("role")) for row in engineers if _text(row.get("role"))}
    missing_roles: dict[str, set[str]] = defaultdict(set)
    for row in effort:
        task_id = _text(row.get("task_id"))
        role = _text(row.get("role"))
        hours = row.get("required_hours")
        if not task_id or not role or not is_number(hours) or float(hours) < 0:
            if task_id:
                findings.append(_issue(
                    code="INVALID_ROLE_EFFORT", severity="critical", category="effort",
                    title="Некорректно указаны трудозатраты по роли",
                    message="Укажите роль и неотрицательное числовое количество часов.",
                    entity_type="task", entity_id=task_id, sheet="Калькулятор сметы",
                ))
            continue
        if float(hours) > 0 and task_id in task_by_id and not _is_done(task_by_id[task_id]) and role not in available_roles:
            missing_roles[task_id].add(role)
    for task_id, roles in sorted(missing_roles.items()):
        findings.append(_issue(
            code="MISSING_SPECIALISTS_FOR_ROLES", severity="warning", category="effort",
            title="Для задачи не хватает специалистов",
            message="Без этих ролей задача не сможет попасть в квартальный план.",
            entity_type="task", entity_id=task_id, sheet="Калькулятор сметы",
            details=tuple(f"Нужная роль: {role}" for role in sorted(roles)),
        ))

    active_team_ids = {
        _text(row.get("team_id"))
        for row in [*active_tasks, *engineers]
        if _text(row.get("team_id"))
    }
    history_by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    history_keys: Counter[tuple[str, str]] = Counter()
    for row in history:
        team_id = _text(row.get("team_id"))
        snapshot_date = _text(row.get("snapshot_date"))
        history_by_team[team_id].append(row)
        history_keys[(team_id, snapshot_date)] += 1
        velocity = row.get("velocity_achieved")
        if not is_number(velocity) or float(velocity) < 0:
            findings.append(_issue(
                code="INVALID_TEAM_VELOCITY", severity="critical", category="history",
                title="Некорректно указана производительность команды",
                message="Укажите неотрицательную фактическую производительность в Story Points.",
                entity_type="team", entity_id=team_id, sheet="Team_history",
                row=(history_rows.get(team_id) or [None])[0], details=(f"Указано: {velocity!r}",),
            ))
        planned_sp = row.get("planned_sp")
        if not is_number(planned_sp) or float(planned_sp) < 0:
            findings.append(_issue(
                code="INVALID_HISTORY_PLANNED_SP", severity="warning", category="history",
                title="Некорректно указан исторический план команды",
                message="Эта строка истории не сможет корректно участвовать в расчёте KPI.",
                entity_type="team", entity_id=team_id, sheet="Team_history",
                row=(history_rows.get(team_id) or [None])[0],
                details=(f"Указано: {planned_sp!r}",),
            ))
    for (team_id, snapshot_date), count in sorted(history_keys.items()):
        if team_id and snapshot_date and count > 1:
            findings.append(_issue(
                code="DUPLICATE_TEAM_HISTORY", severity="warning", category="duplicates",
                title="История команды содержит повторяющийся снимок",
                message="Повтор может исказить среднюю производительность команды.",
                entity_type="team", entity_id=team_id, sheet="Team_history",
                row=(history_rows.get(team_id) or [None])[0],
                details=(f"Дата снимка: {snapshot_date}", f"Повторов: {count}"),
            ))
    for team_id in sorted(active_team_ids - set(history_by_team)):
        findings.append(_issue(
            code="TEAM_HISTORY_MISSING", severity="warning", category="history",
            title="Для команды нет истории производительности",
            message="Не удалось надёжно рассчитать доступную ёмкость в Story Points.",
            entity_type="team", entity_id=team_id, sheet="Team_history",
        ))

    skills_by_engineer: dict[str, set[str]] = defaultdict(set)
    for skill in skills:
        engineer_id = _text(skill.get("engineer_id"))
        technology = _text(skill.get("technology"))
        if engineer_id not in engineer_ids:
            findings.append(_issue(
                code="SKILL_UNKNOWN_ENGINEER", severity="critical", category="skills",
                title="Навык относится к неизвестному сотруднику",
                message="Исправьте идентификатор сотрудника или добавьте его профиль.",
                entity_type="engineer", entity_id=engineer_id, sheet="Engineers_profiles",
                details=(f"Навык: {technology}",),
            ))
        elif technology:
            skills_by_engineer[engineer_id].add(technology)
    for engineer_id in sorted(engineer_ids - set(skills_by_engineer)):
        findings.append(_issue(
            code="ENGINEER_SKILLS_MISSING", severity="info", category="skills",
            title="Для сотрудника не указаны навыки",
            message="Планирование доступно, но данные Звёздной карты будут неполными.",
            entity_type="engineer", entity_id=engineer_id, sheet="Engineers_profiles",
            row=(engineer_rows.get(engineer_id) or [None])[0],
        ))

    findings.extend(_catalog_conflict_issues(merge_preview))

    # Preserve one clear blocking message for validation failures not already
    # represented by a concrete structured rule.  Raw validator text remains
    # server-side and is never exposed to the browser.
    if validation.errors and not any(item["severity"] == "critical" for item in findings):
        findings.append(_issue(
            code="STRUCTURAL_VALIDATION_ERROR", severity="critical", category="structure",
            title="В файле есть некорректные значения",
            message="Проверьте обязательные таблицы и значения, затем загрузите исправленный файл.",
        ))

    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in findings:
        key = (item["code"], item["entity_type"], item["entity_id"], item["sheet"], item["row"])
        unique.setdefault(key, item)
    ordered = sorted(
        unique.values(),
        key=lambda item: (
            CATEGORY_ORDER[item["category"]], SEVERITY_ORDER[item["severity"]],
            item["entity_id"] or "", item["row"] or 0, item["code"],
        ),
    )
    counts = {
        severity: sum(item["severity"] == severity for item in ordered)
        for severity in ("critical", "warning", "info")
    }
    groups = []
    for category in CATEGORY_TITLES:
        items = [item for item in ordered if item["category"] == category]
        if not items:
            continue
        groups.append({
            "category": category,
            "title": CATEGORY_TITLES[category],
            "count": len(items),
            "counts": {
                severity: sum(item["severity"] == severity for item in items)
                for severity in counts
            },
            "items": items,
        })
    return {
        "has_critical_errors": counts["critical"] > 0,
        "can_continue": counts["critical"] == 0,
        "show_dialog": sum(counts.values()) > 0,
        "summary": counts,
        "groups": groups,
    }


def structural_preflight(message: str) -> dict[str, Any]:
    issue = _issue(
        code="UNREADABLE_EXCEL", severity="critical", category="structure",
        title="Не удалось прочитать данные Excel",
        message=message,
    )
    return {
        "has_critical_errors": True,
        "can_continue": False,
        "show_dialog": True,
        "summary": {"critical": 1, "warning": 0, "info": 0},
        "groups": [{
            "category": "structure", "title": CATEGORY_TITLES["structure"],
            "count": 1, "counts": {"critical": 1, "warning": 0, "info": 0},
            "items": [issue],
        }],
    }
