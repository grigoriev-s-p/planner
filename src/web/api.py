"""FastAPI application serving the product UI and planner API."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .constructor import (
    ConstructorDatasetUploadError,
    ConstructorScenario,
    ScenarioValidationError,
    build_constructor_catalog,
    build_constructor_plan,
    build_constructor_plan_from_workbook,
)
from .service import (
    PlanningAPIService,
    ServiceDataError,
    build_bus_factor_payload,
    get_planning_service,
)
from .catalog import (
    ARCHIVE_REASON_LABELS,
    ArchiveConflictError,
    ArchiveValidationError,
    CatalogNotFoundError,
    get_catalog_repository,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(title="PostCode Quarter Planning API", version="1.0.0")


class ArchiveRequest(BaseModel):
    reason_code: str
    reason_text: str | None = None


class DraftApprovalRequest(BaseModel):
    draft_id: str


class PlanDraftStore:
    """Keep one reviewable plan draft and the last approved plan in memory."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._drafts: dict[str, dict] = {}
        self._approved: dict | None = None

    def create(self, payload: dict) -> dict:
        draft_id = uuid4().hex
        draft = deepcopy(payload)
        draft["draft"] = {
            **draft.get("draft", {}),
            "id": draft_id,
            "storage": "server_memory",
            "server_persisted": True,
            "approved": False,
        }
        with self._lock:
            self._drafts = {draft_id: deepcopy(draft)}
        return draft

    def approve(self, draft_id: str) -> dict:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise KeyError(draft_id)
            approved = deepcopy(draft)
            approved["draft"] = {
                **approved.get("draft", {}),
                "approved": True,
            }
            self._approved = approved
            return deepcopy(approved)

    def approved(self) -> dict | None:
        with self._lock:
            return deepcopy(self._approved) if self._approved is not None else None

    def reset(self) -> None:
        with self._lock:
            self._drafts = {}
            self._approved = None


plan_drafts = PlanDraftStore()


def _service():
    try:
        return get_planning_service()
    except ServiceDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _catalog_service() -> PlanningAPIService:
    repository = get_catalog_repository()
    return PlanningAPIService.from_tables(
        repository.active_planning_tables(), plan_version="working-catalog"
    )


def _with_lifecycle(payload: dict, entity_type: str, entity_id: str) -> dict:
    return {**payload, **get_catalog_repository().lifecycle(entity_type, entity_id)}


def _current_dashboard() -> dict:
    return plan_drafts.approved() or deepcopy(_service().dashboard)


def _dashboard_with_lifecycle() -> dict:
    payload = _current_dashboard()
    repository = get_catalog_repository()
    # Current Bus Factor follows the active catalog even when an approved plan
    # remains historically unchanged after an archive action. This calls only
    # the existing analytics service and never launches the quarter planner.
    payload["star_map"] = build_bus_factor_payload(repository.active_planning_tables())
    payload.setdefault("features", {})["bus_factor"] = "AVAILABLE"
    for collection, entity_type, id_field in (
        ("tasks", "task", "task_id"),
        ("teams", "team", "team_id"),
        ("engineers", "engineer", "engineer_id"),
    ):
        payload[collection] = [
            {**item, **repository.lifecycle(entity_type, item[id_field])}
            for item in payload.get(collection, [])
        ]
    if isinstance(payload.get("plan"), dict):
        lifecycle_by_task = {
            item["task_id"]: repository.lifecycle("task", item["task_id"])
            for item in payload["plan"].get("tasks", [])
        }
        payload["plan"]["tasks"] = [
            {**item, **lifecycle_by_task[item["task_id"]]}
            for item in payload["plan"].get("tasks", [])
        ]
    return payload


@app.exception_handler(ServiceDataError)
async def service_data_error_handler(_, error: ServiceDataError):
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/api/health")
def health() -> dict[str, str]:
    _service()
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard() -> dict:
    return _dashboard_with_lifecycle()


@app.get("/api/plan")
def plan() -> dict:
    return _current_dashboard()["plan"]


@app.get("/api/summary")
def summary() -> dict:
    return _current_dashboard()["summary"]


@app.get("/api/kpis")
def kpis() -> dict:
    return _current_dashboard()["kpis"]


@app.get("/api/tasks")
def tasks() -> list[dict]:
    return [
        _with_lifecycle(item, "task", item["task_id"])
        for item in _current_dashboard()["tasks"]
    ]


