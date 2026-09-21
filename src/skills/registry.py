"""Deterministic canonical registry and task-title skill extraction.

The initial vocabulary is grounded in the organizer workbook: ``DATASET_SKILLS``
contains the distinct values observed in its engineer skill column.  A small,
reviewed extension covers technologies that occur in real task titles and a few
common spelling aliases.  Canonicalization and title extraction deliberately use
separate APIs: a known value may be normalized without making it eligible for
substring matching in arbitrary prose.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable, Mapping


EXTRACTOR_VERSION = "skills-registry-1.0"

# 116 values observed in data/raw/dataset.xlsx before canonicalization.
DATASET_SKILLS = (
    ".NET Core", "A/B testing", "Adobe Illustrator", "Agile/Scrum", "Airflow",
    "Android SDK", "Angular", "Ansible", "Apache Kafka", "ARKit", "ASP.NET",
    "BI инструменты", "BPMN", "C#", "CI/CD", "CI/CD (GitLab, Jenkins)", "CJM",
    "CocoaPods", "Confluence", "Core", "Core Spring", "CoreLocation",
    "Dependency Injection", "DevTools", "Docker", "ElasticSearch",
    "Entity Framework", "Figma", "Git", "GitLab CI", "Gradle", "GraphQL",
    "gRPC", "Hadoop", "Hibernate", "Hive", "HTML/CSS", "Java", "JavaScript",
    "Jetpack Compose", "Jira", "JSON", "JUnit", "Kafka", "Kotlin", "Kubernetes",
    "LINQ", "Linux", "Liquibase", "Material Design", "Microservices",
    "Microservices Архитектура", "MS SQL", "MVC", "MVVM", "Pandas", "Playwright",
    "PostgreSQL", "Postman", "Power BI", "Prometheus", "PySpark", "Pytest", "Python",
    "RabbitMQ", "React", "Regression/Smoke/Functional testing", "REST API",
    "REST/gRPC", "REST/SOAP интеграции", "Room", "Scala", "Selenium WebDriver",
    "SOAP", "Spark", "Spring Boot", "Spring Cloud", "Spring Core", "Spring Framework",
    "SQL", "SQLite", "Swift", "SwiftUI", "Tableau", "Terraform", "TOGAF",
    "TypeScript", "UIKit", "UML", "Usability", "UX/UI", "Vue.js", "WebSocket", "XML",
    "Архитектура БД", "Архитектура ИТ-систем", "Архитектура ПО", "Бизнес-анализ",
    "Интеграционное тестирование", "Математическая статистика", "Математический анализ",
    "Микросервисы", "Нагрузочное тестирование (JMeter)", "Оптимизация ETL",
    "Продуктовые метрики ИИ", "Проектирование интеграций", "Регресс", "Роадмапы",
    "Ручное тестирование", "Сбор требований", "Системный анализ",
    "Снифферы трафика (Charles)", "Тестирование безопасности", "ТЗ",
    "Управление Data-командами", "Функциональное тестирование",
)

# These occur in actual task titles or are narrowly scoped compatibility
# additions required for future similar workbooks.  They are intentionally few.
ADDITIONAL_SKILLS = ("Grafana", "ClickHouse", "LLM", "C++", ".NET", "1С")

CANONICAL_RENAMES = {
    "Apache Kafka": "Kafka",
    "Микросервисы": "Microservices",
    "Core Spring": "Spring Core",
    "ElasticSearch": "Elasticsearch",
}

ALIASES: dict[str, tuple[str, ...]] = {
    "PostgreSQL": ("Postgres", "PostgresSQL", "Postgre SQL"),
    "JavaScript": ("JS", "Java Script"),
    "CI/CD": ("CI CD", "CI-CD", "CICD"),
    "Kafka": ("Apache Kafka",),
    "Vue.js": ("VueJS", "Vue JS", "Vue"),
    "Elasticsearch": ("Elastic Search",),
    "MS SQL": ("MSSQL", "Microsoft SQL Server", "SQL Server"),
    ".NET Core": ("Dotnet Core", "Dot Net Core"),
    ".NET": ("Dotnet", "Dot Net"),
    "C#": ("C Sharp",),
    "C++": ("CPP",),
    "Kubernetes": ("K8s",),
    "1С": ("1C",),
}

# Generic business words are valid declared competencies but unsafe in prose.
TITLE_DISABLED = {
    "Core", "Room", "Регресс", "ТЗ", "Архитектура ПО", "Сбор требований",
    "Роадмапы", "Usability", "BI инструменты",
}


def normalize_skill_text(value: Any) -> str:
    """NFKC/case/separator normalization without destroying skill symbols."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    text = text.replace("\\", "/")
    text = " ".join(text.replace("\u00a0", " ").replace("\u202f", " ").split())
    return text.casefold()


