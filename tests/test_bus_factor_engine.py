import pandas as pd

from src.bus_factor import BusFactorService


def make_service():
    engineers = pd.DataFrame(
        [
            {
                "engineer_id": "E1",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Senior",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "E2",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "E3",
                "team_id": "B",
                "role": "Разработчик Java",
                "grade": "Senior",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "E4",
                "team_id": "B",
                "role": "Девопс",
                "grade": "Middle",
                "capacity_rate": 0.5,
            },
        ]
    )

    skills = pd.DataFrame(
        [
            {"engineer_id": "E1", "skill": "Kafka"},
            {"engineer_id": "E1", "skill": "Java"},
            {"engineer_id": "E2", "skill": "Java"},
            {"engineer_id": "E3", "skill": "Kafka"},
            {"engineer_id": "E3", "skill": "Java"},
            {"engineer_id": "E4", "skill": "Docker"},
        ]
    )

    tasks = pd.DataFrame(
        [
            {
                "task_id": "T1",
                "team_id": "A",
                "status": "ToDo",
            },
            {
                "task_id": "T2",
                "team_id": "A",
                "status": "Done",
            },
        ]
    )

    role_effort = pd.DataFrame(
        [
            {
                "task_id": "T1",
                "role": "ДевОпс",
                "required_hh": 20,
            },
            {
                "task_id": "T2",
                "role": "Разработчик Java",
                "required_hh": 40,
            },
        ]
    )

    return BusFactorService(
        engineers=engineers,
        engineer_skills=skills,
        tasks=tasks,
        task_role_effort=role_effort,
    )


def test_skill_team_and_org_bus_factor():
    service = make_service()
    coverage = service.skill_coverage("A")
    kafka = coverage[coverage["competency"] == "Kafka"].iloc[0]

    assert kafka["team_bus_factor"] == 1
    assert kafka["organization_bus_factor"] == 2
    assert kafka["coverage_status"] == "cross_team_backup_available"


def test_role_quarter_relevance():
    service = make_service()
    coverage = service.role_coverage("A")
    devops = coverage[coverage["competency"] == "Девопс"].iloc[0]

    assert devops["quarter_relevance"] == "required"
    assert devops["quarter_task_count"] == 1
    assert devops["quarter_required_hours"] == 20


def test_role_not_required_ignores_done_task():
    service = make_service()
    coverage = service.role_coverage("A")
    java = coverage[
        coverage["competency"] == "Разработчик Java"
    ].iloc[0]

    assert java["quarter_relevance"] == "not_required"
    assert java["coverage_status"] == "structural_only"


def test_potential_helper_is_external_holder_only():
    service = make_service()

    helpers = service.get_potential_helpers(
        target_team_id="A",
        competency_type="skill",
        competency="Kafka",
    )

    assert list(helpers["engineer_id"]) == ["E3"]
    assert helpers.iloc[0]["source_team_id"] == "B"
    assert "free_hours" not in helpers.columns


def test_role_helper():
    service = make_service()

    helpers = service.get_potential_helpers(
        target_team_id="A",
        competency_type="role",
        competency="Девопс",
    )

    assert list(helpers["engineer_id"]) == ["E4"]


def test_absence_impact():
    service = make_service()

    impact = service.employee_absence_impact("E1", team_id="A")
    kafka = impact[
        (impact["competency_type"] == "skill")
        & (impact["competency"] == "Kafka")
    ].iloc[0]

    assert kafka["team_bf_before"] == 1
    assert kafka["team_bf_after"] == 0
    assert bool(kafka["becomes_uncovered"]) is True


def test_training_candidate_prefers_java_dev():
    service = make_service()

    training = service.training_candidates(
        team_id="A",
        skill="Kafka",
    )

    assert not training.empty
    assert training.iloc[0]["engineer_id"] == "E2"


def test_all_helpers_excludes_zero_bf_skill_without_task_skill_mapping():
    service = make_service()

    # Docker есть только в другой команде B, но команда A его не имеет.
    # Без task->skill mapping это НЕ должно считаться дефицитом Team A.
    helpers = service.get_all_potential_helpers(max_team_bf=2)

    bad = helpers[
        (helpers["target_team_id"] == "A")
        & (helpers["competency_type"] == "skill")
        & (helpers["competency"] == "Docker")
    ]

    assert bad.empty


