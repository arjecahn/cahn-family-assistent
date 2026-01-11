"""FastAPI app voor de Cahn Family Task Assistant."""
import os
import secrets
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional

from .task_engine import engine
from .database import seed_initial_data, reset_tasks_2026, get_all_tasks
from .voice_handlers import handle_google_action

app = FastAPI(
    title="Cahn Family Task Assistant",
    description="Huishoudcoach voor de familie Cahn",
    version="1.0.0"
)

# API Key voor authenticatie (kan worden overschreven via environment variable)
API_KEY = os.getenv("API_KEY", "cahn-family-2026-secret-key")


async def verify_api_key(authorization: Optional[str] = Header(None)):
    """Verifieer de API key uit de Authorization header."""
    # Skip auth voor health check
    if authorization is None:
        raise HTTPException(status_code=401, detail="API key required")

    # Verwacht "Bearer <api_key>" format
    if authorization.startswith("Bearer "):
        token = authorization[7:]
    else:
        token = authorization

    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return token


# Startup event
@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    try:
        seed_initial_data()
    except Exception as e:
        print(f"Database init error (might be OK on first run): {e}")


# Health check
@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/api/init")
async def init_database():
    """Initialiseer de database (eenmalig aanroepen)."""
    try:
        seed_initial_data()
        return {"status": "ok", "message": "Database geinitialiseerd"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/tasks")
async def list_tasks():
    """Haal alle taken op met hun volledige configuratie."""
    tasks = get_all_tasks()
    return [
        {
            "name": t.name,
            "display_name": t.display_name,
            "description": t.description,
            "weekly_target": t.weekly_target,
            "per_child_target": t.per_child_target,
            "rotation_weeks": t.rotation_weeks,
            "time_of_day": t.time_of_day
        }
        for t in tasks
    ]


@app.post("/api/tasks/reset-2026")
async def reset_to_2026():
    """Reset alle taken naar de 2026 afspraken.

    LET OP: Dit verwijdert alle bestaande voltooide taken!
    """
    try:
        reset_tasks_2026()
        return {
            "status": "ok",
            "message": "Taken gereset naar 2026 configuratie",
            "tasks": [
                "uitruimen_ochtend (1x/week per kind)",
                "uitruimen_avond (2x/week per kind)",
                "inruimen (2x/week per kind)",
                "dekken (2x/week per kind)",
                "karton_papier (1x/week per kind)",
                "glas (1x/3 weken per kind)",
                "koken (1x/3 weken per kind)"
            ]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# === API Endpoints ===

class TaskCompletionRequest(BaseModel):
    member_name: str
    task_name: str


class AbsenceRequest(BaseModel):
    member_name: str
    start_date: date
    end_date: date
    reason: Optional[str] = None


class SwapRequest(BaseModel):
    requester_name: str
    target_name: str
    task_name: str
    swap_date: date


class SwapResponse(BaseModel):
    swap_id: str
    accept: bool


@app.get("/api/suggest/{task_name}")
async def suggest_for_task(task_name: str):
    """Suggereer wie een taak moet doen."""
    try:
        suggestion = engine.suggest_member_for_task(task_name)
        return {
            "suggested": suggestion.suggested_member.name,
            "reason": suggestion.reason,
            "scores": [
                {
                    "name": s.member.name,
                    "total_this_week": s.total_tasks_this_week,
                    "score": round(s.weighted_score, 2)
                }
                for s in suggestion.scores
            ]
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/complete")
async def complete_task(request: TaskCompletionRequest):
    """Registreer dat iemand een taak heeft voltooid."""
    try:
        completion = engine.complete_task(request.member_name, request.task_name)
        return {
            "success": True,
            "message": f"{request.member_name} heeft {request.task_name} voltooid!",
            "completion_id": completion.id
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/absence")
async def register_absence(request: AbsenceRequest):
    """Registreer afwezigheid."""
    try:
        absence = engine.register_absence(
            request.member_name,
            request.start_date,
            request.end_date,
            request.reason
        )
        return {
            "success": True,
            "message": f"{request.member_name} is afwezig van {request.start_date} tot {request.end_date}",
            "absence_id": absence.id
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/summary")
async def weekly_summary():
    """Geef het weekoverzicht."""
    return engine.get_weekly_summary()


@app.get("/api/schedule")
async def week_schedule():
    """Haal het weekrooster op met ASCII/emoji overzicht.

    Dit toont per dag wie welke taken moet doen, met afvinkbare checkboxes.
    Gebruik dit om het rooster te tonen aan de kinderen.
    """
    return engine.get_week_schedule()


@app.post("/api/swap/request")
async def request_swap(request: SwapRequest):
    """Vraag een ruil aan."""
    try:
        swap = engine.request_swap(
            request.requester_name,
            request.target_name,
            request.task_name,
            request.swap_date
        )
        return {
            "success": True,
            "message": f"Ruil verzoek gestuurd naar {request.target_name}",
            "swap_id": swap.id
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/swap/respond")
async def respond_to_swap(request: SwapResponse):
    """Reageer op een ruil verzoek."""
    try:
        engine.respond_to_swap(request.swap_id, request.accept)
        status = "geaccepteerd" if request.accept else "afgewezen"
        return {
            "success": True,
            "message": f"Ruil verzoek {status}"
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/swaps/pending/{member_name}")
async def get_pending_swaps(member_name: str):
    """Haal openstaande ruil verzoeken op."""
    swaps = engine.get_pending_swaps(member_name)
    return [
        {
            "swap_id": s.id,
            "from": s.requester_name,
            "task": s.task_name,
            "date": s.swap_date
        }
        for s in swaps
    ]


# === Google Actions Webhook ===

@app.post("/webhook/google")
async def google_actions_webhook(request: dict):
    """
    Webhook endpoint voor Google Actions.
    Ontvangt requests van Google Assistant en stuurt responses terug.
    """
    return handle_google_action(request)


# === Local development ===

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
