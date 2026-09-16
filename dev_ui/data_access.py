"""Read-only access to normalized CSV files for the temporary DEV UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

TABLE_FILES = {
    "tasks": "tasks.csv",
    "task_role_effort": "task_role_effort.csv",
    "task_dependencies": "task_dependencies.csv",
    "engineers": "engineers.csv",
    "engineer_skills": "engineer_skills.csv",
    "team_history": "team_history.csv",
    "planning_results": "planning_results.csv",
    "plan_mismatch_reasons": "plan_mismatch_reasons.csv",
    "task_closure_results": "task_closure_results.csv",
}


@dataclass(frozen=True)
class LoadResult:
    frame: pd.DataFrame | None
    error: str | None = None


@st.cache_data(show_spinner=False)
def _read_csv(path_text: str, modified_ns: int) -> pd.DataFrame:
    """Cache a CSV by path and modification time so changed files are reloaded."""

    del modified_ns
    return pd.read_csv(path_text)


def load_csv(filename: str) -> LoadResult:
    """Load one processed CSV without letting a missing or malformed file crash the UI."""

    path = PROCESSED_DIR / filename
    if not path.is_file():
        return LoadResult(None, f"Processed file is missing: {filename}")
    try:
        return LoadResult(_read_csv(str(path), path.stat().st_mtime_ns))
    except (OSError, UnicodeError, pd.errors.ParserError) as error:
        return LoadResult(None, f"Could not read {filename}: {error}")


def load_known_tables() -> tuple[dict[str, pd.DataFrame], list[str]]:
    tables: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    for table_name, filename in TABLE_FILES.items():
        result = load_csv(filename)
        if result.frame is None:
            errors.append(result.error or f"Could not read {filename}")
        else:
            tables[table_name] = result.frame
    return tables, errors


def list_processed_files() -> pd.DataFrame:
    if not PROCESSED_DIR.is_dir():
        return pd.DataFrame(columns=["file", "size_bytes", "modified_at"])
    records = []
    for path in sorted(item for item in PROCESSED_DIR.iterdir() if item.is_file()):
        metadata = path.stat()
        records.append(
            {
                "file": path.name,
                "size_bytes": metadata.st_size,
                "modified_at": datetime.fromtimestamp(metadata.st_mtime),
            }
        )
    return pd.DataFrame(records)


def boolean_mask(series: pd.Series) -> pd.Series:
    """Normalize CSV booleans without treating arbitrary nonempty strings as true."""

    return series.fillna(False).astype(str).str.strip().str.casefold().isin({"true", "1", "yes"})


def _nonblank_strings(values: Iterable[object]) -> set[str]:
    return {
        str(value).strip()
        for value in values
        if pd.notna(value) and str(value).strip()
    }


def derive_data_quality_issues(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Reconstruct current diagnostics in memory from normalized read-only data."""

    columns = ["severity", "issue_type", "entity_id", "source", "details"]
    issues: list[dict[str, str]] = []
    tasks = tables.get("tasks", pd.DataFrame())
    effort = tables.get("task_role_effort", pd.DataFrame())
    dependencies = tables.get("task_dependencies", pd.DataFrame())

    task_ids = (
        _nonblank_strings(tasks["task_id"])
        if "task_id" in tasks.columns
        else set()
    )
    if "task_id" in tasks.columns:
        duplicated = tasks["task_id"].dropna().astype(str)
        for task_id in sorted(duplicated[duplicated.duplicated(keep=False)].unique()):
            issues.append(
                {
                    "severity": "ERROR",
                    "issue_type": "DUPLICATE_TASK",
                    "entity_id": task_id,
                    "source": "tasks",
                    "details": "Task identifier occurs more than once.",
                }
            )

    if {"task_id", "is_placeholder"}.issubset(tasks.columns):
        placeholder_ids = _nonblank_strings(
            tasks.loc[boolean_mask(tasks["is_placeholder"]), "task_id"]
        )
        effort_ids = (
            _nonblank_strings(effort["task_id"])
            if "task_id" in effort.columns
            else set()
        )
        dependency_ids: set[str] = set()
        for column in ("blocking_task_id", "blocked_task_id"):
            if column in dependencies.columns:
                dependency_ids.update(_nonblank_strings(dependencies[column]))
        for task_id in sorted(placeholder_ids):
            sources = []
            if task_id in effort_ids:
                sources.append("task_role_effort")
            if task_id in dependency_ids:
                sources.append("Task_dependencies")
            source = " and ".join(sources) or "tasks"
            issues.append(
                {
                    "severity": "WARNING",
                    "issue_type": "MISSING_TASK",
                    "entity_id": task_id,
                    "source": source,
                    "details": (
                        f"Placeholder task created for {task_id}: referenced by {source} "
                        "but absent from Tasks."
                    ),
                }
            )

    for row_number, dependency in dependencies.iterrows():
        for column in ("blocking_task_id", "blocked_task_id"):
            if column not in dependencies.columns:
                continue
            value = dependency.get(column)
            task_id = str(value).strip() if pd.notna(value) else ""
            if not task_id or task_id not in task_ids:
                issues.append(
                    {
                        "severity": "ERROR",
                        "issue_type": "ORPHAN_TASK_REFERENCE",
                        "entity_id": task_id,
                        "source": "task_dependencies",
                        "details": f"Row {row_number + 1}: {column} is absent from tasks.",
                    }
                )

    numeric_rules = (
        ("task_role_effort", "required_hours", 0.0, None),
        ("engineers", "capacity_rate", 0.0, 1.0),
        ("tasks", "story_points", 0.0, None),
    )
    for table_name, field, lower_bound, upper_bound in numeric_rules:
        frame = tables.get(table_name, pd.DataFrame())
        if field not in frame.columns:
            continue
        values = pd.to_numeric(frame[field], errors="coerce")
        invalid = frame[field].notna() & values.isna()
        invalid |= values.notna() & (values < lower_bound)
        if upper_bound is not None:
            if field == "capacity_rate":
                invalid |= values.notna() & (values <= lower_bound)
            invalid |= values.notna() & (values > upper_bound)
        for row_number in frame.index[invalid]:
            entity = ""
            for identifier in ("task_id", "engineer_id"):
                if identifier in frame.columns and pd.notna(frame.at[row_number, identifier]):
                    entity = str(frame.at[row_number, identifier])
                    break
            issues.append(
                {
                    "severity": "ERROR",
                    "issue_type": "INVALID_NUMERIC_VALUE",
                    "entity_id": entity,
                    "source": table_name,
                    "details": f"Row {row_number + 1}: invalid {field}={frame.at[row_number, field]!r}.",
                }
            )

    return pd.DataFrame(issues, columns=columns)