def test_all_helpers_includes_required_zero_bf_role():
    service = make_service()

    # DevOps нужен активной задаче T1, но в Team A такого специалиста нет.
    # Поэтому внешний E4 должен быть предложен.
    helpers = service.get_all_potential_helpers(max_team_bf=2)

    devops = helpers[
        (helpers["target_team_id"] == "A")
        & (helpers["competency_type"] == "role")
        & (helpers["competency"] == "Девопс")
    ]

    assert list(devops["engineer_id"]) == ["E4"]
    assert int(devops.iloc[0]["team_bus_factor"]) == 0
    assert devops.iloc[0]["quarter_relevance"] == "required"


def test_all_helpers_excludes_not_required_role():
    service = make_service()

    # Java фигурирует только в Done-задаче, значит в текущем квартальном
    # активном спросе эта роль не должна генерировать helper-рекомендации.
    helpers = service.get_all_potential_helpers(max_team_bf=2)

    java = helpers[
        (helpers["target_team_id"] == "A")
        & (helpers["competency_type"] == "role")
        & (helpers["competency"] == "Разработчик Java")
    ]

    assert java.empty


def test_split_engineer_is_one_helper_row():
    engineers = pd.DataFrame(
        [
            {
                "engineer_id": "E1",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "E2",
                "team_id": "B",
                "role": "Девопс",
                "grade": "Senior",
                "capacity_rate": 0.5,
            },
            {
                "engineer_id": "E2",
                "team_id": "C",
                "role": "Девопс",
                "grade": "Senior",
                "capacity_rate": 0.5,
            },
        ]
    )

    skills = pd.DataFrame(
        [
            {"engineer_id": "E1", "skill": "Java"},
            {"engineer_id": "E2", "skill": "Docker"},
        ]
    )

    service = BusFactorService(
        engineers=engineers,
        engineer_skills=skills,
    )

    helpers = service.get_potential_helpers(
        target_team_id="A",
        competency_type="role",
        competency="Девопс",
    )

    assert len(helpers) == 1
    row = helpers.iloc[0]

    assert row["engineer_id"] == "E2"
    assert row["source_team_id"] == "B, C"
    assert row["capacity_rate"] == 1.0


def test_helper_count_matches_unique_engineers_for_split_assignments():
    engineers = pd.DataFrame(
        [
            {
                "engineer_id": "E1",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "E2",
                "team_id": "B",
                "role": "Девопс",
                "grade": "Senior",
                "capacity_rate": 0.5,
            },
            {
                "engineer_id": "E2",
                "team_id": "C",
                "role": "Девопс",
                "grade": "Senior",
                "capacity_rate": 0.5,
            },
        ]
    )

    skills = pd.DataFrame(
        [
            {"engineer_id": "E1", "skill": "Java"},
            {"engineer_id": "E2", "skill": "Docker"},
        ]
    )

    tasks = pd.DataFrame(
        [
            {
                "task_id": "T1",
                "team_id": "A",
                "status": "ToDo",
            }
        ]
    )

    role_effort = pd.DataFrame(
        [
            {
                "task_id": "T1",
                "role": "Девопс",
                "required_hours": 20,
            }
        ]
    )

    service = BusFactorService(
        engineers=engineers,
        engineer_skills=skills,
        tasks=tasks,
        task_role_effort=role_effort,
    )

    helpers = service.get_all_potential_helpers(max_team_bf=2)
    devops = helpers[
        (helpers["target_team_id"] == "A")
        & (helpers["competency_type"] == "role")
        & (helpers["competency"] == "Девопс")
    ]

    assert len(devops) == 1
    assert devops.iloc[0]["organization_bus_factor"] == 1


def test_training_candidates_ignore_absent_skill_without_task_skill_mapping():
    service = make_service()

    # Docker есть в организации, но в Team A BF=0.
    # Без task->skill mapping это не доказанный дефицит команды.
    training = service.training_candidates(
        team_id="A",
        skill="Docker",
    )

    assert training.empty


