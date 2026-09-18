from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Позволяет запускать файл прямо из корня:
# py scripts/run_bus_factor.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.bus_factor import BusFactorService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bus Factor analytics for Team Planning project"
    )
    parser.add_argument(
        "--processed-dir",
        default="data/processed",
        help="Каталог с нормализованными CSV",
    )
    parser.add_argument(
        "--team",
        default=None,
        help="Показать подробный анализ одной команды",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/bus_factor",
        help="Куда сохранить отчёты",
    )
    args = parser.parse_args()

    service = BusFactorService.from_processed_dir(
        args.processed_dir
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    helpers = service.get_all_potential_helpers(
        max_team_bf=2,
    )
    helpers_path = output_dir / "potential_helpers.csv"
    helpers.to_csv(helpers_path, index=False, encoding="utf-8-sig")

    print("\n=== POTENTIAL CROSS-TEAM HELPERS ===")
    if helpers.empty:
        print("Подходящие внешние помощники не найдены.")
    else:
        groups = (
            helpers.groupby(
                [
                    "target_team_id",
                    "competency_type",
                    "competency",
                    "team_bus_factor",
                    "organization_bus_factor",
                    "quarter_relevance",
                ],
                dropna=False,
            )
            .size()
            .reset_index(name="helper_count")
        )

        print(
            f"Найдено {len(helpers)} строк кандидатов "
            f"для {len(groups)} дефицитных компетенций."
        )

        print("\nКраткая сводка:")
        print(groups.to_string(index=False))


    print(f"\nСохранено: {helpers_path}")

    teams = [args.team] if args.team else service.teams()

    coverage_frames = []
    training_frames = []
    absence_frames = []

    for team_id in teams:
        coverage = service.coverage(team_id)
        coverage_frames.append(coverage)

        if service.task_skill_requirements is None:
            # Без task->skill mapping BF=0 не считаем проблемой.
            critical_skills = coverage[
                (coverage["competency_type"] == "skill")
                & (coverage["team_bus_factor"] >= 1)
                & (coverage["team_bus_factor"] <= 2)
            ]
        else:
            # При явных требованиях можно учитывать и BF=0,
            # но только для required skills.
            critical_skills = coverage[
                (coverage["competency_type"] == "skill")
                & (coverage["team_bus_factor"] <= 2)
                & (coverage["quarter_relevance"] == "required")
            ]

        for item in critical_skills.itertuples(index=False):
            training = service.training_candidates(
                team_id=team_id,
                skill=item.competency,
                top_n=3,
            )
            if not training.empty:
                training_frames.append(training)

    if coverage_frames:
        import pandas as pd

        coverage_all = pd.concat(
            coverage_frames,
            ignore_index=True,
        )
        coverage_path = output_dir / "coverage.csv"
        coverage_all.to_csv(
            coverage_path,
            index=False,
            encoding="utf-8-sig",
        )
        print(f"Сохранено: {coverage_path}")

        if args.team:
            print(f"\n=== COVERAGE: {args.team} ===")
            print(
                coverage_all[
                    coverage_all["team_id"] == args.team
                ].to_string(index=False)
            )

    import pandas as pd

    training_path = output_dir / "training_candidates.csv"

    if training_frames:
        training_all = pd.concat(
            training_frames,
            ignore_index=True,
        )
    else:
        training_all = pd.DataFrame(
            columns=[
                "team_id",
                "skill",
                "engineer_id",
                "role",
                "grade",
                "capacity_rate",
                "training_score",
                "closest_holder",
                "shared_skill_count",
                "shared_skills",
                "profile_similarity",
                "role_similarity",
                "reason",
            ]
        )

    # Всегда перезаписываем файл, чтобы результаты прошлого запуска
    # не выглядели как актуальные.
    training_all.to_csv(
        training_path,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"Сохранено: {training_path} "
        f"({len(training_all)} рекомендаций)"
    )

    # Absence impact — по всем инженерам выбранных команд.
    selected_engineers = service.engineers[
        service.engineers["team_id"].isin(teams)
    ]["engineer_id"].unique()

    for engineer_id in selected_engineers:
        impact = service.employee_absence_impact(
            engineer_id=engineer_id
        )
        if not impact.empty:
            absence_frames.append(impact)

    if absence_frames:
        import pandas as pd

        absence_all = pd.concat(
            absence_frames,
            ignore_index=True,
        )
        absence_path = output_dir / "absence_impact.csv"
        absence_all.to_csv(
            absence_path,
            index=False,
            encoding="utf-8-sig",
        )
        print(f"Сохранено: {absence_path}")


if __name__ == "__main__":
    main()
