"""Canonical skill registry shared by preprocessing, planning and UI DTOs."""

from .registry import (
    EXTRACTOR_VERSION,
    SkillDefinition,
    SkillMatch,
    canonicalize_skill,
    extract_title_skills,
    get_skill_registry,
    merge_task_skill_requirements,
    normalize_skill_text,
)

__all__ = [
    "EXTRACTOR_VERSION",
    "SkillDefinition",
    "SkillMatch",
    "canonicalize_skill",
    "extract_title_skills",
    "get_skill_registry",
    "merge_task_skill_requirements",
    "normalize_skill_text",
]