def test_training_candidates_exclude_unrelated_employee():
    engineers = pd.DataFrame(
        [
            {
                "engineer_id": "HOLDER",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Senior",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "GOOD",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "RANDOM",
                "team_id": "A",
                "role": "Аналитик",
                "grade": "Senior",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "EXT",
                "team_id": "B",
                "role": "Разработчик Java",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
        ]
    )

    skills = pd.DataFrame(
        [
            {"engineer_id": "HOLDER", "skill": "Kafka"},
            {"engineer_id": "HOLDER", "skill": "Java"},
            {"engineer_id": "HOLDER", "skill": "Spring Boot"},
            {"engineer_id": "HOLDER", "skill": "PostgreSQL"},

            {"engineer_id": "GOOD", "skill": "Java"},
            {"engineer_id": "GOOD", "skill": "Spring Boot"},
            {"engineer_id": "GOOD", "skill": "PostgreSQL"},

            {"engineer_id": "RANDOM", "skill": "BPMN"},
            {"engineer_id": "RANDOM", "skill": "UML"},

            {"engineer_id": "EXT", "skill": "Kafka"},
        ]
    )

    service = BusFactorService(
        engineers=engineers,
        engineer_skills=skills,
    )

    training = service.training_candidates(
        team_id="A",
        skill="Kafka",
        top_n=3,
    )

    assert list(training["engineer_id"]) == ["GOOD"]
    assert int(training.iloc[0]["shared_skill_count"]) == 3
    assert training.iloc[0]["role_similarity"] == 1.0


def test_training_candidates_for_well_covered_skill_are_empty():
    engineers = pd.DataFrame(
        [
            {
                "engineer_id": "E1",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Senior",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "E2",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "E3",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Junior",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "E4",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
        ]
    )

    skills = pd.DataFrame(
        [
            {"engineer_id": "E1", "skill": "Kafka"},
            {"engineer_id": "E2", "skill": "Kafka"},
            {"engineer_id": "E3", "skill": "Kafka"},
            {"engineer_id": "E4", "skill": "Java"},
        ]
    )

    service = BusFactorService(
        engineers=engineers,
        engineer_skills=skills,
    )

    training = service.training_candidates(
        team_id="A",
        skill="Kafka",
    )

    assert training.empty


def test_training_same_role_requires_at_least_one_shared_skill():
    engineers = pd.DataFrame(
        [
            {
                "engineer_id": "HOLDER",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Senior",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "CANDIDATE",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
        ]
    )

    skills = pd.DataFrame(
        [
            {"engineer_id": "HOLDER", "skill": "Kafka"},
            {"engineer_id": "HOLDER", "skill": "Java"},
            {"engineer_id": "CANDIDATE", "skill": "Python"},
        ]
    )

    service = BusFactorService(
        engineers=engineers,
        engineer_skills=skills,
    )

    training = service.training_candidates(
        team_id="A",
        skill="Kafka",
    )

    assert training.empty


def test_training_related_role_with_one_shared_skill_is_excluded():
    engineers = pd.DataFrame(
        [
            {
                "engineer_id": "HOLDER",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Senior",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "CANDIDATE",
                "team_id": "A",
                "role": "Разработчик Android",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
        ]
    )

    skills = pd.DataFrame(
        [
            {"engineer_id": "HOLDER", "skill": "Kafka"},
            {"engineer_id": "HOLDER", "skill": "Java"},
            {"engineer_id": "HOLDER", "skill": "PostgreSQL"},
            {"engineer_id": "CANDIDATE", "skill": "Java"},
            {"engineer_id": "CANDIDATE", "skill": "Kotlin"},
        ]
    )

    service = BusFactorService(
        engineers=engineers,
        engineer_skills=skills,
    )

    training = service.training_candidates(
        team_id="A",
        skill="Kafka",
    )

    assert training.empty


def test_training_related_role_with_two_shared_skills_is_included():
    engineers = pd.DataFrame(
        [
            {
                "engineer_id": "HOLDER",
                "team_id": "A",
                "role": "Разработчик Java",
                "grade": "Senior",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "CANDIDATE",
                "team_id": "A",
                "role": "Разработчик Android",
                "grade": "Middle",
                "capacity_rate": 1.0,
            },
        ]
    )

    skills = pd.DataFrame(
        [
            {"engineer_id": "HOLDER", "skill": "Kafka"},
            {"engineer_id": "HOLDER", "skill": "Java"},
            {"engineer_id": "HOLDER", "skill": "PostgreSQL"},
            {"engineer_id": "HOLDER", "skill": "Docker"},
            {"engineer_id": "CANDIDATE", "skill": "Java"},
            {"engineer_id": "CANDIDATE", "skill": "Docker"},
            {"engineer_id": "CANDIDATE", "skill": "Kotlin"},
        ]
    )

    service = BusFactorService(
        engineers=engineers,
        engineer_skills=skills,
    )

    training = service.training_candidates(
        team_id="A",
        skill="Kafka",
    )

    assert list(training["engineer_id"]) == ["CANDIDATE"]
    assert int(training.iloc[0]["shared_skill_count"]) == 2