@app.get("/api/tasks/{task_id}")
def task(task_id: str) -> dict:
    payload = next(
        (item for item in _current_dashboard()["tasks"] if item["task_id"] == task_id),
        None,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown task_id: {task_id}")
    return _with_lifecycle(payload, "task", task_id)


@app.get("/api/initiatives")
def initiatives() -> list[dict]:
    return _current_dashboard()["initiatives"]


@app.get("/api/risks")
def risks() -> list[dict]:
    return _current_dashboard()["risks"]


@app.get("/api/teams")
def teams() -> list[dict]:
    return [
        _with_lifecycle(item, "team", item["team_id"])
        for item in _current_dashboard()["teams"]
    ]


@app.get("/api/teams/{team_id}")
def team(team_id: str) -> dict:
    payload = next(
        (item for item in _current_dashboard()["teams"] if item["team_id"] == team_id),
        None,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown team_id: {team_id}")
    return _with_lifecycle(payload, "team", team_id)


@app.get("/api/engineers")
def engineers() -> list[dict]:
    return [
        _with_lifecycle(item, "engineer", item["engineer_id"])
        for item in _current_dashboard()["engineers"]
    ]


@app.get("/api/engineers/{engineer_id}")
def engineer(engineer_id: str) -> dict:
    payload = next(
        (
            item
            for item in _current_dashboard()["engineers"]
            if item["engineer_id"] == engineer_id
        ),
        None,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown engineer_id: {engineer_id}")
    return _with_lifecycle(payload, "engineer", engineer_id)


@app.get("/api/star-map")
def star_map() -> dict:
    return build_bus_factor_payload(get_catalog_repository().active_planning_tables())


@app.get("/api/plan-constructor/catalog")
def plan_constructor_catalog() -> dict:
    """Return active entities from the persistent working catalog."""

    repository = get_catalog_repository()
    return build_constructor_catalog(
        _catalog_service(), archived_task_ids=repository.archived_ids("task")
    )


@app.post("/api/plan-constructor/plan/upload")
def plan_constructor_plan_upload(
    filename: str,
    content: bytes = Body(media_type="application/octet-stream"),
) -> dict:
    """Build a complete six-sprint plan directly from an uploaded workbook."""

    try:
        return plan_drafts.create(
            build_constructor_plan_from_workbook(
                filename, content, get_catalog_repository()
            )
        )
    except ConstructorDatasetUploadError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Не удалось использовать загруженный датасет.",
                "errors": [str(error)],
            },
        ) from error


@app.post("/api/plan-constructor/plan")
def plan_constructor(scenario: ConstructorScenario) -> dict:
    """Build a six-sprint plan in memory without changing source data."""

    try:
        return plan_drafts.create(
            build_constructor_plan(
                scenario, catalog_tables=get_catalog_repository().tables()
            )
        )
    except ScenarioValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Исправьте ошибки в черновике плана.",
                "errors": list(error.errors),
            },
        ) from error


@app.post("/api/plan-constructor/drafts/approve")
def approve_constructor_plan(request: DraftApprovalRequest) -> dict:
    """Promote the reviewed draft without invoking the planner again."""

    try:
        return plan_drafts.approve(request.draft_id)
    except KeyError as error:
        raise HTTPException(
            status_code=409,
            detail="Предварительный план больше недоступен. Постройте его заново.",
        ) from error


@app.get("/api/catalog/archive-reasons")
def archive_reasons() -> dict:
    return ARCHIVE_REASON_LABELS


def _current_plan_reference_count(entity_type: str, entity_id: str) -> int:
    current_tasks = _current_dashboard()["tasks"]
    if entity_type == "task":
        task_payload = next(
            (item for item in current_tasks if item["task_id"] == entity_id), None
        )
        return int(bool(task_payload and task_payload.get("status") == "PLANNED"))
    if entity_type == "engineer":
        return sum(
            1
            for task_payload in current_tasks
            for allocation in task_payload.get("allocations", [])
            if allocation.get("engineer_id") == entity_id
        )
    if entity_type == "team":
        return sum(
            1
            for task_payload in current_tasks
            if task_payload.get("assigned_team_id") == entity_id
            and task_payload.get("status") == "PLANNED"
        )
    return 0


def _archive_entity(
    entity_type: str, entity_id: str, request: ArchiveRequest
) -> dict:
    references = _current_plan_reference_count(entity_type, entity_id)
    try:
        lifecycle = get_catalog_repository().archive(
            entity_type,
            entity_id,
            request.reason_code,
            request.reason_text,
            current_plan_references=references,
        )
    except CatalogNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ArchiveConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    messages = {
        "task": "Задача удалена из активного каталога.",
        "engineer": "Инженер удалён из активного каталога.",
        "team": "Команда удалена из активного каталога.",
    }
    return {
        "entity": lifecycle,
        "message": messages[entity_type],
        "current_plan_unchanged": True,
        "warning": (
            "Объект участвует в текущем плане. Он исключён из будущих "
            "построений, но текущий план автоматически не пересчитан."
            if references
            else None
        ),
    }


@app.post("/api/tasks/{task_id}/archive")
def archive_task(task_id: str, request: ArchiveRequest) -> dict:
    return _archive_entity("task", task_id, request)


@app.post("/api/engineers/{engineer_id}/archive")
def archive_engineer(engineer_id: str, request: ArchiveRequest) -> dict:
    return _archive_entity("engineer", engineer_id, request)


@app.post("/api/teams/{team_id}/archive")
def archive_team(team_id: str, request: ArchiveRequest) -> dict:
    return _archive_entity("team", team_id, request)


if not FRONTEND_DIR.is_dir():
    raise RuntimeError(f"Frontend directory does not exist: {FRONTEND_DIR}")

# Registered last so /api/* routes always win over the SPA/static fallback.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
