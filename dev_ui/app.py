"""Temporary Streamlit UI for inspecting normalized development data."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from data_access import (
    TABLE_FILES,
    boolean_mask,
    derive_data_quality_issues,
    list_processed_files,
    load_csv,
    load_known_tables,
)


st.set_page_config(page_title="Processed data inspector", layout="wide")


def show_load_errors(errors: list[str]) -> None:
    for error in errors:
        st.warning(error)


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
    st.header("Overview")
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
        ("Tasks", len(tasks)),
        ("Placeholder tasks", placeholders),
        ("Initiatives / PRODF", count_unique(tasks, "initiative_id")),
        ("Teams", len(teams)),
        ("Engineers", len(engineers)),
        ("Technologies / skills", count_unique(skills, "technology")),
        ("Dependencies", len(dependencies)),
        ("Task role effort rows", len(effort)),
        ("Team history rows", len(history)),
        ("Data quality issues", len(quality)),
    ]
    for start in range(0, len(metrics), 5):
        columns = st.columns(5)
        for column, (label, value) in zip(columns, metrics[start : start + 5]):
            column.metric(label, value)

    st.subheader("Available processed files")
    files = list_processed_files()
    if files.empty:
        st.warning("No files were found in data/processed.")
    else:
        st.dataframe(files, width="stretch", hide_index=True)


def tasks_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Tasks")
    tasks = tables.get("tasks")
    if tasks is None:
        st.warning(f"Processed file is missing: {TABLE_FILES['tasks']}")
        return

    filtered = tasks.copy()
    filter_columns = st.columns(5)
    task_query = filter_columns[0].text_input("task_id contains", key="tasks_id")
    with filter_columns[1]:
        filtered = multiselect_filter(filtered, "initiative_id", "Initiative / PRODF", "tasks_init")
    with filter_columns[2]:
        filtered = multiselect_filter(filtered, "team_id", "Team", "tasks_team")
    with filter_columns[3]:
        filtered = multiselect_filter(filtered, "status", "Status", "tasks_status")
    placeholders_only = filter_columns[4].checkbox("Show placeholders only")
    if task_query and "task_id" in filtered.columns:
        filtered = filtered[
            filtered["task_id"].astype(str).str.contains(task_query, case=False, na=False)
        ]
    if placeholders_only:
        if "is_placeholder" in filtered.columns:
            filtered = filtered[boolean_mask(filtered["is_placeholder"])]
        else:
            st.warning("Column is_placeholder is absent from tasks.csv.")

    display = filtered.copy()
    if "is_placeholder" in display.columns:
        display.insert(
            0,
            "record_type",
            boolean_mask(display["is_placeholder"]).map(
                {True: "⚠ Placeholder", False: "Task"}
            ),
        )
    st.caption(f"Showing {len(display)} of {len(tasks)} tasks.")
    st.dataframe(display, width="stretch", hide_index=True)


def effort_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Task Role Effort")
    effort = tables.get("task_role_effort")
    if effort is None:
        st.warning(f"Processed file is missing: {TABLE_FILES['task_role_effort']}")
        return
    filtered = effort.copy()
    left, right = st.columns(2)
    with left:
        filtered = multiselect_filter(filtered, "task_id", "Task", "effort_task")
    with right:
        filtered = multiselect_filter(filtered, "role", "Role", "effort_role")
    st.dataframe(filtered, width="stretch", hide_index=True)

    if "required_hours" not in effort.columns:
        st.warning("Column required_hours is absent from task_role_effort.csv.")
        return
    numeric = effort.copy()
    numeric["required_hours"] = pd.to_numeric(numeric["required_hours"], errors="coerce")
    by_role, by_task = st.columns(2)
    with by_role:
        st.subheader("Required hours by role")
        if "role" in numeric.columns:
            aggregate = (
                numeric.groupby("role", dropna=False)["required_hours"]
                .sum(min_count=1)
                .sort_values(ascending=False)
                .rename("required_hours")
            )
            st.bar_chart(aggregate)
            st.dataframe(aggregate.reset_index(), width="stretch", hide_index=True)
    with by_task:
        st.subheader("Required hours by task")
        if "task_id" in numeric.columns:
            aggregate = (
                numeric.groupby("task_id", dropna=False)["required_hours"]
                .sum(min_count=1)
                .sort_values(ascending=False)
                .rename("required_hours")
            )
            st.bar_chart(aggregate)
            st.dataframe(aggregate.reset_index(), width="stretch", hide_index=True)


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
    st.header("Dependencies")
    st.info(
        "Raw display: arrows follow blocking_task_id → blocked_task_id exactly as stored. "
        "This is not a confirmed business interpretation of dependency_type."
    )
    dependencies = enriched_dependencies(tables)
    if dependencies is None:
        st.warning(f"Processed file is missing: {TABLE_FILES['task_dependencies']}")
        return
    suspicious_only = st.checkbox("Show dependencies with placeholders only")
    shown = dependencies[dependencies["has_placeholder"]] if suspicious_only else dependencies
    suspicious_count = int(dependencies["has_placeholder"].sum())
    if suspicious_count:
        st.warning(f"{suspicious_count} dependencies reference at least one placeholder task.")
    st.dataframe(shown, width="stretch", hide_index=True)

    if st.checkbox("Show simple raw dependency graph"):
        tasks = tables.get("tasks", pd.DataFrame())
        placeholder_ids = set()
        if {"task_id", "is_placeholder"}.issubset(tasks.columns):
            placeholder_ids = set(
                tasks.loc[boolean_mask(tasks["is_placeholder"]), "task_id"].dropna().astype(str)
            )
        st.caption("Orange nodes are placeholders. Edge direction mirrors the raw columns.")
        st.graphviz_chart(dependency_graph(shown, placeholder_ids), width="stretch")


def engineers_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Engineers / Skills")
    engineers = tables.get("engineers")
    skills = tables.get("engineer_skills")
    engineers_tab, skills_tab, aggregates_tab = st.tabs(["Engineers", "Skills", "Aggregates"])

    with engineers_tab:
        if engineers is None:
            st.warning(f"Processed file is missing: {TABLE_FILES['engineers']}")
        else:
            filtered = engineers.copy()
            columns = st.columns(3)
            with columns[0]:
                filtered = multiselect_filter(filtered, "team_id", "Team", "engineer_team")
            with columns[1]:
                filtered = multiselect_filter(filtered, "role", "Role", "engineer_role")
            with columns[2]:
                filtered = multiselect_filter(filtered, "grade", "Grade", "engineer_grade")
            if "capacity_rate" in filtered.columns:
                filtered["available_hours"] = (
                    pd.to_numeric(filtered["capacity_rate"], errors="coerce") * 80
                )
            else:
                st.warning("Column capacity_rate is absent from engineers.csv.")
            st.caption("available_hours = 80 × capacity_rate; diagnostic formula only.")
            st.dataframe(filtered, width="stretch", hide_index=True)

    with skills_tab:
        if skills is None:
            st.warning(f"Processed file is missing: {TABLE_FILES['engineer_skills']}")
        else:
            engineer_options = nonblank_options(skills, "engineer_id")
            selected = st.selectbox("Engineer", ["All engineers", *engineer_options])
            shown = skills
            if selected != "All engineers" and "engineer_id" in skills.columns:
                shown = skills[skills["engineer_id"].astype(str) == selected]
            st.dataframe(shown, width="stretch", hide_index=True)

    with aggregates_tab:
        if engineers is not None:
            left, right = st.columns(2)
            with left:
                st.subheader("Engineers by role")
                if "role" in engineers.columns:
                    st.bar_chart(engineers["role"].value_counts())
            with right:
                st.subheader("Specialists by team")
                if "team_id" in engineers.columns:
                    st.bar_chart(engineers["team_id"].value_counts())
        if skills is not None and {"technology", "engineer_id"}.issubset(skills.columns):
            st.subheader("Engineers by technology")
            technology_counts = (
                skills.groupby("technology")["engineer_id"]
                .nunique()
                .sort_values(ascending=False)
                .rename("engineers")
            )
            st.bar_chart(technology_counts)
            st.dataframe(technology_counts.reset_index(), width="stretch", hide_index=True)


def team_history_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Team History")
    history = tables.get("team_history")
    if history is None:
        st.warning(f"Processed file is missing: {TABLE_FILES['team_history']}")
        return
    st.dataframe(history, width="stretch", hide_index=True)
    required = {"team_id", "velocity_achieved"}
    if not required.issubset(history.columns):
        st.warning("team_history.csv lacks team_id or velocity_achieved.")
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
    st.subheader("Historical velocity diagnostics")
    st.caption(
        "calculated_capacity = average historical velocity × 0.8. "
        "This is a diagnostic view of the specification formula, not planner output."
    )
    st.dataframe(aggregate, width="stretch", hide_index=True)
    st.bar_chart(aggregate.set_index("team_id")[["average_historical_velocity", "calculated_capacity"]])


def data_quality_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Data Quality")
    persisted = load_csv("data_quality_issues.csv")
    if persisted.frame is not None:
        issues = persisted.frame
        st.caption("Loaded from data/processed/data_quality_issues.csv.")
    else:
        issues = derive_data_quality_issues(tables)
        st.info(
            "Preprocessing warnings are not persisted as a separate file. "
            "This table reconstructs the current placeholder, reference, and numeric diagnostics "
            "in memory from processed CSV files. Nothing is written to disk."
        )
    if issues.empty:
        st.success("No data quality issues were detected in the available processed tables.")
        return
    filtered = issues.copy()
    filter_columns = st.columns(4)
    for container, column, label in zip(
        filter_columns,
        ("severity", "issue_type", "entity_id", "source"),
        ("Severity", "Issue type", "Entity", "Source"),
    ):
        with container:
            filtered = multiselect_filter(filtered, column, label, f"quality_{column}")
    st.dataframe(filtered, width="stretch", hide_index=True)


def reference_data_page(tables: dict[str, pd.DataFrame]) -> None:
    st.header("Reference Data")
    references = (
        (
            "Planning results",
            "planning_results",
            "Итог разработки, выбирается заказчиком и исполнителем на этапе планирования",
        ),
        (
            "Plan mismatch reasons",
            "plan_mismatch_reasons",
            "Причины расхождения планов заказчика и исполнителя",
        ),
        (
            "Task closure results",
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
                st.warning(f"Processed file is missing: {TABLE_FILES[table_name]}")
            else:
                st.dataframe(frame, width="stretch", hide_index=True)


def main() -> None:
    st.title("Processed data inspector")
    st.caption(
        "Temporary development-only, read-only UI. It does not run preprocessing or planning."
    )
    pages = {
        "Overview": overview_page,
        "Tasks": tasks_page,
        "Task Role Effort": effort_page,
        "Dependencies": dependencies_page,
        "Engineers / Skills": engineers_page,
        "Team History": team_history_page,
        "Data Quality": data_quality_page,
        "Reference Data": reference_data_page,
    }
    selected = st.sidebar.radio("Section", list(pages))
    tables, errors = load_known_tables()
    if selected == "Overview":
        overview_page(tables, errors)
    else:
        pages[selected](tables)


if __name__ == "__main__":
    main()
