"""File discovery, hashing, and processed-dataset manifest handling."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


PREPROCESSING_VERSION = "1.3.0"
REQUIRED_PROCESSED_FILES = (
    "tasks.csv",
    "task_role_effort.csv",
    "task_dependencies.csv",
    "engineers.csv",
    "engineer_skills.csv",
    "team_history.csv",
    "planning_results.csv",
    "plan_mismatch_reasons.csv",
    "task_closure_results.csv",
)
MANIFEST_NAME = "_dataset_manifest.json"


class DatasetLoadError(RuntimeError):
    """The raw dataset cannot be identified or its manifest is unreadable."""


def find_raw_workbook(raw_dir: Path) -> Path:
    """Return the only Excel workbook in *raw_dir*.

    A single source workbook is intentional: choosing one arbitrarily could make
    the generated dataset non-reproducible.
    """

    candidates = sorted(
        path
        for path in raw_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".xlsx", ".xlsm"}
        and not path.name.startswith("~$")
    )
    if len(candidates) != 1:
        raise DatasetLoadError(
            f"Expected exactly one Excel file in {raw_dir}, found {len(candidates)}."
        )
    return candidates[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(processed_dir: Path) -> dict[str, Any] | None:
    manifest_path = processed_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def processed_dataset_is_current(
    processed_dir: Path,
    raw_file_label: str,
    raw_file_hash: str,
) -> bool:
    """Check both manifest metadata and hashes of every required output file."""

    manifest = read_manifest(processed_dir)
    if manifest is None:
        return False
    if manifest.get("raw_file") != raw_file_label:
        return False
    if manifest.get("raw_file_hash") != raw_file_hash:
        return False
    if manifest.get("preprocessing_version") != PREPROCESSING_VERSION:
        return False

    generated_files = manifest.get("generated_files")
    if not isinstance(generated_files, list):
        return False
    generated_by_name = {
        item.get("name"): item
        for item in generated_files
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if set(generated_by_name) != set(REQUIRED_PROCESSED_FILES):
        return False

    for filename in REQUIRED_PROCESSED_FILES:
        output_path = processed_dir / filename
        metadata = generated_by_name[filename]
        if not output_path.is_file() or output_path.stat().st_size == 0:
            return False
        if metadata.get("sha256") != sha256_file(output_path):
            return False
        if not isinstance(metadata.get("rows"), int) or metadata["rows"] < 1:
            return False
    return True
