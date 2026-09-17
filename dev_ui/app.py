"""Temporary Streamlit UI for inspecting normalized development data."""

from __future__ import annotations

import json
import sys

import pandas as pd
import streamlit as st

from data_access import (
    PROJECT_ROOT,
    TABLE_FILES,
    boolean_mask,
    count_unique_engineers,
    derive_data_quality_issues,
    list_processed_files,
    load_csv,
    load_known_tables,
    unique_engineer_counts,
)
from translations import (
    APP_STATUS_LABELS,
    COLUMN_LABELS,
    ISSUE_TYPE_LABELS,
    PLACEMENT_OUTCOME_LABELS,
    REASON_TITLES,
    RESULT_LABELS,
    SEVERITY_LABELS,
    SHIFT_DIRECTION_LABELS,
    SKILL_DATA_LIMITATION_RU,
    reason_details_ru,
)
from upload_flow import process_uploaded_workbook

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.planning.dependency_rules import RAW_DIRECTION_DESCRIPTION
from src.planning.feasibility import FeasibilityContext, FeasibilityService
from src.planning.models import SprintState
from src.planning.planner import build_quarter_plan


st.set_page_config(page_title="Просмотр processed-данных", layout="wide")


def show_load_errors(errors: list[str]) -> None:
    for error in errors:
        st.warning(error)


