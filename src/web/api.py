"""FastAPI application serving the product UI and planner API."""

from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
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
from .service import ServiceDataError, get_planning_service


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(title="PostCode Quarter Planning API", version="1.0.0")


def _service():
    try:
        return get_planning_service()
    except ServiceDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.exception_handler(ServiceDataError)
async def service_data_error_handler(_, error: ServiceDataError):
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/api/health")
def health() -> dict[str, str]:
    _service()
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard() -> dict:
    return _service().dashboard


@app.get("/api/plan")
def plan() -> dict:
    return _service().plan_payload


@app.get("/api/summary")
def summary() -> dict:
    return _service().summary


@app.get("/api/kpis")
def kpis() -> dict:
    return _service().kpis


@app.get("/api/tasks")
def tasks() -> list[dict]:
    return _service().tasks


@app.get("/api/tasks/{task_id}")
def task(task_id: str) -> dict:
    payload = _service().task_by_id.get(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown task_id: {task_id}")
    return payload


@app.get("/api/initiatives")
def initiatives() -> list[dict]:
    return _service().initiatives


@app.get("/api/risks")
def risks() -> list[dict]:
    return _service().risks


@app.get("/api/teams")
def teams() -> list[dict]:
    return _service().teams


@app.get("/api/teams/{team_id}")
def team(team_id: str) -> dict:
    payload = _service().team_by_id.get(team_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown team_id: {team_id}")
    return payload


@app.get("/api/engineers")
def engineers() -> list[dict]:
    return _service().engineers


@app.get("/api/engineers/{engineer_id}")
def engineer(engineer_id: str) -> dict:
    payload = _service().engineer_by_id.get(engineer_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown engineer_id: {engineer_id}")
    return payload


@app.get("/api/star-map")
def star_map() -> dict:
    return _service().star_map


@app.get("/api/plan-constructor/catalog")
def plan_constructor_catalog() -> dict:
    """Return read-only dataset entities that can seed a browser draft."""

    return build_constructor_catalog(_service())


@app.post("/api/plan-constructor/plan/upload")
def plan_constructor_plan_upload(
    filename: str,
    content: bytes = Body(media_type="application/octet-stream"),
) -> dict:
    """Build a complete six-sprint plan directly from an uploaded workbook."""

    try:
        return build_constructor_plan_from_workbook(filename, content)
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
        return build_constructor_plan(scenario)
    except ScenarioValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Исправьте ошибки в черновике плана.",
                "errors": list(error.errors),
            },
        ) from error


if not FRONTEND_DIR.is_dir():
    raise RuntimeError(f"Frontend directory does not exist: {FRONTEND_DIR}")

# Registered last so /api/* routes always win over the SPA/static fallback.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
