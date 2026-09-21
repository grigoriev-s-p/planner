"""Small, explicit skill neighbourhoods used only for candidate ranking.

The groups are adapted from the repository's real synthetic dataset and the
old training-recommendations branch.  They describe explainable proximity;
they do not change canonical skill identity or imply that two skills are
aliases.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from src.skills import canonicalize_skill


@dataclass(frozen=True)
class SkillGroup:
    key: str
    label: str
    skills: frozenset[str]


@dataclass(frozen=True)
class SkillAffinity:
    score: float
    group_key: str | None
    group_label: str | None
    related_skills: tuple[str, ...]


def _group(key: str, label: str, *skills: str) -> SkillGroup:
    return SkillGroup(
        key=key,
        label=label,
        skills=frozenset(filter(None, (canonicalize_skill(skill) for skill in skills))),
    )


@lru_cache(maxsize=1)
def skill_groups() -> tuple[SkillGroup, ...]:
    return (
        _group("java_spring", "Java и Spring backend", "Java", "Spring Core", "Spring Framework", "Spring Boot", "Spring Cloud", "Hibernate", "Liquibase", "JUnit", "Gradle"),
        _group("frontend_web", "Web frontend", "JavaScript", "TypeScript", "React", "Vue.js", "Angular", "HTML/CSS", "DevTools", "WebSocket", "GraphQL"),
        _group("api_integration", "API и интеграции", "REST API", "SOAP", "REST/SOAP интеграции", "REST/gRPC", "gRPC", "JSON", "XML", "GraphQL", "Postman"),
        _group("databases", "SQL и базы данных", "SQL", "PostgreSQL", "MS SQL", "SQLite", "ClickHouse", "Liquibase", "Архитектура БД"),
        _group("devops", "DevOps и инфраструктура", "Linux", "Docker", "Kubernetes", "CI/CD", "GitLab CI", "CI/CD (GitLab, Jenkins)", "Ansible", "Terraform", "Prometheus", "Grafana", "Git"),
        _group("messaging", "Распределённые системы и messaging", "Kafka", "RabbitMQ", "Elasticsearch", "Microservices", "Microservices Архитектура", "Java", "Scala", "Spring Boot", "Spring Cloud", "Docker"),
        _group("qa_manual", "Ручное и API-тестирование", "Usability", "Postman", "Ручное тестирование", "Функциональное тестирование", "Regression/Smoke/Functional testing", "Регресс", "Интеграционное тестирование", "Снифферы трафика (Charles)", "Тестирование безопасности", "Нагрузочное тестирование (JMeter)"),
        _group("qa_automation", "Автоматизация тестирования", "Selenium WebDriver", "Playwright", "Pytest", "JUnit", "Postman", "CI/CD", "DevTools"),
        _group("ios", "iOS-разработка", "Swift", "SwiftUI", "UIKit", "ARKit", "CoreLocation", "CocoaPods", "MVVM"),
        _group("android", "Android-разработка", "Kotlin", "Java", "Android SDK", "Gradle", "Jetpack Compose", "Material Design", "Dependency Injection", "Room", "MVVM"),
        _group("dotnet", ".NET-разработка", "C#", ".NET", ".NET Core", "ASP.NET", "Entity Framework", "LINQ", "MVC", "MS SQL"),
        _group("data_engineering", "Data Engineering", "Python", "PySpark", "Scala", "Hadoop", "Spark", "Hive", "Airflow", "Оптимизация ETL", "Kafka"),
        _group("data_analytics", "Аналитика данных и BI", "Python", "Pandas", "SQL", "Power BI", "Tableau", "BI инструменты", "Математическая статистика", "Математический анализ"),
        _group("analysis", "Системный и бизнес-анализ", "Системный анализ", "Бизнес-анализ", "Сбор требований", "ТЗ", "BPMN", "UML", "Confluence", "Jira"),
        _group("product_design", "UX/UI и продуктовые исследования", "Figma", "Adobe Illustrator", "UX/UI", "CJM", "A/B testing", "Usability", "Material Design"),
        _group("architecture", "Архитектура решений", "TOGAF", "Архитектура ПО", "Архитектура ИТ-систем", "Microservices Архитектура", "Архитектура БД", "Проектирование интеграций", "MVC", "MVVM", "REST API", "gRPC"),
        _group("product_management", "Управление Data- и продуктовыми направлениями", "Управление Data-командами", "Agile/Scrum", "Продуктовые метрики ИИ", "Роадмапы"),
    )


def skill_affinity(target_skill: str, candidate_skills: Iterable[str]) -> SkillAffinity:
    """Return the strongest deterministic and human-readable learning path."""

    target = canonicalize_skill(target_skill)
    candidate_set = {canonicalize_skill(skill) for skill in candidate_skills}
    candidate_set.discard("")
    best: tuple[float, int, int, SkillGroup, tuple[str, ...]] | None = None
    for group in skill_groups():
        if target not in group.skills:
            continue
        related = tuple(sorted((candidate_set & group.skills) - {target}, key=str.casefold))
        count = len(related)
        if not count:
            continue
        score = 1.0 if count >= 4 else {1: 0.55, 2: 0.75, 3: 0.9}[count]
        option = (score, count, -len(group.skills), group, related)
        if best is None or option[:3] > best[:3]:
            best = option
    if best is None:
        return SkillAffinity(0.0, None, None, ())
    score, _, _, group, related = best
    return SkillAffinity(score, group.key, group.label, related)