def _canonical_id(display_name: str) -> str:
    symbolic = display_name.replace("#", " sharp ").replace("+", " plus ").replace(".", " dot ")
    ascii_name = unicodedata.normalize("NFKD", symbolic).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")
    if slug:
        return slug
    digest = hashlib.sha1(display_name.encode("utf-8")).hexdigest()[:10]
    return f"skill-{digest}"


def _group(display_name: str) -> str:
    value = normalize_skill_text(display_name)
    groups = (
        ("frontend", {"javascript", "typescript", "react", "angular", "vue.js", "html/css", "ux/ui"}),
        ("backend", {"java", "python", "c#", "c++", ".net", ".net core", "spring boot", "spring cloud", "spring core", "spring framework", "asp.net"}),
        ("data", {"sql", "postgresql", "ms sql", "sqlite", "clickhouse", "hadoop", "hive", "spark", "pyspark", "pandas", "airflow"}),
        ("devops", {"ci/cd", "gitlab ci", "docker", "kubernetes", "terraform", "ansible", "linux", "prometheus", "grafana"}),
        ("integration", {"kafka", "rabbitmq", "rest api", "grpc", "soap", "graphql", "websocket"}),
        ("mobile", {"kotlin", "swift", "swiftui", "uikit", "android sdk", "jetpack compose", "arkit"}),
        ("qa", {"pytest", "junit", "playwright", "selenium webdriver", "postman"}),
    )
    for group_id, names in groups:
        if value in names:
            return group_id
    return "other"


@dataclass(frozen=True)
class SkillDefinition:
    canonical_id: str
    display_name: str
    aliases: tuple[str, ...]
    group_id: str
    match_rules: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillMatch:
    canonical_id: str
    display_name: str
    matched_text: str
    source: str = "title"
    confidence: str = "high"

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "technology": self.display_name,
            "matched_text": self.matched_text,
            "source": self.source,
            "confidence": self.confidence,
            "extractor_version": EXTRACTOR_VERSION,
        }


class SkillRegistry:
    def __init__(self) -> None:
        canonical_names = {
            CANONICAL_RENAMES.get(value, value) for value in (*DATASET_SKILLS, *ADDITIONAL_SKILLS)
        }
        definitions: list[SkillDefinition] = []
        for display_name in sorted(canonical_names, key=str.casefold):
            source_aliases = {
                value for value, canonical in CANONICAL_RENAMES.items() if canonical == display_name
            }
            aliases = tuple(sorted({display_name, *source_aliases, *ALIASES.get(display_name, ())}, key=lambda item: (-len(item), item.casefold())))
            definitions.append(SkillDefinition(
                canonical_id=_canonical_id(display_name),
                display_name=display_name,
                aliases=aliases,
                group_id=_group(display_name),
                match_rules={"title_enabled": display_name not in TITLE_DISABLED, "confidence": "high"},
            ))
        self.definitions = tuple(definitions)
        self._by_alias = {
            normalize_skill_text(alias): definition
            for definition in self.definitions
            for alias in definition.aliases
        }
        patterns = []
        for definition in self.definitions:
            if not definition.match_rules.get("title_enabled", True):
                continue
            for alias in definition.aliases:
                normalized = normalize_skill_text(alias)
                if normalized:
                    pattern = re.compile(
                        rf"(?<![\w+#.]){re.escape(normalized)}(?![\w+#.])",
                        re.UNICODE,
                    )
                    patterns.append((len(normalized), normalized, pattern, definition))
        self._patterns = tuple(sorted(patterns, key=lambda item: (-item[0], item[1])))

    def canonicalize(self, value: Any) -> str:
        cleaned = " ".join(unicodedata.normalize("NFKC", str(value or "")).split())
        if not cleaned:
            return ""
        definition = self._by_alias.get(normalize_skill_text(cleaned))
        return definition.display_name if definition else cleaned

    def definition(self, value: Any) -> SkillDefinition | None:
        return self._by_alias.get(normalize_skill_text(value))

    def options(self) -> list[dict[str, Any]]:
        return [
            {
                "canonical_id": item.canonical_id,
                "display_name": item.display_name,
                "aliases": list(item.aliases),
                "group_id": item.group_id,
            }
            for item in self.definitions
        ]

    def extract(self, title: Any) -> list[SkillMatch]:
        original = unicodedata.normalize("NFKC", str(title or ""))
        normalized, offsets = _normalize_with_offsets(original)
        if not normalized:
            return []
        matches: list[SkillMatch] = []
        occupied: list[tuple[int, int]] = []
        seen: set[str] = set()
        for _, _, pattern, definition in self._patterns:
            if definition.canonical_id in seen:
                continue
            for match in pattern.finditer(normalized):
                span = match.span()
                if any(span[0] < end and start < span[1] for start, end in occupied):
                    continue
                start = offsets[span[0]]
                end = offsets[span[1] - 1] + 1
                matches.append(SkillMatch(
                    canonical_id=definition.canonical_id,
                    display_name=definition.display_name,
                    matched_text=original[start:end],
                    confidence=str(definition.match_rules.get("confidence", "high")),
                ))
                occupied.append(span)
                seen.add(definition.canonical_id)
                break
        return sorted(matches, key=lambda item: original.find(item.matched_text))


