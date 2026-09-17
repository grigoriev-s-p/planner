"""Structured upload, preprocessing, audit, and planning-gate workflow."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from scripts.data_audit import (
    AuditError,
    DatasetAuditResult,
    audit_dataset,
    machine_readable_audit,
    structural_audit_failure,
)
from scripts.prepare_dataset import run_preprocessing
from src.data.parser import DatasetParsingError
from src.data.validation import DataValidationError


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
class UploadFlowResult:
    processing_status: str
    tables: dict[str, pd.DataFrame]
    audit: DatasetAuditResult


def audit_processed_dataset(processed_dir: Path) -> DatasetAuditResult:
    """Run the existing audit and expose its structured result."""

    try:
        return machine_readable_audit(audit_dataset(processed_dir, 84))
    except (AuditError, OSError, ValueError) as error:
        return structural_audit_failure(str(error))


def process_uploaded_workbook(filename: str, content: bytes) -> UploadFlowResult:
    """Preprocess an uploaded workbook in isolation and return in-memory tables."""

    safe_name = Path(filename).name
    if Path(safe_name).suffix.lower() not in {".xlsx", ".xlsm"}:
        return UploadFlowResult(
            processing_status="failed",
            tables={},
            audit=structural_audit_failure("Upload must be an .xlsx or .xlsm workbook."),
        )

    try:
        with tempfile.TemporaryDirectory(prefix="quarter-planner-upload-") as directory:
            root = Path(directory)
            raw_file = root / safe_name
            processed_dir = root / "processed"
            raw_file.write_bytes(content)
            processing = run_preprocessing(
                raw_file=raw_file,
                output_dir=processed_dir,
                project_root=root,
                force=True,
            )
            audit = audit_processed_dataset(processed_dir)
            tables = {
                table_name: pd.read_csv(processed_dir / csv_name)
                for table_name, csv_name in TABLE_FILES.items()
            }
            return UploadFlowResult(processing.status, tables, audit)
    except (
        AuditError,
        DatasetParsingError,
        DataValidationError,
        OSError,
        ValueError,
    ) as error:
        return UploadFlowResult(
            processing_status="failed",
            tables={},
            audit=structural_audit_failure(str(error)),
        )
