"""FastAPI app voor de Cahn Family Task Assistant."""
import os
import secrets
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional

from .task_engine import engine
from .database import (
    seed_initial_data, reset_tasks_2026, update_task_targets, get_all_tasks,
    get_member_by_name, get_last_completion_for_member, delete_completion,
    migrate_add_cascade_delete, migrate_add_schedule_table, migrate_add_missed_tasks_table,
    get_missed_tasks_for_week, get_missed_tasks_for_member,
    migrate_add_fairness_balance_table, get_all_fairness_balances, normalize_fairness_balances,
    get_all_members
)

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


@app.post("/api/migrate/cascade")
async def run_cascade_migration():
    """Voer migratie uit om CASCADE DELETE toe te voegen aan foreign keys.

    Dit zorgt ervoor dat bij het verwijderen van een task of member,
    de gerelateerde completions/absences/swaps automatisch worden verwijderd.
    Veilig om meerdere keren uit te voeren.
    """
    try:
        migrate_add_cascade_delete()
        return {"status": "ok", "message": "CASCADE DELETE constraints toegevoegd"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/migrate/schedule-table")
async def run_schedule_table_migration():
    """Voer migratie uit om schedule_assignments tabel toe te voegen.

    Dit is nodig voor persistent weekroosters.
    Veilig om meerdere keren uit te voeren.
    """
    try:
        migrate_add_schedule_table()
        return {"status": "ok", "message": "schedule_assignments tabel aangemaakt"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/migrate/missed-tasks-table")
async def run_missed_tasks_table_migration():
    """Voer migratie uit om missed_tasks tabel toe te voegen.

    Dit is nodig voor het bijhouden van verzaakte taken.
    Veilig om meerdere keren uit te voeren.
    """
    try:
        migrate_add_missed_tasks_table()
        return {"status": "ok", "message": "missed_tasks tabel aangemaakt"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/migrate/fairness-balance-table")
async def run_fairness_balance_table_migration():
    """Voer migratie uit om fairness_balance tabel toe te voegen.

    Dit is nodig voor het bijhouden van langetermijn eerlijkheid.
    Veilig om meerdere keren uit te voeren.
    """
    try:
        migrate_add_fairness_balance_table()
        return {"status": "ok", "message": "fairness_balance tabel aangemaakt en geinitialiseerd"}
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


@app.post("/api/tasks/update-targets")
async def update_targets():
    """Update alleen de taak-frequenties ZONDER data te verwijderen.

    Dit is veilig om te gebruiken - completions en schedule blijven behouden.
    Alleen de weekly_target, per_child_target en rotation_weeks worden aangepast.
    """
    try:
        update_task_targets()
        return {
            "status": "ok",
            "message": "Taak-targets bijgewerkt (data behouden)",
            "tasks": [
                "uitruimen_ochtend: 3x/week totaal",
                "uitruimen_avond: 7x/week totaal",
                "inruimen: 7x/week totaal",
                "dekken: 7x/week totaal",
                "karton_papier: 2x/week totaal",
                "glas: 1x/week totaal",
                "koken: 1x/maand per kind"
            ]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# === API Endpoints ===

class TaskCompletionRequest(BaseModel):
    member_name: str
    task_name: str


class BulkCompletionItem(BaseModel):
    member_name: str
    task_name: str
    completed_date: Optional[date] = None  # Optioneel: datum waarop taak is gedaan


class BulkCompletionRequest(BaseModel):
    completions: list[BulkCompletionItem]


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


class UndoRequest(BaseModel):
    member_name: str


class UndoTaskRequest(BaseModel):
    """Specifieke taak ongedaan maken."""
    member_name: str
    task_name: str
    completed_date: Optional[date] = None  # Default: vandaag


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


@app.post("/api/complete/bulk")
async def complete_tasks_bulk(request: BulkCompletionRequest):
    """Registreer meerdere taken in één transactie.

    ALLES slaagt of NIETS slaagt - geen partial failures.
    Als één taak of persoon niet gevonden wordt, worden geen taken opgeslagen.
    """
    try:
        # Converteer request naar list of dicts
        tasks_data = [
            {
                "member_name": item.member_name,
                "task_name": item.task_name,
                "completed_date": item.completed_date
            }
            for item in request.completions
        ]

        # Voer alles uit in één transactie
        completions = engine.complete_tasks_bulk(tasks_data)

        return {
            "success": True,
            "message": f"{len(completions)} taken geregistreerd",
            "results": [
                {
                    "member_name": c.member_name,
                    "task_name": c.task_name,
                    "completion_id": c.id
                }
                for c in completions
            ]
        }
    except ValueError as e:
        # Validation error - niets is opgeslagen
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Database error - alles is teruggedraaid
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.post("/api/undo")
async def undo_last_task(request: UndoRequest):
    """Maak de laatst voltooide taak ongedaan voor een gezinslid.

    DEPRECATED: Gebruik /api/undo/task voor specifieke undo.
    Deze endpoint kan conflicten geven bij meerdere ChatGPT sessies.
    """
    member = get_member_by_name(request.member_name)
    if not member:
        raise HTTPException(status_code=404, detail=f"Gezinslid '{request.member_name}' niet gevonden")

    last_completion = get_last_completion_for_member(member.id)
    if not last_completion:
        return {
            "success": False,
            "message": f"{request.member_name} heeft nog geen taken voltooid om ongedaan te maken"
        }

    task_name = last_completion.task_name
    deleted = delete_completion(last_completion.id)

    if deleted:
        return {
            "success": True,
            "message": f"Ongedaan gemaakt: {task_name} van {request.member_name}",
            "undone_task": task_name
        }
    else:
        return {
            "success": False,
            "message": "Kon de taak niet ongedaan maken"
        }


@app.post("/api/undo/task")
async def undo_specific_task(request: UndoTaskRequest):
    """Maak een specifieke taak ongedaan.

    Beter dan /api/undo omdat het specifiek is en geen conflicten geeft
    bij meerdere ChatGPT sessies.

    De taak wordt weer op het rooster gezet (herplanning).
    """
    try:
        result = engine.undo_task_completion(
            request.member_name,
            request.task_name,
            request.completed_date
        )
        return result
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

    Het rooster wordt persistent opgeslagen:
    - Eerste keer in een week: rooster genereren en opslaan
    - Daarna: opgeslagen rooster teruggeven

    Dit toont per dag wie welke taken moet doen, met afvinkbare checkboxes.
    """
    return engine.get_week_schedule()


@app.post("/api/schedule/regenerate")
async def regenerate_schedule():
    """Forceer het opnieuw genereren van het weekrooster.

    LET OP: Dit verwijdert het bestaande rooster voor deze week!
    Alleen gebruiken in uitzonderlijke gevallen (bijv. na database reset).
    """
    try:
        result = engine.regenerate_schedule()
        return {
            "success": True,
            "message": "Rooster opnieuw gegenereerd",
            "schedule": result
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


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


# === Verzaakte Taken ===

@app.get("/api/missed/{member_name}")
async def get_missed_tasks_for_person(member_name: str, limit: int = 20):
    """Haal verzaakte taken op voor een specifiek gezinslid.

    Dit toont een historisch overzicht van taken die niet zijn gedaan.
    Inclusief of ze zijn herplant of vervallen.
    """
    member = get_member_by_name(member_name)
    if not member:
        raise HTTPException(status_code=404, detail=f"Gezinslid '{member_name}' niet gevonden")

    missed = get_missed_tasks_for_member(member.id, limit)
    return [
        {
            "week": f"Week {m.week_number}, {m.year}",
            "original_day": ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"][m.original_day],
            "task": m.task_name,
            "status": "vervallen" if m.expired else f"herplant naar {['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo'][m.rescheduled_to_day]}" if m.rescheduled_to_day is not None else "onbekend",
            "date": m.created_at.isoformat()
        }
        for m in missed
    ]


# === Fairness Balance ===

@app.get("/api/fairness")
async def get_fairness_balances():
    """Haal de fairness balances op voor alle gezinsleden.

    Returns:
        Dict met per persoon hun balance en uitleg.
        Positief = schuld (moet meer doen, bijv. na vakantie)
        Negatief = tegoed (heeft extra gedaan, bijv. siblings vervangen)
    """
    balances = get_all_fairness_balances()
    members = get_all_members()

    result = []
    for member in members:
        balance = balances.get(member.name, 0.0)
        if balance > 0.5:
            status = "schuld"
            explanation = f"Heeft {balance:.1f} taken 'schuld' (bijv. door afwezigheid)"
        elif balance < -0.5:
            status = "tegoed"
            explanation = f"Heeft {abs(balance):.1f} taken 'tegoed' (extra werk gedaan)"
        else:
            status = "in balans"
            explanation = "Eerlijke verdeling"

        result.append({
            "name": member.name,
            "balance": round(balance, 2),
            "status": status,
            "explanation": explanation
        })

    return {
        "fairness_balances": result,
        "info": "Positief = moet meer doen, Negatief = heeft extra gedaan"
    }


@app.post("/api/fairness/normalize")
async def normalize_fairness():
    """Normaliseer alle fairness balances zodat het gemiddelde 0 is.

    Dit is nuttig om drift te voorkomen over lange tijd.
    Wordt automatisch gedaan, maar kan ook handmatig worden uitgevoerd.
    """
    try:
        adjustment = normalize_fairness_balances()
        return {
            "status": "ok",
            "message": "Fairness balances genormaliseerd",
            "adjustment": round(adjustment, 2)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/fairness/reset")
async def reset_fairness():
    """Reset alle fairness balances naar 0.

    LET OP: Dit verwijdert alle opgebouwde schuld/tegoed!
    Alleen gebruiken als je helemaal opnieuw wilt beginnen.
    """
    try:
        members = get_all_members()
        from .database import set_fairness_balance
        for member in members:
            set_fairness_balance(member.id, 0.0)
        return {
            "status": "ok",
            "message": "Alle fairness balances gereset naar 0"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# === Local development ===

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