def _normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    offsets: list[int] = []
    for source_index, source_char in enumerate(text):
        expanded = unicodedata.normalize("NFKC", source_char).replace("ё", "е").replace("Ё", "Е").casefold()
        for char in expanded:
            if char in "‐‑‒–—―−":
                char = "-"
            elif char == "\\":
                char = "/"
            elif char.isspace():
                char = " "
            if char == " " and (not chars or chars[-1] == " "):
                continue
            chars.append(char)
            offsets.append(source_index)
    while chars and chars[-1] == " ":
        chars.pop()
        offsets.pop()
    return "".join(chars), offsets


@lru_cache(maxsize=1)
def get_skill_registry() -> SkillRegistry:
    return SkillRegistry()


def canonicalize_skill(value: Any) -> str:
    return get_skill_registry().canonicalize(value)


def extract_title_skills(title: Any) -> list[SkillMatch]:
    return get_skill_registry().extract(title)


def merge_task_skill_requirements(
    *,
    task_id: str,
    title: Any,
    explicit: Iterable[str | Mapping[str, Any]] = (),
    roles: Iterable[str] = (),
    rejected: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Merge explicit and high-confidence title requirements deterministically."""

    role_values = tuple(dict.fromkeys(str(role).strip() for role in roles if str(role).strip()))
    default_role = role_values[0] if len(role_values) == 1 else None
    rejected_values = {canonicalize_skill(value) for value in rejected if canonicalize_skill(value)}
    merged: dict[str, dict[str, Any]] = {}
    for item in explicit:
        record = dict(item) if isinstance(item, Mapping) else {"technology": item}
        technology = canonicalize_skill(record.get("technology") or record.get("skill"))
        if not technology:
            continue
        if record.get("rejected"):
            rejected_values.add(technology)
            continue
        merged[technology] = {
            "task_id": task_id,
            "role": str(record.get("role") or default_role or "").strip() or None,
            "technology": technology,
            "source": "explicit",
            "matched_text": str(record.get("matched_text") or record.get("technology") or technology),
            "confidence": "high",
            "required_hours": record.get("required_hours"),
            "extractor_version": EXTRACTOR_VERSION,
            "confirmed": True,
            "rejected": False,
        }
    for match in extract_title_skills(title):
        if match.display_name in rejected_values:
            continue
        existing = merged.get(match.display_name)
        if existing:
            existing["source"] = "both"
            existing["matched_text"] = match.matched_text
            continue
        merged[match.display_name] = {
            "task_id": task_id,
            "role": default_role,
            "technology": match.display_name,
            "source": "title",
            "matched_text": match.matched_text,
            "confidence": match.confidence,
            "required_hours": None,
            "extractor_version": EXTRACTOR_VERSION,
            "confirmed": match.confidence == "high",
            "rejected": False,
        }
    return [merged[key] for key in sorted(merged, key=str.casefold)]