def localized_display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Translate display-only values without changing source DataFrames."""

    display = frame.copy()
    if "result" in display.columns:
        display["result"] = display["result"].map(RESULT_LABELS).fillna(display["result"])
    if "severity" in display.columns:
        display["severity"] = display["severity"].map(SEVERITY_LABELS).fillna(
            display["severity"]
        )
    if "issue_type" in display.columns:
        display["issue_type"] = display["issue_type"].map(ISSUE_TYPE_LABELS).fillna(
            display["issue_type"]
        )
    if "plan_status" in display.columns:
        display["plan_status"] = display["plan_status"].map(APP_STATUS_LABELS).fillna(
            display["plan_status"]
        )
    if "outcome" in display.columns:
        display["outcome"] = display["outcome"].map(PLACEMENT_OUTCOME_LABELS).fillna(
            display["outcome"]
        )
    if "cross_team" in display.columns:
        display["cross_team"] = display["cross_team"].map(
            {True: "Из другой команды", False: "Своя команда"}
        ).fillna(display["cross_team"])
    for column in ("start_shift_direction", "end_shift_direction"):
        if column in display.columns:
            display[column] = display[column].map(SHIFT_DIRECTION_LABELS).fillna(
                display[column]
            )
    return display


def show_dataframe(frame: pd.DataFrame, *, hide_index: bool = True) -> None:
    display = localized_display_frame(frame)
    column_config = {
        column: COLUMN_LABELS[column]
        for column in display.columns
        if column in COLUMN_LABELS
    }
    st.dataframe(
        display,
        width="stretch",
        hide_index=hide_index,
        column_config=column_config,
    )


def nonblank_options(frame: pd.DataFrame, column: str) -> list[str]:
    if column not in frame.columns:
        return []
    return sorted(
        {
            str(value).strip()
            for value in frame[column]
            if pd.notna(value) and str(value).strip()
        }
    )


def multiselect_filter(frame: pd.DataFrame, column: str, label: str, key: str) -> pd.DataFrame:
    options = nonblank_options(frame, column)
    if not options:
        return frame
    selected = st.multiselect(label, options, key=key)
    if selected:
        return frame[frame[column].astype(str).isin(selected)]
    return frame


def count_unique(frame: pd.DataFrame | None, column: str) -> int:
    if frame is None or column not in frame.columns:
        return 0
    return len(nonblank_options(frame, column))


def overview_page(tables: dict[str, pd.DataFrame], errors: list[str]) -> None:
    st.header("Обзор")
    show_load_errors(errors)
    tasks = tables.get("tasks", pd.DataFrame())
    engineers = tables.get("engineers", pd.DataFrame())
    skills = tables.get("engineer_skills", pd.DataFrame())
    dependencies = tables.get("task_dependencies", pd.DataFrame())
    effort = tables.get("task_role_effort", pd.DataFrame())
    history = tables.get("team_history", pd.DataFrame())
    quality = derive_data_quality_issues(tables)

    placeholders = (
        int(boolean_mask(tasks["is_placeholder"]).sum())
        if "is_placeholder" in tasks.columns
        else 0
    )
    teams = set(nonblank_options(tasks, "team_id")) | set(nonblank_options(engineers, "team_id"))
    metrics = [
        ("Задачи", len(tasks)),
        ("Задачи-заглушки", placeholders),
        ("Инициативы / PRODF", count_unique(tasks, "initiative_id")),
        ("Команды", len(teams)),
        ("Инженеры", count_unique_engineers(engineers)),
        ("Технологии / компетенции", count_unique(skills, "technology")),
        ("Зависимости", len(dependencies)),
        ("Строки трудозатрат", len(effort)),
        ("Записи истории команд", len(history)),
        ("Проблемы качества данных", len(quality)),
    ]
    for start in range(0, len(metrics), 5):
        columns = st.columns(5)
        for column, (label, value) in zip(columns, metrics[start : start + 5]):
            column.metric(label, value)

    st.subheader("Доступные processed-файлы")
    files = list_processed_files()
    if files.empty:
        st.warning("В data/processed нет файлов.")
    else:
        show_dataframe(files)


def tasks_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Задачи")
    tasks = tables.get("tasks")
    if tasks is None:
        st.warning(f"Нет processed-файла: {TABLE_FILES['tasks']}")
        return

    filtered = tasks.copy()
    filter_columns = st.columns(5)
    task_query = filter_columns[0].text_input("Поиск по task_id", key="tasks_id")
    with filter_columns[1]:
        filtered = multiselect_filter(filtered, "initiative_id", "PRODF / инициатива", "tasks_init")
    with filter_columns[2]:
        filtered = multiselect_filter(filtered, "team_id", "Команда", "tasks_team")
    with filter_columns[3]:
        filtered = multiselect_filter(filtered, "status", "Статус из датасета", "tasks_status")
    placeholders_only = filter_columns[4].checkbox("Показывать только задачи-заглушки")
    if task_query and "task_id" in filtered.columns:
        filtered = filtered[
            filtered["task_id"].astype(str).str.contains(task_query, case=False, na=False)
        ]
    if placeholders_only:
        if "is_placeholder" in filtered.columns:
            filtered = filtered[boolean_mask(filtered["is_placeholder"])]
        else:
            st.warning("В tasks.csv нет колонки is_placeholder.")

    display = filtered.copy()
    if "is_placeholder" in display.columns:
        display.insert(
            0,
            "record_type",
            boolean_mask(display["is_placeholder"]).map(
                {True: "⚠ Задача-заглушка", False: "Задача"}
            ),
        )
    st.caption(f"Показано задач: {len(display)} из {len(tasks)}.")
    show_dataframe(display)


def effort_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Трудозатраты по ролям")
    effort = tables.get("task_role_effort")
    if effort is None:
        st.warning(f"Нет processed-файла: {TABLE_FILES['task_role_effort']}")
        return
    filtered = effort.copy()
    left, right = st.columns(2)
    with left:
        filtered = multiselect_filter(filtered, "task_id", "Задача", "effort_task")
    with right:
        filtered = multiselect_filter(filtered, "role", "Роль", "effort_role")
    show_dataframe(filtered)

    if "required_hours" not in effort.columns:
        st.warning("В task_role_effort.csv нет колонки required_hours.")
        return
    numeric = effort.copy()
    numeric["required_hours"] = pd.to_numeric(numeric["required_hours"], errors="coerce")
    by_role, by_task = st.columns(2)
    with by_role:
        st.subheader("Трудозатраты по ролям")
        if "role" in numeric.columns:
            aggregate = (
                numeric.groupby("role", dropna=False)["required_hours"]
                .sum(min_count=1)
                .sort_values(ascending=False)
                .rename("Требуется часов")
            )
            aggregate.index.name = "Роль"
            st.bar_chart(aggregate)
            show_dataframe(aggregate.reset_index())
    with by_task:
        st.subheader("Трудозатраты по задачам")
        if "task_id" in numeric.columns:
            aggregate = (
                numeric.groupby("task_id", dropna=False)["required_hours"]
                .sum(min_count=1)
                .sort_values(ascending=False)
                .rename("Требуется часов")
            )
            st.bar_chart(aggregate)
            show_dataframe(aggregate.reset_index())


def enriched_dependencies(tables: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    dependencies = tables.get("task_dependencies")
    tasks = tables.get("tasks")
    if dependencies is None:
        return None
    result = dependencies.copy()
    task_ids: set[str] = set()
    placeholder_ids: set[str] = set()
    if tasks is not None and "task_id" in tasks.columns:
        task_ids = set(tasks["task_id"].dropna().astype(str))
        if "is_placeholder" in tasks.columns:
            placeholder_ids = set(
                tasks.loc[boolean_mask(tasks["is_placeholder"]), "task_id"].dropna().astype(str)
            )
    for side in ("blocking", "blocked"):
        identifier = f"{side}_task_id"
        if identifier in result.columns:
            values = result[identifier].astype(str)
            result[f"{side}_exists"] = values.isin(task_ids)
            result[f"{side}_is_placeholder"] = values.isin(placeholder_ids)
    placeholder_columns = [
        column for column in ("blocking_is_placeholder", "blocked_is_placeholder")
        if column in result.columns
    ]
    result.insert(
        0,
        "has_placeholder",
        result[placeholder_columns].any(axis=1) if placeholder_columns else False,
    )
    return result


def dependency_graph(frame: pd.DataFrame, placeholder_ids: set[str]) -> str:
    lines = ["digraph dependencies {", "rankdir=LR;", "node [shape=box];"]
    node_ids = set(frame.get("blocking_task_id", pd.Series(dtype=str)).dropna().astype(str))
    node_ids.update(frame.get("blocked_task_id", pd.Series(dtype=str)).dropna().astype(str))
    for task_id in sorted(node_ids):
        attributes = "style=filled, fillcolor=orange" if task_id in placeholder_ids else ""
        suffix = f" [{attributes}]" if attributes else ""
        lines.append(f"{json.dumps(task_id)}{suffix};")
    required = {"blocking_task_id", "blocked_task_id"}
    if required.issubset(frame.columns):
        for _, row in frame.iterrows():
            if pd.isna(row["blocking_task_id"]) or pd.isna(row["blocked_task_id"]):
                continue
            label = "" if pd.isna(row.get("dependency_type")) else str(row["dependency_type"])
            lines.append(
                f"{json.dumps(str(row['blocking_task_id']))} -> "
                f"{json.dumps(str(row['blocked_task_id']))} "
                f"[label={json.dumps(label)}];"
            )
    lines.append("}")
    return "\n".join(lines)


def dependencies_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Зависимости")
    st.info(
        "Стрелки следуют исходным колонкам blocking_task_id → blocked_task_id. "
        "Это временное правило, а не подтверждённая бизнес-интерпретация dependency_type."
    )
    dependencies = enriched_dependencies(tables)
    if dependencies is None:
        st.warning(f"Нет processed-файла: {TABLE_FILES['task_dependencies']}")
        return
    suspicious_only = st.checkbox("Показывать только зависимости с задачами-заглушками")
    shown = dependencies[dependencies["has_placeholder"]] if suspicious_only else dependencies
    suspicious_count = int(dependencies["has_placeholder"].sum())
    if suspicious_count:
        st.warning(
            f"Зависимостей с хотя бы одной задачей-заглушкой: {suspicious_count}."
        )
    show_dataframe(shown)

    if st.checkbox("Показать простой граф исходных зависимостей"):
        tasks = tables.get("tasks", pd.DataFrame())
        placeholder_ids = set()
        if {"task_id", "is_placeholder"}.issubset(tasks.columns):
            placeholder_ids = set(
                tasks.loc[boolean_mask(tasks["is_placeholder"]), "task_id"].dropna().astype(str)
            )
        st.caption(
            "Оранжевые узлы — задачи-заглушки. Направление рёбер повторяет исходные колонки."
        )
        st.graphviz_chart(dependency_graph(shown, placeholder_ids), width="stretch")


def engineers_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Инженеры и компетенции")
    engineers = tables.get("engineers")
    skills = tables.get("engineer_skills")
    engineers_tab, skills_tab, aggregates_tab = st.tabs(
        ["Инженеры", "Компетенции", "Сводные данные"]
    )

    with engineers_tab:
        if engineers is None:
            st.warning(f"Нет processed-файла: {TABLE_FILES['engineers']}")
        else:
            filtered = engineers.copy()
            columns = st.columns(3)
            with columns[0]:
                filtered = multiselect_filter(filtered, "team_id", "Команда", "engineer_team")
            with columns[1]:
                filtered = multiselect_filter(filtered, "role", "Роль", "engineer_role")
            with columns[2]:
                filtered = multiselect_filter(filtered, "grade", "Грейд", "engineer_grade")
            if "capacity_rate" in filtered.columns:
                filtered["available_hours"] = (
                    pd.to_numeric(filtered["capacity_rate"], errors="coerce") * 80
                )
            else:
                st.warning("В engineers.csv нет колонки capacity_rate.")
            st.caption(
                "Диагностическая формула: доступные часы = 80 × capacity_rate."
            )
            show_dataframe(filtered)

    with skills_tab:
        if skills is None:
            st.warning(f"Нет processed-файла: {TABLE_FILES['engineer_skills']}")
        else:
            engineer_options = nonblank_options(skills, "engineer_id")
            selected = st.selectbox("Инженер", ["Все инженеры", *engineer_options])
            shown = skills
            if selected != "Все инженеры" and "engineer_id" in skills.columns:
                shown = skills[skills["engineer_id"].astype(str) == selected]
            show_dataframe(shown)

    with aggregates_tab:
        if engineers is not None:
            left, right = st.columns(2)
            with left:
                st.subheader("Инженеры по ролям")
                if "role" in engineers.columns:
                    role_counts = unique_engineer_counts(
                        engineers, "role", label="Количество инженеров"
                    )
                    role_counts.index.name = "Роль"
                    st.bar_chart(role_counts)
            with right:
                st.subheader("Специалисты по командам")
                if "team_id" in engineers.columns:
                    team_counts = unique_engineer_counts(
                        engineers, "team_id", label="Количество специалистов"
                    )
                    team_counts.index.name = "Команда"
                    st.bar_chart(team_counts)
        if skills is not None and {"technology", "engineer_id"}.issubset(skills.columns):
            st.subheader("Инженеры по технологиям")
            technology_counts = (
                skills.groupby("technology")["engineer_id"]
                .nunique()
                .sort_values(ascending=False)
                .rename("Количество инженеров")
            )
            technology_counts.index.name = "Технология / компетенция"
            st.bar_chart(technology_counts)
            show_dataframe(technology_counts.reset_index())


def team_history_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("История производительности команд")
    history = tables.get("team_history")
    if history is None:
        st.warning(f"Нет processed-файла: {TABLE_FILES['team_history']}")
        return
    show_dataframe(history)
    required = {"team_id", "velocity_achieved"}
    if not required.issubset(history.columns):
        st.warning("В team_history.csv нет колонки team_id или velocity_achieved.")
        return
    numeric = history.copy()
    numeric["velocity_achieved"] = pd.to_numeric(numeric["velocity_achieved"], errors="coerce")
    aggregate = (
        numeric.groupby("team_id", dropna=False)["velocity_achieved"]
        .mean()
        .rename("average_historical_velocity")
        .reset_index()
    )
    aggregate["calculated_capacity"] = aggregate["average_historical_velocity"] * 0.8
    st.subheader("Диагностика исторической производительности")
    st.caption(
        "Расчётная ёмкость = средняя историческая производительность × 0.8. "
        "Это диагностическое отображение формулы из ТЗ, а не результат планировщика."
    )
    show_dataframe(aggregate)
    chart = aggregate.set_index("team_id")[["average_historical_velocity", "calculated_capacity"]]
    st.bar_chart(
        chart.rename(
            columns={
                "average_historical_velocity": "Средняя производительность, SP",
                "calculated_capacity": "Расчётная ёмкость, SP",
            }
        )
    )


def data_quality_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Качество данных")
    persisted = load_csv("data_quality_issues.csv")
    if persisted.frame is not None:
        issues = persisted.frame
        st.caption("Данные загружены из data/processed/data_quality_issues.csv.")
    else:
        issues = derive_data_quality_issues(tables)
        st.info(
            "Preprocessing пока не сохраняет предупреждения в отдельный файл. "
            "Таблица собирает в памяти проблемы задач-заглушек, ссылок и числовых полей "
            "из processed CSV. На диск ничего не записывается."
        )
    if issues.empty:
        st.success("В доступных processed-таблицах проблем не обнаружено.")
        return
    filtered = issues.copy()
    filter_columns = st.columns(4)
    for container, column, label in zip(
        filter_columns,
        ("severity", "issue_type", "entity_id", "source"),
        ("Уровень", "Тип проблемы", "Идентификатор", "Источник"),
    ):
        with container:
            filtered = multiselect_filter(filtered, column, label, f"quality_{column}")
    show_dataframe(filtered)


def upload_page(_: dict[str, pd.DataFrame]) -> None:
    st.header("Загрузка датасета")
    st.caption(
        "Excel-файл обрабатывается во временной папке: preprocessing → validation → "
        "data audit. Исходные файлы проекта не изменяются."
    )
    uploaded = st.file_uploader("Файл датасета", type=["xlsx", "xlsm"])
    if uploaded is not None and st.button("Проверить датасет", type="primary"):
        with st.spinner("Проверяем структуру и качество данных…"):
            result = process_uploaded_workbook(uploaded.name, uploaded.getvalue())
        st.session_state["uploaded_tables"] = result.tables
        st.session_state["uploaded_audit"] = result.audit

    audit = st.session_state.get("uploaded_audit")
    if audit is None:
        st.info("Загрузите Excel-файл, чтобы проверить его перед планированием.")
        return

    if audit.status == "ACCEPTED":
        st.success("Датасет принят")
    elif audit.status == "ACCEPTED_WITH_WARNINGS":
        st.warning("Датасет принят с замечаниями. Проблемные задачи исключаются отдельно.")
    else:
        st.error("Планирование невозможно: структура датасета непригодна.")

    counts = audit.counts
    metric_columns = st.columns(3)
    for column, severity, label in zip(
        metric_columns,
        ("BLOCKING", "WARNING", "INFO"),
        ("Блокирующие сущности", "Предупреждения", "Информация"),
    ):
        column.metric(label, counts[severity])

    issue_rows = [issue.to_dict() for issue in audit.issues]
    if issue_rows:
        show_dataframe(pd.DataFrame(issue_rows))
    else:
        st.success("Проблем качества данных не обнаружено.")
    if audit.planning_allowed:
        st.info("Квартальный план можно строить по валидным задачам.")
    else:
        st.error("Исправьте структурную ошибку перед запуском planner.")


def feasibility_context_from_tables(
    tables: dict[str, pd.DataFrame],
) -> FeasibilityContext | None:
    required = (
        "tasks",
        "task_role_effort",
        "task_dependencies",
        "engineers",
        "engineer_skills",
        "team_history",
    )
    missing = [TABLE_FILES[name] for name in required if name not in tables]
    if missing:
        for filename in missing:
            st.warning(f"Нет processed-файла: {filename}")
        return None
    return FeasibilityContext.from_records(
        tasks=tables["tasks"].to_dict("records"),
        task_role_effort=tables["task_role_effort"].to_dict("records"),
        dependencies=tables["task_dependencies"].to_dict("records"),
        engineers=tables["engineers"].to_dict("records"),
        engineer_skills=tables["engineer_skills"].to_dict("records"),
        team_history=tables["team_history"].to_dict("records"),
    )


def feasibility_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Проверка выполнимости задачи")
    st.caption(
        "Временная проверка одной задачи. Тестовые значения хранятся только в текущей "
        "сессии интерфейса и не записываются в processed-данные."
    )
    context = feasibility_context_from_tables(tables)
    if context is None:
        return
    service = FeasibilityService(context)
    task_options = sorted(
        str(task_id)
        for task_id in tables["tasks"].get("task_id", pd.Series(dtype=str)).dropna().unique()
    )
    if not task_options:
        st.warning("В tasks.csv нет значений task_id.")
        return

    selection_columns = st.columns([3, 1])
    selected_task = selection_columns[0].selectbox(
        "Задача", task_options, key="feasibility_task"
    )
    sprint_number = selection_columns[1].number_input(
        "Спринт", min_value=1, max_value=6, value=1, step=1
    )
    task = service.task_record(selected_task) or {}
    task_fields = [
        field
        for field in (
            "task_id",
            "initiative_id",
            "team_id",
            "story_points",
            "status",
            "is_placeholder",
        )
        if field in task
    ]
    st.subheader("Задача")
    show_dataframe(pd.DataFrame([{field: task.get(field) for field in task_fields}]))

    team_id = task.get("team_id")
    team_label = str(team_id) if pd.notna(team_id) else "команда не указана"
    team_sp_used = st.number_input(
        f"Уже занято SP у команды — {team_label}",
        min_value=0.0,
        value=0.0,
        step=1.0,
    )

    st.subheader("Тестовая загрузка инженеров")
    candidate_ids = service.candidate_engineer_ids(selected_task)
    engineer_usage = pd.DataFrame(
        {"engineer_id": candidate_ids, "used_hours": [0.0] * len(candidate_ids)}
    )
    if engineer_usage.empty:
        st.info(
            "Нет инженеров нужной роли среди всех команд, либо данные задачи неполные."
        )
        edited_engineers = engineer_usage
    else:
        edited_engineers = st.data_editor(
            engineer_usage,
            hide_index=True,
            width="stretch",
            disabled=["engineer_id"],
            column_config={
                "used_hours": st.column_config.NumberColumn(
                    "Использовано часов", min_value=0.0, step=1.0
                )
            },
            key=f"feasibility_engineers_{selected_task}",
        )

    st.subheader("Тестовое состояние зависимостей")
    st.caption(
        "Текущее временное правило направления: "
        f"{RAW_DIRECTION_DESCRIPTION.replace('->', '→')}"
    )
    incoming = service.incoming_task_dependencies(selected_task)
    dependency_usage = pd.DataFrame(
        [
            {
                "blocking_task_id": row.get("blocking_task_id"),
                "dependency_type": row.get("dependency_type"),
                "completed_sprint": None,
            }
            for row in incoming
        ]
    )
    if dependency_usage.empty:
        st.info("У выбранной задачи нет входящих зависимостей.")
        edited_dependencies = dependency_usage
    else:
        edited_dependencies = st.data_editor(
            dependency_usage,
            hide_index=True,
            width="stretch",
            disabled=["blocking_task_id", "dependency_type"],
            column_config={
                "completed_sprint": st.column_config.NumberColumn(
                    "Спринт завершения", min_value=1, max_value=6, step=1
                )
            },
            key=f"feasibility_dependencies_{selected_task}",
        )

    if not st.button("Проверить выполнимость", type="primary"):
        return

    engineer_hours_used = {
        str(row["engineer_id"]): float(row["used_hours"])
        for _, row in edited_engineers.iterrows()
        if pd.notna(row.get("engineer_id")) and pd.notna(row.get("used_hours"))
    }
    completed_tasks = {
        str(row["blocking_task_id"]): int(row["completed_sprint"])
        for _, row in edited_dependencies.iterrows()
        if pd.notna(row.get("blocking_task_id")) and pd.notna(row.get("completed_sprint"))
    }
    team_usage = {str(team_id): float(team_sp_used)} if pd.notna(team_id) else {}
    state = SprintState(
        sprint_number=int(sprint_number),
        team_sp_used=team_usage,
        engineer_hours_used=engineer_hours_used,
        completed_tasks=completed_tasks,
    )
    result = service.check(selected_task, state)

    st.subheader("Ресурс команды")
    if result.team_capacity is None:
        st.warning("Нельзя рассчитать ресурс команды: не хватает данных о задаче.")
    else:
        capacity = result.team_capacity
        metrics = st.columns(6)
        for container, label, value in zip(
            metrics,
            (
                "Средняя историческая производительность",
                "Коэффициент фокуса",
                "Доступная ёмкость спринта",
                "Уже занято SP",
                "Доступно SP",
                "Требуется SP",
            ),
            (
                capacity.average_velocity_sp,
                capacity.focus_factor,
                capacity.capacity_sp,
                capacity.already_used_sp,
                capacity.available_sp,
                capacity.required_sp,
            ),
        ):
            container.metric(label, f"{value:g}")
        if capacity.result == "OK":
            st.success("Ресурса команды по Story Points достаточно")
        else:
            st.error(
                f"Не хватает ресурса команды по Story Points. Дефицит: "
                f"{capacity.deficit_sp:g} SP"
            )

    serialized = result.to_dict()
    st.subheader("Ресурс по ролям")
    if result.role_capacity:
        show_dataframe(pd.DataFrame(serialized["role_capacity"]))
    else:
        st.info("Для задачи нет положительных трудозатрат по ролям.")

    st.subheader("Инженеры")
    if result.engineers:
        show_dataframe(pd.DataFrame(serialized["engineers"]))
    else:
        st.info("Для задачи нет подходящих записей об инженерах.")

    st.subheader("Зависимости")
    st.caption(
        "Текущее временное правило направления: "
        f"{RAW_DIRECTION_DESCRIPTION.replace('->', '→')}"
    )
    if result.dependencies:
        show_dataframe(pd.DataFrame(serialized["dependencies"]))
    else:
        st.info("У задачи нет входящих зависимостей.")

    st.subheader("Итог")
    start_column, complete_column = st.columns(2)
    if result.can_start:
        start_column.success("Можно начать: ДА")
    else:
        start_column.error("Можно начать: НЕТ")
    if result.can_complete:
        complete_column.success("Можно завершить в этом спринте: ДА")
    else:
        complete_column.error("Можно завершить в этом спринте: НЕТ")

    if result.reasons:
        reason_rows = []
        for reason in result.reasons:
            code = reason.code.value
            reason_rows.append(
                {
                    "code": code,
                    "description": REASON_TITLES.get(code, code),
                    "details": reason_details_ru(code, reason.details),
                }
            )
        show_dataframe(pd.DataFrame(reason_rows))
    else:
        st.success("Ограничений выполнимости не обнаружено.")
    for limitation in result.limitations:
        st.info(SKILL_DATA_LIMITATION_RU)


def _task_plan_rows(task_results) -> list[dict]:
    return [
        {
            "task_id": item.task_id,
            "initiative_id": item.initiative_id,
            "team_id": item.team_id,
            "original_team_id": item.original_team_id,
            "assigned_team_id": item.assigned_team_id,
            "team_reassigned": item.team_reassigned,
            "team_assignment_reason": item.team_assignment_reason,
            "story_points": item.story_points,
            "source_status": item.source_status,
            "source_rung": item.source_rung,
            "total_score": item.score.total_score,
            "base_score": item.score.base_score,
            "dynamic_score": item.score.dynamic_score,
            **item.score.components,
            "scoring_version": item.score.scoring_version,
            "priority_order": item.priority_order,
            "plan_status": item.status.value,
            "baseline_start_date": item.baseline_start_date,
            "baseline_end_date": item.baseline_end_date,
            "forecast_start_date": item.forecast_start_date,
            "forecast_end_date": item.forecast_end_date,
            "start_shift_days": item.start_shift_days,
            "end_shift_days": item.end_shift_days,
            "start_sprint": item.start_sprint,
            "completion_sprint": item.completion_sprint,
            "is_multi_sprint": item.is_multi_sprint,
            "outside_quarter": item.outside_quarter,
            "reason_codes": ", ".join(reason.code.value for reason in item.reasons),
            "reason_descriptions": "; ".join(
                REASON_TITLES.get(reason.code.value, reason.code.value)
                for reason in item.reasons
            ),
        }
        for item in task_results
    ]


def _placement_attempt_rows(attempts) -> list[dict]:
    return [
        {
            "sprint_number": attempt.sprint_number,
            "outcome": attempt.outcome.value,
            "allocated_hours": attempt.allocated_hours,
            "capacity_sp_load": attempt.capacity_sp_load,
            "planned_completion_sp": attempt.planned_completion_sp,
            "completed_sp": attempt.completed_sp,
            "reason_codes": ", ".join(reason.code.value for reason in attempt.reasons),
            "reason_descriptions": "; ".join(
                REASON_TITLES.get(reason.code.value, reason.code.value)
                for reason in attempt.reasons
            ),
        }
        for attempt in attempts
    ]


def _score_rows(task_result) -> list[dict]:
    return [
        {
            "scoring_version": task_result.score.scoring_version,
            "total_score": task_result.score.total_score,
            "base_score": task_result.score.base_score,
            "dynamic_score": task_result.score.dynamic_score,
            **task_result.score.components,
        }
    ]


def _ineligible_task_rows(task_results) -> list[dict]:
    return [
        {
            "task_id": item.task_id,
            "initiative_id": item.initiative_id,
            "team_id": item.team_id,
            "missing_role": reason.role,
            "required_hours": reason.required_hours,
            "available_specialists": reason.available_specialists,
            "description": REASON_TITLES.get(reason.code.value, reason.code.value),
        }
        for item in task_results
        for reason in item.eligibility.reasons
    ]


def _reason_rows(reasons) -> list[dict]:
    rows = []
    for reason in reasons:
        description = REASON_TITLES.get(reason.code.value, reason.code.value)
        if reason.details.get("attempted_sprint") is not None:
            description += f' — спринт {reason.details["attempted_sprint"]}'
        rows.append(
            {
                "code": reason.code.value,
                "description": description,
                "details": reason_details_ru(reason.code.value, reason.details),
            }
        )
    return rows


def quarter_plan_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Квартальный план")
    uploaded_audit = st.session_state.get("uploaded_audit")
    if uploaded_audit is not None and not uploaded_audit.planning_allowed:
        st.error("Planner заблокирован структурной ошибкой загруженного датасета.")
        return
    st.caption(
        "Детерминированный план на шесть спринтов. Сначала planner стремится закрыть "
        "максимум инициатив, затем учитывает rung и срок завершения. Проверяются Story Points, часы инженеров и зависимости "
        "blocking_task_id → blocked_task_id."
    )
    st.info(
        "Часы и SP-нагрузка планируются независимо. SP-нагрузка распределяется по "
        "спринтам целыми значениями и суммарно равна полному SP задачи. Завершённые "
        "SP равны нулю до Done и полному SP задачи в спринте завершения."
    )
    st.caption(
        "Сначала назначаются инженеры своей команды, затем — свободные специалисты "
        "той же роли из других команд. Лимит часов общий для каждого инженера; "
        "Story Points учитываются у команды задачи."
    )
    context = feasibility_context_from_tables(tables)
    if context is None:
        return

    if st.button("Построить план", type="primary", key="build_quarter_plan"):
        with st.spinner("Строим план на шесть спринтов…"):
            st.session_state["quarter_plan"] = build_quarter_plan(context)

    plan = st.session_state.get("quarter_plan")
    if plan is None:
        st.info("Нажмите «Построить план», чтобы рассчитать распределение задач.")
        return

    summary = plan.summary()
    if plan.quarter_start_date is None:
        st.warning(
            "В данных нет однозначной даты начала квартала. Forecast сохраняется на уровне "
            "спринтов; календарные forecast-даты и сдвиги не вычисляются."
        )
    metrics = (
        ("Задач всего", summary["total_tasks"]),
        ("Запланировано", summary["planned_tasks"]),
        ("Перенесено без занятия ресурсов", summary["moved_tasks"]),
        ("Доступно для планирования", summary["eligible_tasks"]),
        ("Нет специалиста роли", summary["blocked_by_missing_specialists"]),
        ("Задачи-заглушки", summary["placeholder_tasks"]),
        ("Завершено до квартала", summary["precompleted_tasks"]),
        ("На несколько спринтов", summary["multi_sprint_tasks"]),
        ("SP-нагрузка", f'{summary["capacity_sp_load"]:g}'),
        ("Завершено SP", f'{summary["completed_story_points"]:g}'),
        ("Суммарная SP capacity", f'{summary["available_story_points"]:g}'),
    )
    for start in range(0, len(metrics), 4):
        columns = st.columns(4)
        for container, (label, value) in zip(columns, metrics[start : start + 4]):
            container.metric(label, value)

    st.subheader("Задачи в квартальном плане")
    if plan.planned_tasks:
        show_dataframe(pd.DataFrame(_task_plan_rows(plan.planned_tasks)))
    else:
        st.warning("Ни одну задачу не удалось полностью разместить в шести спринтах.")

    sprint_tabs = st.tabs([f"Спринт {number}" for number in range(1, 7)])
    task_results = {item.task_id: item for item in plan.task_results}
    for tab, sprint in zip(sprint_tabs, plan.sprints):
        with tab:
            allocations = []
            for item in sprint.task_allocations:
                task_result = task_results[item.task_id]
                if item.is_started and item.is_completed:
                    state = "Выполнение"
                elif item.is_started:
                    state = "Начало"
                elif item.is_completed:
                    state = "Завершение"
                else:
                    state = "Продолжение"
                allocations.append(
                    {
                        "task_id": item.task_id,
                        "allocation_state": state,
                        "task_story_points": item.task_story_points,
                        "capacity_sp_load": item.capacity_sp_load,
                        "planned_completion_sp": item.planned_completion_sp,
                        "completed_sp": item.completed_sp,
                        "allocated_hours": item.allocated_hours,
                        "completion_sprint": task_result.completion_sprint,
                    }
                )
            if allocations:
                show_dataframe(pd.DataFrame(allocations))
            else:
                st.info("В этом спринте нет распределённых задач.")

            teams = [
                {
                    "team_id": team_id,
                    "used_sp": sprint.team_sp_used.get(team_id, 0.0),
                    "capacity_sp": capacity,
                    "remaining_sp": capacity - sprint.team_sp_used.get(team_id, 0.0),
                }
                for team_id, capacity in sprint.team_capacity_sp.items()
            ]
            left, right = st.columns(2)
            with left:
                st.markdown("**Ресурс команд**")
                show_dataframe(pd.DataFrame(teams))
            with right:
                st.markdown("**Загрузка инженеров**")
                if sprint.engineer_hours_used:
                    show_dataframe(
                        pd.DataFrame(
                            [
                                {"engineer_id": engineer_id, "used_hours": hours}
                                for engineer_id, hours in sprint.engineer_hours_used.items()
                            ]
                        )
                    )
                else:
                    st.caption("Инженерные часы не заняты.")

            for allocation in sprint.task_allocations:
                with st.expander(f"{allocation.task_id}: распределение ресурсов"):
                    if allocation.allocated_role_hours:
                        show_dataframe(
                            pd.DataFrame(
                                [
                                    {"role": role, "allocated_hours": hours}
                                    for role, hours in allocation.allocated_role_hours.items()
                                ]
                            )
                        )
                    if allocation.engineer_allocations:
                        show_dataframe(
                            pd.DataFrame(
                                [item.to_dict() for item in allocation.engineer_allocations]
                            ).drop(columns=["hours"])
                        )
                    result = task_results[allocation.task_id]
                    if result.dependencies:
                        st.write("Блокирующие задачи: " + ", ".join(result.dependencies))
                    reason_rows = _reason_rows(result.reasons)
                    if reason_rows:
                        st.caption("Итоговое решение")
                        show_dataframe(pd.DataFrame(reason_rows))
                    if result.placement_attempts:
                        st.caption("Попытки размещения")
                        show_dataframe(pd.DataFrame(_placement_attempt_rows(result.placement_attempts)))
                    total_hours = sum(item.allocated_hours for item in result.allocations)
                    total_load = sum(item.capacity_sp_load for item in result.allocations)
                    total_sp = sum(item.completed_sp for item in result.allocations)
                    st.caption(
                        f"Итого по задаче: {total_hours:g} ч, SP-нагрузка {total_load:g}, "
                        f"завершено {total_sp:g} SP."
                    )
                    st.caption("Почему задача получила такой приоритет")
                    show_dataframe(pd.DataFrame(_score_rows(result)))

    if plan.ineligible_tasks:
        st.subheader("Задачи, невозможные к планированию")
        st.caption(
            "Эти задачи требуют ролей, которых нет среди профилей инженеров. "
            "Planner не выполнял попытки распределения ресурсов."
        )
        show_dataframe(pd.DataFrame(_ineligible_task_rows(plan.ineligible_tasks)))

    st.subheader("Не удалось полностью разместить в текущем квартале")
    if plan.moved_tasks:
        show_dataframe(pd.DataFrame(_task_plan_rows(plan.moved_tasks)))
        for item in plan.moved_tasks:
            with st.expander(f"{item.task_id}: почему задача перенесена"):
                if item.remaining_role_hours:
                    show_dataframe(
                        pd.DataFrame(
                            [
                                {"role": role, "remaining_hours_after_sprint": hours}
                                for role, hours in item.remaining_role_hours.items()
                            ]
                        )
                    )
                show_dataframe(pd.DataFrame(_reason_rows(item.reasons)))
                if item.placement_attempts:
                    st.caption("Пробные и отклонённые попытки — ресурсы не заняты")
                    show_dataframe(pd.DataFrame(_placement_attempt_rows(item.placement_attempts)))
                st.caption("Почему задача получила такой приоритет")
                show_dataframe(pd.DataFrame(_score_rows(item)))
    else:
        st.success("Все планируемые задачи вошли в квартал.")

    if plan.placeholder_tasks:
        st.subheader("Задачи с неизвестными данными")
        st.caption("Задачи-заглушки не участвуют в планировании ресурсов.")
        show_dataframe(pd.DataFrame(_task_plan_rows(plan.placeholder_tasks)))


def reference_data_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Справочники")
    references = (
        (
            "Итоги планирования",
            "planning_results",
            "Итог разработки, выбирается заказчиком и исполнителем на этапе планирования",
        ),
        (
            "Причины расхождения планов",
            "plan_mismatch_reasons",
            "Причины расхождения планов заказчика и исполнителя",
        ),
        (
            "Результаты закрытия задач",
            "task_closure_results",
            "Результаты работ при закрытии задачи",
        ),
    )
    tabs = st.tabs([item[0] for item in references])
    for tab, (_, table_name, source_title) in zip(tabs, references):
        with tab:
            st.caption(source_title)
            frame = tables.get(table_name)
            if frame is None:
                st.warning(f"Нет processed-файла: {TABLE_FILES[table_name]}")
            else:
                show_dataframe(frame)


def main() -> None:
    st.title("Просмотр processed-данных")
    st.caption(
        "Временный интерфейс для разработки. Он читает processed-данные, строит план "
        "только в памяти и не изменяет файлы."
    )
    pages = {
        "Загрузка датасета": upload_page,
        "Обзор": overview_page,
        "Задачи": tasks_page,
        "Трудозатраты по ролям": effort_page,
        "Зависимости": dependencies_page,
        "Инженеры и компетенции": engineers_page,
        "История производительности команд": team_history_page,
        "Качество данных": data_quality_page,
        "Проверка выполнимости задачи": feasibility_page,
        "Квартальный план": quarter_plan_page,
        "Справочники": reference_data_page,
    }
    selected = st.sidebar.radio("Раздел", list(pages))
    tables, errors = load_known_tables()
    uploaded_tables = st.session_state.get("uploaded_tables")
    if uploaded_tables:
        tables = uploaded_tables
        errors = []
    if selected == "Обзор":
        overview_page(tables, errors)
    else:
        pages[selected](tables)


if __name__ == "__main__":
    main()
