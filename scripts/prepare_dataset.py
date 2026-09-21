"""Build normalized CSV files from the organizer's single raw Excel workbook."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.loader import (  # noqa: E402
    MANIFEST_NAME,
    PREPROCESSING_VERSION,
    REQUIRED_PROCESSED_FILES,
    DatasetLoadError,
    find_raw_workbook,
    processed_dataset_is_current,
    sha256_file,
)
from src.data.normalization import NormalizedDataset, normalize  # noqa: E402
from src.data.parser import DatasetParsingError, parse_workbook  # noqa: E402
from src.data.validation import DataValidationError, validate  # noqa: E402


@dataclass(frozen=True)
class ProcessingResult:
    status: str
    raw_file: Path
    counts: dict[str, int]
    warnings: list[str]


def raw_file_label(raw_file: Path, project_root: Path) -> str:
    try:
        return raw_file.relative_to(project_root).as_posix()
    except ValueError:
        return raw_file.name


EMPTY_TABLE_FIELDS = {
    "task_skill_requirements.csv": [
        "task_id", "role", "technology", "source", "matched_text",
        "confidence", "required_hours", "extractor_version", "confirmed", "rejected",
    ],
}


def write_csv_atomically(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(rows[0].keys()) if rows else EMPTY_TABLE_FIELDS.get(path.name)
    if not fieldnames:
        raise ValueError(f"Cannot write empty table {path.name}.")
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        writer = csv.DictWriter(temporary_file, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary_path, path)


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
        json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
        temporary_file.write("\n")
    os.replace(temporary_path, path)


def generate_manifest(
    raw_file_label_value: str,
    raw_hash: str,
    output_dir: Path,
    dataset: NormalizedDataset,
) -> dict[str, Any]:
    generated_files = [
        {
            "name": filename,
            "sha256": sha256_file(output_dir / filename),
            "rows": len(dataset.tables[filename]),
        }
        for filename in REQUIRED_PROCESSED_FILES
    ]
    return {
        "raw_file": raw_file_label_value,
        "raw_file_hash": raw_hash,
        "generated_files": generated_files,
        "preprocessing_version": PREPROCESSING_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def run_preprocessing(
    raw_file: Path,
    output_dir: Path,
    project_root: Path,
    force: bool = False,
) -> ProcessingResult:
    raw_file = raw_file.resolve()
    output_dir = output_dir.resolve()
    source_label = raw_file_label(raw_file, project_root.resolve())
    raw_hash = sha256_file(raw_file)
    if not force and processed_dataset_is_current(output_dir, source_label, raw_hash):
        return ProcessingResult("skipped", raw_file, {}, [])

    parsed = parse_workbook(raw_file)
    dataset = normalize(parsed)
    report = validate(dataset)
    report.require_success()

    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in REQUIRED_PROCESSED_FILES:
        write_csv_atomically(output_dir / filename, dataset.tables[filename])
    manifest = generate_manifest(source_label, raw_hash, output_dir, dataset)
    write_json_atomically(output_dir / MANIFEST_NAME, manifest)
    return ProcessingResult(
        "processed",
        raw_file,
        {filename: len(dataset.tables[filename]) for filename in REQUIRED_PROCESSED_FILES},
        report.warnings,
    )


def print_summary(result: ProcessingResult, output_dir: Path) -> None:
    if result.status == "skipped":
        print("Processed dataset already exists. Skipping preprocessing.")
        return
    print(f"Raw dataset: {result.raw_file}")
    labels = {
        "tasks.csv": "Tasks",
        "task_role_effort.csv": "Task role effort",
        "task_dependencies.csv": "Dependencies",
        "engineers.csv": "Engineers",
        "engineer_skills.csv": "Engineer skills",
        "task_skill_requirements.csv": "Task skill requirements",
        "team_history.csv": "Team history",
        "planning_results.csv": "Planning results",
        "plan_mismatch_reasons.csv": "Plan mismatch reasons",
        "task_closure_results.csv": "Task closure results",
    }
    for filename in REQUIRED_PROCESSED_FILES:
        print(f"{labels[filename]}: {result.counts[filename]}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    print("Validation: OK")
    print(f"Processed dataset saved to {output_dir.as_posix()}/")


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild the processed dataset even when the manifest and hashes match.",
    )
    arguments = argument_parser.parse_args()
    try:
        raw_file = find_raw_workbook(PROJECT_ROOT / "data" / "raw")
        result = run_preprocessing(
            raw_file=raw_file,
            output_dir=PROJECT_ROOT / "data" / "processed",
            project_root=PROJECT_ROOT,
            force=arguments.force,
        )
    except (DatasetLoadError, DatasetParsingError, DataValidationError, OSError, ValueError) as error:
        print(f"Preprocessing failed: {error}", file=sys.stderr)
        return 1
    print_summary(result, PROJECT_ROOT / "data" / "processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
