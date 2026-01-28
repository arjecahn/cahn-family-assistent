"""FastAPI app voor de Cahn Family Task Assistant."""
import os
import secrets
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException, Depends, Header, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

from .task_engine import engine
from .database import (
    seed_initial_data, reset_tasks_2026, update_task_targets, get_all_tasks,
    get_member_by_name, get_task_by_name, get_last_completion_for_member, delete_completion,
    migrate_add_cascade_delete, migrate_add_schedule_table, migrate_add_missed_tasks_table,
    migrate_add_member_email, update_member_email, get_all_members,
    get_missed_tasks_for_week, get_missed_tasks_for_member,
    add_push_subscription, delete_push_subscription_by_endpoint,
    get_push_subscriptions_for_member, migrate_add_push_subscriptions_table
)
from .push_notifications import (
    get_vapid_public_key, send_push_notification, send_push_to_all,
    send_morning_reminder, send_evening_reminder
)
from .voice_handlers import handle_google_action
from .calendar_generator import generate_ical

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


@app.post("/api/migrate/member-email")
async def run_member_email_migration():
    """Voer migratie uit om email kolom toe te voegen aan members tabel.

    Veilig om meerdere keren uit te voeren.
    """
    try:
        migrate_add_member_email()
        return {"status": "ok", "message": "email kolom toegevoegd aan members tabel"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/migrate/push-subscriptions")
async def run_push_subscriptions_migration():
    """Voer migratie uit om push_subscriptions tabel toe te voegen.

    Veilig om meerdere keren uit te voeren.
    """
    try:
        migrate_add_push_subscriptions_table()
        return {"status": "ok", "message": "push_subscriptions tabel aangemaakt"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# === Push Notification Endpoints ===

@app.get("/api/vapid-public-key")
async def vapid_public_key():
    """Geef de VAPID public key voor push notification subscriptions."""
    key = get_vapid_public_key()
    if not key:
        raise HTTPException(status_code=503, detail="VAPID keys niet geconfigureerd")
    return {"publicKey": key}


class PushSubscribeRequest(BaseModel):
    member_name: str
    endpoint: str
    p256dh: str
    auth: str


@app.post("/api/push/subscribe")
async def push_subscribe(request: PushSubscribeRequest):
    """Registreer een push notification subscription."""
    try:
        sub = add_push_subscription(
            member_name=request.member_name,
            endpoint=request.endpoint,
            p256dh=request.p256dh,
            auth=request.auth
        )
        return {
            "success": True,
            "message": f"Push notificaties ingeschakeld voor {request.member_name}",
            "subscription_id": sub.id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(request: PushUnsubscribeRequest):
    """Verwijder een push notification subscription."""
    deleted = delete_push_subscription_by_endpoint(request.endpoint)
    return {
        "success": deleted,
        "message": "Subscription verwijderd" if deleted else "Subscription niet gevonden"
    }


class PushTestRequest(BaseModel):
    member_name: str


@app.post("/api/push/test")
async def push_test(request: PushTestRequest):
    """Stuur test notificaties naar een gezinslid (ochtend + avond)."""
    from .database import today_local, get_completions_for_week
    from .task_engine import engine

    today = today_local()
    week_number = today.isocalendar()[1]
    day_of_week = today.weekday()
    day_name = ["ma", "di", "wo", "do", "vr", "za", "zo"][day_of_week]

    # Haal het rooster
    schedule = engine.get_week_schedule()

    # Verzamel taken voor vandaag
    member_tasks = []
    if day_name in schedule.get("schedule", {}):
        day_schedule = schedule["schedule"][day_name]
        for task_name, assigned_member in day_schedule.items():
            if assigned_member == request.member_name:
                member_tasks.append(task_name)

    # Verzamel openstaande taken (niet afgevinkt vandaag)
    completions = get_completions_for_week(week_number)
    completed_today = set()
    for c in completions:
        if c.completed_at.date() == today:
            completed_today.add((c.member_name, c.task_name))

    open_tasks = []
    for task in member_tasks:
        if (request.member_name, task) not in completed_today:
            open_tasks.append(task)

    results = {"morning": None, "evening": None}

    # Stuur ochtend notificatie
    if member_tasks:
        results["morning"] = send_morning_reminder(request.member_name, member_tasks)
    else:
        results["morning"] = {"skipped": True, "reason": "Geen taken vandaag"}

    # Stuur avond notificatie (na 2 sec delay zodat ze apart aankomen)
    import asyncio
    await asyncio.sleep(2)

    if open_tasks:
        results["evening"] = send_evening_reminder(request.member_name, open_tasks)
    else:
        results["evening"] = {"skipped": True, "reason": "Alle taken al gedaan!"}

    return results


@app.get("/api/push/status/{member_name}")
async def push_status(member_name: str):
    """Check of een gezinslid push notificaties heeft ingeschakeld."""
    subs = get_push_subscriptions_for_member(member_name)
    return {
        "member_name": member_name,
        "enabled": len(subs) > 0,
        "device_count": len(subs)
    }


@app.post("/api/push/morning-reminders")
async def send_morning_reminders():
    """Stuur ochtend herinneringen naar alle gezinsleden.

    Dit endpoint kan worden aangeroepen door een externe cron job (bijv. Vercel Cron)
    om 7:00 uur 's ochtends.
    """
    from .database import today_local, get_all_members
    from .task_engine import engine

    today = today_local()
    week_number = today.isocalendar()[1]
    year = today.isocalendar()[0]
    day_of_week = today.weekday()

    # Haal het rooster voor vandaag
    schedule = engine.get_week_schedule()
    members = get_all_members()

    results = {}
    for member in members:
        # Verzamel taken voor dit gezinslid vandaag
        member_tasks = []
        day_name = ["ma", "di", "wo", "do", "vr", "za", "zo"][day_of_week]

        if day_name in schedule.get("schedule", {}):
            day_schedule = schedule["schedule"][day_name]
            for task_name, assigned_member in day_schedule.items():
                if assigned_member == member.name:
                    member_tasks.append(task_name)

        # Stuur notificatie als er taken zijn
        if member_tasks:
            result = send_morning_reminder(member.name, member_tasks)
            results[member.name] = result
        else:
            results[member.name] = {"skipped": True, "reason": "Geen taken vandaag"}

    return {"results": results}


@app.post("/api/push/evening-reminders")
async def send_evening_reminders():
    """Stuur avond herinneringen voor openstaande taken.

    Dit endpoint kan worden aangeroepen door een externe cron job (bijv. Vercel Cron)
    om 18:00 uur.
    """
    from .database import today_local, get_all_members, get_completions_for_week
    from .task_engine import engine

    today = today_local()
    week_number = today.isocalendar()[1]
    year = today.isocalendar()[0]
    day_of_week = today.weekday()

    # Haal het rooster en completions voor vandaag
    schedule = engine.get_week_schedule()
    completions = get_completions_for_week(week_number)
    members = get_all_members()

    # Maak set van voltooide taken vandaag
    completed_today = set()
    for c in completions:
        if c.completed_at.date() == today:
            completed_today.add((c.member_name, c.task_name))

    results = {}
    for member in members:
        # Verzamel openstaande taken voor dit gezinslid vandaag
        open_tasks = []
        day_name = ["ma", "di", "wo", "do", "vr", "za", "zo"][day_of_week]

        if day_name in schedule.get("schedule", {}):
            day_schedule = schedule["schedule"][day_name]
            for task_name, assigned_member in day_schedule.items():
                if assigned_member == member.name:
                    if (member.name, task_name) not in completed_today:
                        open_tasks.append(task_name)

        # Stuur notificatie als er openstaande taken zijn
        if open_tasks:
            result = send_evening_reminder(member.name, open_tasks)
            results[member.name] = result
        else:
            results[member.name] = {"skipped": True, "reason": "Alle taken gedaan!"}

    return {"results": results}


@app.get("/api/members")
async def list_members():
    """Haal alle gezinsleden op met hun email adressen."""
    members = get_all_members()
    return [
        {
            "name": m.name,
            "email": m.email
        }
        for m in members
    ]


class UpdateMemberEmailRequest(BaseModel):
    email: str


@app.put("/api/members/{member_name}/email")
async def set_member_email(member_name: str, request: UpdateMemberEmailRequest):
    """Update de email van een gezinslid."""
    try:
        member = update_member_email(member_name, request.email)
        return {
            "success": True,
            "message": f"Email voor {member_name} ingesteld op {request.email}",
            "member": {"name": member.name, "email": member.email}
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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
    completed_date: Optional[date] = None  # Optioneel: datum waarop taak is gedaan (default: vandaag)


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


class SameDaySwapRequest(BaseModel):
    """Direct ruilen van taken op dezelfde dag."""
    member1_name: str
    member1_task: str
    member2_name: str
    member2_task: str
    swap_date: date


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


@app.get("/api/explain/{task_name}")
async def explain_task_assignment(task_name: str, member: Optional[str] = None):
    """
    Uitgebreide uitleg waarom iemand een taak krijgt toegewezen.

    Dit endpoint is bedoeld om transparantie te bieden aan de kinderen,
    zodat ze kunnen zien dat de verdeling eerlijk is.

    Args:
        task_name: Naam van de taak (bijv. "inruimen", "dekken")
        member: Optioneel - specifiek lid om uit te leggen (default: wie aan de beurt is)

    Returns:
        Gedetailleerde vergelijking met visuele balken en tekstuele uitleg.
    """
    try:
        explanation = engine.explain_task_assignment(task_name, member)

        return {
            "task": explanation.task_display_name,
            "assigned_to": explanation.assigned_to,
            "short_reason": explanation.assigned_to_reason,

            # Vergelijkingstabel
            "comparison": [
                {
                    "name": c.name,
                    "is_assigned": c.is_assigned,
                    "is_available": c.is_available,
                    "tasks_this_week": c.tasks_this_week,
                    "tasks_this_week_bar": c.tasks_this_week_bar,
                    "specific_task_this_month": c.specific_task_this_month,
                    "specific_task_bar": c.specific_task_bar,
                    "days_since_task": c.days_since_task,
                    "days_since_text": c.days_since_text,
                }
                for c in explanation.comparisons
            ],

            # Tekstuele uitleg
            "explanations": {
                "week": explanation.week_explanation,
                "month": explanation.month_explanation,
                "recency": explanation.recency_explanation,
            },

            "conclusion": explanation.conclusion,

            # Voor wie de berekening wil zien
            "raw_scores": explanation.raw_scores,

            # ASCII weergave voor ChatGPT
            "ascii_explanation": _format_ascii_explanation(explanation)
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _format_ascii_explanation(explanation) -> str:
    """Formatteer de uitleg als ASCII art voor ChatGPT."""
    lines = []
    lines.append(f"Waarom moet {explanation.assigned_to} {explanation.task_display_name}?")
    lines.append("")

    # Taken deze week
    lines.append("📊 Taken deze week:")
    for c in explanation.comparisons:
        marker = " 👈" if c.is_assigned else ""
        available = "" if c.is_available else " (afwezig)"
        lines.append(f"   {c.name:6} {c.tasks_this_week_bar} {c.tasks_this_week} taken{available}{marker}")

    lines.append("")

    # Deze taak deze maand
    lines.append(f"🔄 {explanation.task_display_name.capitalize()} deze maand:")
    for c in explanation.comparisons:
        marker = " 👈" if c.is_assigned else ""
        lines.append(f"   {c.name:6} {c.specific_task_bar} {c.specific_task_this_month}x{marker}")

    lines.append("")

    # Recency
    lines.append(f"⏰ Laatst {explanation.task_display_name}:")
    for c in explanation.comparisons:
        marker = " 👈" if c.is_assigned else ""
        lines.append(f"   {c.name:6} {c.days_since_text}{marker}")

    lines.append("")
    lines.append(explanation.conclusion)

    return "\n".join(lines)


@app.post("/api/complete")
async def complete_task(request: TaskCompletionRequest):
    """Registreer dat iemand een taak heeft voltooid.

    Args:
        member_name: Wie heeft de taak gedaan
        task_name: Welke taak
        completed_date: Optioneel - op welke datum (default: vandaag)
    """
    try:
        completion = engine.complete_task(
            request.member_name,
            request.task_name,
            completed_date=request.completed_date
        )
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
    """Registreer afwezigheid en regenereer het rooster."""
    try:
        absence = engine.register_absence(
            request.member_name,
            request.start_date,
            request.end_date,
            request.reason
        )

        # Regenereer het rooster zodat taken worden herverdeeld
        try:
            engine.regenerate_schedule()
        except Exception:
            pass  # Als regeneratie faalt, doorgaan met success response

        return {
            "success": True,
            "message": f"{request.member_name} is afwezig van {request.start_date} tot {request.end_date}. Rooster is aangepast!",
            "absence_id": absence.id
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/absences/upcoming")
async def get_upcoming_absences():
    """Haal aankomende afwezigheden op (komende 2 weken)."""
    from datetime import date, timedelta
    from .database import get_db

    today = date.today()
    two_weeks = today + timedelta(days=14)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, member_name, start_date, end_date, reason
        FROM absences
        WHERE end_date >= %s AND start_date <= %s
        ORDER BY start_date
    """, (today, two_weeks))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "id": str(r["id"]),
            "member": r["member_name"],
            "start": r["start_date"].isoformat(),
            "end": r["end_date"].isoformat(),
            "reason": r["reason"]
        }
        for r in rows
    ]


@app.delete("/api/absence/{absence_id}")
async def delete_absence(absence_id: str):
    """Verwijder een afwezigheid en regenereer het rooster."""
    from .database import get_db

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM absences WHERE id = %s RETURNING member_name", (absence_id,))
    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    if not result:
        raise HTTPException(status_code=404, detail="Afwezigheid niet gevonden")

    # Regenereer het rooster
    try:
        engine.regenerate_schedule()
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Afwezigheid van {result['member_name']} verwijderd. Rooster is aangepast!"
    }


# === Custom Rules Endpoints ===

class CustomRuleRequest(BaseModel):
    member_name: str
    task_name: Optional[str] = None
    day_of_week: Optional[int] = None  # 0=maandag, 6=zondag
    rule_type: str = "unavailable"  # unavailable, never
    description: Optional[str] = None


@app.get("/api/rules")
async def get_rules():
    """Haal alle actieve custom rules op."""
    from .database import get_all_custom_rules
    rules = get_all_custom_rules()
    return {
        "rules": [
            {
                "id": r.id,
                "member_name": r.member_name,
                "task_name": r.task_name,
                "day_of_week": r.day_of_week,
                "rule_type": r.rule_type,
                "description": r.description
            }
            for r in rules
        ]
    }


@app.post("/api/rules")
async def add_rule(request: CustomRuleRequest):
    """Voeg een nieuwe custom rule toe."""
    from .database import add_custom_rule
    rule = add_custom_rule({
        "member_name": request.member_name,
        "task_name": request.task_name,
        "day_of_week": request.day_of_week,
        "rule_type": request.rule_type,
        "description": request.description
    })
    return {
        "success": True,
        "message": f"Regel toegevoegd voor {request.member_name}",
        "rule": {
            "id": rule.id,
            "member_name": rule.member_name,
            "task_name": rule.task_name,
            "day_of_week": rule.day_of_week,
            "rule_type": rule.rule_type,
            "description": rule.description
        }
    }


@app.delete("/api/rules/{rule_id}")
async def delete_rule(rule_id: str):
    """Verwijder een custom rule."""
    from .database import delete_custom_rule
    deleted = delete_custom_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Regel niet gevonden")
    return {"success": True, "message": "Regel verwijderd"}


@app.post("/api/schedule/regenerate")
async def regenerate_schedule():
    """Regenereer het weekrooster met huidige regels en afwezigheden."""
    try:
        engine.regenerate_schedule()
        schedule = engine.get_week_schedule()
        return {
            "success": True,
            "message": "Rooster opnieuw gegenereerd!",
            "schedule": schedule
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# === Extra Task Assignments (handmatig toegevoegde taken) ===

class ExtraTaskRequest(BaseModel):
    member_name: str
    task_name: str
    task_date: date


@app.post("/api/tasks/extra")
async def add_extra_task(request: ExtraTaskRequest):
    """Voeg een extra taak toe aan een dag (zonder af te vinken).

    Dit is voor taken die niet automatisch gepland waren maar wel gedaan
    moeten worden, bijv. "ik ga vrijdag koken".
    """
    from .database import add_extra_task_assignment, get_db

    # Haal member en task op
    member = get_member_by_name(request.member_name)
    if not member:
        raise HTTPException(status_code=404, detail=f"Gezinslid '{request.member_name}' niet gevonden")

    task = get_task_by_name(request.task_name)
    if not task:
        raise HTTPException(status_code=404, detail=f"Taak '{request.task_name}' niet gevonden")

    # Check of taak al gepland staat in het reguliere rooster
    week_number = request.task_date.isocalendar()[1]
    year = request.task_date.isocalendar()[0]
    day_of_week = request.task_date.weekday()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM schedule_assignments
        WHERE week_number = %s AND year = %s AND day_of_week = %s
        AND task_id = %s AND member_id = %s
    """, (week_number, year, day_of_week, int(task.id), int(member.id)))
    already_scheduled = cur.fetchone() is not None
    cur.close()
    conn.close()

    if already_scheduled:
        raise HTTPException(
            status_code=400,
            detail=f"{task.display_name} staat al op jouw rooster voor die dag"
        )

    try:
        result = add_extra_task_assignment(
            task_date=request.task_date,
            task_id=int(task.id),
            task_name=task.display_name,
            member_id=int(member.id),
            member_name=member.name
        )
        return {
            "success": True,
            "message": f"{request.member_name} heeft {request.task_name} toegevoegd voor {request.task_date}",
            "extra_id": result["id"]
        }
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=400, detail="Deze taak heb je al extra toegevoegd voor die dag")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/tasks/extra/{extra_id}")
async def remove_extra_task(extra_id: str):
    """Verwijder een extra toegevoegde taak."""
    from .database import delete_extra_task_assignment

    deleted = delete_extra_task_assignment(extra_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Extra taak niet gevonden")

    return {"success": True, "message": "Extra taak verwijderd"}


@app.get("/api/summary")
async def weekly_summary():
    """Geef het weekoverzicht."""
    return engine.get_weekly_summary()


@app.get("/api/stats")
async def rich_statistics():
    """Uitgebreide statistieken voor de Stand pagina."""
    from .database import get_db, today_local
    from datetime import timedelta
    from collections import defaultdict

    conn = get_db()
    cur = conn.cursor()
    today = today_local()
    current_week = today.isocalendar()[1]
    current_year = today.isocalendar()[0]
    last_week = current_week - 1 if current_week > 1 else 52
    last_week_year = current_year if current_week > 1 else current_year - 1

    # Haal alle members op
    cur.execute("SELECT id, name FROM members")
    members = {r["id"]: r["name"] for r in cur.fetchall()}
    member_names = list(members.values())

    stats = {
        "week_number": current_week,
        "members": {}
    }

    for member_id, member_name in members.items():
        stats["members"][member_name] = {
            "this_week": 0,
            "last_week": 0,
            "this_month": 0,
            "all_time": 0,
            "tasks": {},
            "streak": 0,
            "best_streak": 0,
            "favorite_task": None,
            "favorite_count": 0,
            "by_time_of_day": {"ochtend": 0, "middag": 0, "avond": 0}
        }

    # Completions deze week
    cur.execute("""
        SELECT member_name, task_name, DATE(completed_at) as day
        FROM completions WHERE week_number = %s
    """, (current_week,))
    for r in cur.fetchall():
        if r["member_name"] in stats["members"]:
            stats["members"][r["member_name"]]["this_week"] += 1
            task = r["task_name"]
            if task not in stats["members"][r["member_name"]]["tasks"]:
                stats["members"][r["member_name"]]["tasks"][task] = 0
            stats["members"][r["member_name"]]["tasks"][task] += 1

    # Completions vorige week
    cur.execute("""
        SELECT member_name, COUNT(*) as cnt
        FROM completions WHERE week_number = %s
        GROUP BY member_name
    """, (last_week,))
    for r in cur.fetchall():
        if r["member_name"] in stats["members"]:
            stats["members"][r["member_name"]]["last_week"] = r["cnt"]

    # Completions deze maand
    month_start = today.replace(day=1)
    cur.execute("""
        SELECT member_name, COUNT(*) as cnt
        FROM completions WHERE completed_at >= %s
        GROUP BY member_name
    """, (month_start,))
    for r in cur.fetchall():
        if r["member_name"] in stats["members"]:
            stats["members"][r["member_name"]]["this_month"] = r["cnt"]

    # All-time completions
    cur.execute("""
        SELECT member_name, COUNT(*) as cnt
        FROM completions GROUP BY member_name
    """)
    for r in cur.fetchall():
        if r["member_name"] in stats["members"]:
            stats["members"][r["member_name"]]["all_time"] = r["cnt"]

    # Streaks berekenen (dagen achter elkaar met minstens 1 taak)
    for member_name in member_names:
        cur.execute("""
            SELECT DISTINCT DATE(completed_at) as day
            FROM completions WHERE member_name = %s
            ORDER BY day DESC
        """, (member_name,))
        days = [r["day"] for r in cur.fetchall()]

        if days:
            # Current streak
            streak = 0
            check_day = today
            for d in days:
                if d == check_day or d == check_day - timedelta(days=1):
                    streak += 1
                    check_day = d - timedelta(days=1)
                else:
                    break
            stats["members"][member_name]["streak"] = streak

            # Best streak (simplified - longest consecutive sequence)
            if len(days) > 0:
                best = 1
                current = 1
                for i in range(1, len(days)):
                    if (days[i-1] - days[i]).days == 1:
                        current += 1
                        best = max(best, current)
                    else:
                        current = 1
                stats["members"][member_name]["best_streak"] = best

    # Favoriete taak per persoon (meest gedaan all-time)
    cur.execute("""
        SELECT member_name, task_name, COUNT(*) as cnt
        FROM completions
        GROUP BY member_name, task_name
        ORDER BY member_name, cnt DESC
    """)
    seen = set()
    for r in cur.fetchall():
        if r["member_name"] not in seen and r["member_name"] in stats["members"]:
            stats["members"][r["member_name"]]["favorite_task"] = r["task_name"]
            stats["members"][r["member_name"]]["favorite_count"] = r["cnt"]
            seen.add(r["member_name"])

    # Per time of day (join met tasks tabel)
    cur.execute("""
        SELECT c.member_name, t.time_of_day, COUNT(*) as cnt
        FROM completions c
        JOIN tasks t ON c.task_name = t.display_name OR c.task_name = t.name
        WHERE c.completed_at >= %s
        GROUP BY c.member_name, t.time_of_day
    """, (month_start,))
    for r in cur.fetchall():
        if r["member_name"] in stats["members"] and r["time_of_day"]:
            stats["members"][r["member_name"]]["by_time_of_day"][r["time_of_day"]] = r["cnt"]

    # Gedetailleerde taak breakdown: per taak, per persoon, week/maand/alltime
    # Haal alle taken op
    cur.execute("SELECT name, display_name FROM tasks")
    all_tasks = [(r["name"], r["display_name"]) for r in cur.fetchall()]

    task_stats = {}
    for task_name, display_name in all_tasks:
        task_stats[display_name] = {
            "week": {name: 0 for name in member_names},
            "month": {name: 0 for name in member_names},
            "all_time": {name: 0 for name in member_names}
        }

    # Week counts per task
    cur.execute("""
        SELECT task_name, member_name, COUNT(*) as cnt
        FROM completions WHERE week_number = %s
        GROUP BY task_name, member_name
    """, (current_week,))
    for r in cur.fetchall():
        if r["task_name"] in task_stats and r["member_name"] in member_names:
            task_stats[r["task_name"]]["week"][r["member_name"]] = r["cnt"]

    # Month counts per task
    cur.execute("""
        SELECT task_name, member_name, COUNT(*) as cnt
        FROM completions WHERE completed_at >= %s
        GROUP BY task_name, member_name
    """, (month_start,))
    for r in cur.fetchall():
        if r["task_name"] in task_stats and r["member_name"] in member_names:
            task_stats[r["task_name"]]["month"][r["member_name"]] = r["cnt"]

    # All-time counts per task
    cur.execute("""
        SELECT task_name, member_name, COUNT(*) as cnt
        FROM completions
        GROUP BY task_name, member_name
    """)
    for r in cur.fetchall():
        if r["task_name"] in task_stats and r["member_name"] in member_names:
            task_stats[r["task_name"]]["all_time"][r["member_name"]] = r["cnt"]

    stats["task_breakdown"] = task_stats

    # Totalen en leaderboard
    leaderboard_week = sorted(
        [(name, data["this_week"]) for name, data in stats["members"].items()],
        key=lambda x: -x[1]
    )
    leaderboard_month = sorted(
        [(name, data["this_month"]) for name, data in stats["members"].items()],
        key=lambda x: -x[1]
    )
    leaderboard_alltime = sorted(
        [(name, data["all_time"]) for name, data in stats["members"].items()],
        key=lambda x: -x[1]
    )

    stats["leaderboard"] = {
        "week": leaderboard_week,
        "month": leaderboard_month,
        "all_time": leaderboard_alltime
    }

    # Fun achievements
    achievements = []
    for name, data in stats["members"].items():
        if data["streak"] >= 3:
            achievements.append({"member": name, "badge": "🔥", "text": f"{data['streak']} dagen streak!"})
        if data["this_week"] > data["last_week"] and data["last_week"] > 0:
            achievements.append({"member": name, "badge": "📈", "text": "Meer dan vorige week!"})
        if data["all_time"] >= 50:
            achievements.append({"member": name, "badge": "⭐", "text": "50+ taken all-time!"})
        if data["all_time"] >= 100:
            achievements.append({"member": name, "badge": "🏆", "text": "100+ taken all-time!"})

    stats["achievements"] = achievements

    cur.close()
    conn.close()

    return stats


@app.get("/taken", response_class=HTMLResponse)
async def tasks_pwa():
    """PWA pagina voor het afvinken van taken."""
    return """<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="Chores">
    <meta name="theme-color" content="#4f46e5">
    <meta name="description" content="Huishoudelijke taken voor de familie Cahn">
    <link rel="manifest" href="/manifest.json">
    <link rel="apple-touch-icon" href="/apple-touch-icon.png">
    <link rel="icon" type="image/svg+xml" href="/icon-192.png">
    <title>Family Chores</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            padding-bottom: 80px;
        }
        .container {
            max-width: 400px;
            margin: 0 auto;
        }
        h1 {
            color: white;
            text-align: center;
            margin-bottom: 20px;
            font-size: 24px;
        }

        /* Bottom Navigation */
        .bottom-nav {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: white;
            display: flex;
            justify-content: space-around;
            padding: 8px 0 12px 0;
            box-shadow: 0 -2px 20px rgba(0,0,0,0.1);
            z-index: 50;
        }
        .nav-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 6px 12px;
            border: none;
            background: none;
            color: #64748b;
            font-size: 10px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .nav-item .icon { font-size: 22px; margin-bottom: 2px; }
        .nav-item.active { color: #4f46e5; }
        .nav-item:hover { color: #4f46e5; }

        /* Views */
        .view { display: none; }
        .view.active { display: block; }

        /* Weekrooster view */
        .day-section { margin-bottom: 16px; }
        .day-header {
            font-weight: 600;
            color: #1e293b;
            padding: 8px 0;
            border-bottom: 1px solid #e2e8f0;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .day-header.today { color: #4f46e5; }
        .day-task {
            display: flex;
            align-items: center;
            padding: 10px 0;
            font-size: 14px;
            border-bottom: 1px solid #f1f5f9;
        }
        .day-task .member {
            width: 60px;
            font-weight: 500;
            color: #64748b;
        }
        .day-task .task-name { flex: 1; color: #1e293b; }
        .day-task .status { font-size: 16px; }
        .day-task.completed { opacity: 0.6; text-decoration: line-through; }

        /* Stand view - Rich Statistics */
        .stats-section {
            background: rgba(255,255,255,0.95);
            border-radius: 16px;
            padding: 16px;
            margin-bottom: 16px;
        }
        .stats-section h3 {
            margin: 0 0 12px 0;
            font-size: 16px;
            color: #1e293b;
        }
        .leaderboard-item {
            display: flex;
            align-items: center;
            padding: 12px;
            background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
            border-radius: 12px;
            margin-bottom: 8px;
        }
        .leaderboard-item.gold { background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); }
        .leaderboard-item.silver { background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%); }
        .leaderboard-item.bronze { background: linear-gradient(135deg, #fed7aa 0%, #fdba74 100%); }
        .leaderboard-rank {
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            margin-right: 12px;
        }
        .leaderboard-name { flex: 1; font-weight: 600; color: #1e293b; }
        .leaderboard-score { font-size: 20px; font-weight: 700; color: #4f46e5; }
        .leaderboard-trend { font-size: 12px; margin-left: 8px; }
        .leaderboard-trend.up { color: #22c55e; }
        .leaderboard-trend.down { color: #ef4444; }
        .stat-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
        }
        .stat-card {
            background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
            border-radius: 12px;
            padding: 14px;
            text-align: center;
        }
        .stat-card .value {
            font-size: 28px;
            font-weight: 700;
            color: #4f46e5;
        }
        .stat-card .label {
            font-size: 12px;
            color: #64748b;
            margin-top: 4px;
        }
        .stat-card.streak .value { color: #f97316; }
        .stat-card.alltime .value { color: #8b5cf6; }
        .achievement-badge {
            display: inline-flex;
            align-items: center;
            background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
            border-radius: 20px;
            padding: 6px 12px;
            margin: 4px;
            font-size: 13px;
        }
        .achievement-badge .emoji { font-size: 16px; margin-right: 6px; }
        .task-breakdown {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .task-chip {
            background: #e0e7ff;
            color: #4338ca;
            padding: 6px 12px;
            border-radius: 16px;
            font-size: 13px;
            font-weight: 500;
        }
        .time-bar {
            display: flex;
            height: 24px;
            border-radius: 12px;
            overflow: hidden;
            margin-top: 8px;
        }
        .time-bar .segment {
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            font-weight: 600;
            color: white;
            min-width: 20px;
        }
        .time-bar .ochtend { background: #fbbf24; }
        .time-bar .middag { background: #f97316; }
        .time-bar .avond { background: #8b5cf6; }
        .tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 12px;
        }
        .tab-btn {
            flex: 1;
            padding: 10px;
            border: none;
            background: #e2e8f0;
            border-radius: 8px;
            font-weight: 600;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .tab-btn.active {
            background: #4f46e5;
            color: white;
        }
        .task-table {
            margin-top: 12px;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #e2e8f0;
        }
        .task-table-header, .task-table-row {
            display: flex;
        }
        .task-table-header {
            background: #4f46e5;
            color: white;
            font-weight: 600;
            font-size: 13px;
        }
        .task-table-row {
            border-bottom: 1px solid #e2e8f0;
        }
        .task-table-row:last-child {
            border-bottom: none;
        }
        .task-table-row:nth-child(even) {
            background: #f8fafc;
        }
        .task-col {
            flex: 2;
            padding: 10px 12px;
            font-size: 13px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .member-col {
            flex: 1;
            padding: 10px 8px;
            text-align: center;
            font-size: 14px;
            font-weight: 600;
        }
        .member-col.highlight {
            background: #dcfce7;
            color: #16a34a;
        }
        .radar-container {
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px 0;
        }
        .radar-legend {
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-top: 16px;
            flex-wrap: wrap;
        }
        .radar-legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        .radar-legend-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }
        .progress-rings {
            display: flex;
            justify-content: space-around;
            padding: 20px 0;
        }
        .ring-container {
            text-align: center;
        }
        .ring-label {
            font-size: 14px;
            font-weight: 600;
            margin-top: 8px;
            color: #1e293b;
        }
        .ring-value {
            font-size: 11px;
            color: #64748b;
        }

        /* Afwezigheid view */
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; font-weight: 500; margin-bottom: 6px; color: #1e293b; }
        .form-group input, .form-group select {
            width: 100%;
            padding: 12px;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
            font-size: 16px;
        }
        .form-group input:focus, .form-group select:focus {
            outline: none;
            border-color: #4f46e5;
        }
        .submit-btn {
            width: 100%;
            padding: 14px;
            background: #4f46e5;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
        }
        .submit-btn:hover { background: #4338ca; }
        .success-msg { color: #22c55e; text-align: center; padding: 12px; }
        .error-msg { color: #ef4444; text-align: center; padding: 12px; }

        /* Absence list */
        .absence-item {
            display: flex;
            align-items: center;
            padding: 12px;
            background: #fef3c7;
            border-radius: 8px;
            margin-bottom: 8px;
        }
        .absence-item .emoji { font-size: 24px; margin-right: 12px; }
        .absence-item .details { flex: 1; }
        .absence-item .name { font-weight: 600; color: #1e293b; }
        .absence-item .dates { font-size: 13px; color: #64748b; }
        .absence-item .reason { font-size: 12px; color: #92400e; font-style: italic; }
        .absence-item .delete-btn {
            width: 32px;
            height: 32px;
            border: none;
            background: #fee2e2;
            color: #dc2626;
            border-radius: 50%;
            font-size: 16px;
            cursor: pointer;
            margin-left: 8px;
        }
        .absence-item .delete-btn:hover { background: #fecaca; }
        .card {
            background: white;
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 16px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        }
        .picker {
            display: flex;
            gap: 10px;
            justify-content: center;
            margin-bottom: 20px;
        }
        .picker button {
            padding: 12px 20px;
            border: none;
            border-radius: 25px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            background: rgba(255,255,255,0.2);
            color: white;
            transition: all 0.2s;
        }
        .picker button.active {
            background: white;
            color: #4f46e5;
        }
        .task {
            display: flex;
            align-items: center;
            padding: 16px;
            margin: 8px 0;
            background: #f8fafc;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .task:hover { background: #f1f5f9; }
        .task.done {
            background: #dcfce7;
            text-decoration: line-through;
            opacity: 0.7;
        }
        .task.celebrating {
            animation: celebrate 0.6s ease-out;
        }
        .task.celebrating .check {
            animation: checkPop 0.4s cubic-bezier(0.68, -0.55, 0.265, 1.55);
        }
        @keyframes celebrate {
            0% { transform: scale(1); }
            15% { transform: scale(1.02) rotate(-1deg); }
            30% { transform: scale(1.05) rotate(1deg); background: #bbf7d0; }
            50% { transform: scale(1.02) rotate(-0.5deg); }
            100% { transform: scale(1) rotate(0); }
        }
        @keyframes checkPop {
            0% { transform: scale(1); }
            30% { transform: scale(1.4); }
            50% { transform: scale(0.9); }
            70% { transform: scale(1.15); }
            100% { transform: scale(1); }
        }
        .confetti-container {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 9999;
            overflow: hidden;
        }
        .confetti {
            position: absolute;
            width: 10px;
            height: 10px;
            opacity: 0;
            animation: confettiFall 1.5s ease-out forwards;
        }
        @keyframes confettiFall {
            0% {
                opacity: 1;
                transform: translateY(0) rotate(0deg) scale(1);
            }
            100% {
                opacity: 0;
                transform: translateY(120px) rotate(720deg) scale(0.5);
            }
        }
        .sparkle {
            position: absolute;
            pointer-events: none;
            font-size: 20px;
            animation: sparkleAnim 0.8s ease-out forwards;
        }
        @keyframes sparkleAnim {
            0% { opacity: 1; transform: scale(0) rotate(0deg); }
            50% { opacity: 1; transform: scale(1.2) rotate(180deg); }
            100% { opacity: 0; transform: scale(0.5) rotate(360deg) translateY(-30px); }
        }
        @keyframes megaFadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        @keyframes megaFadeOut {
            from { opacity: 1; }
            to { opacity: 0; }
        }
        @keyframes megaBounceIn {
            0% { transform: scale(0.3); opacity: 0; }
            50% { transform: scale(1.1); }
            70% { transform: scale(0.9); }
            100% { transform: scale(1); opacity: 1; }
        }
        @keyframes megaSpin {
            0% { transform: rotate(0deg) scale(0); }
            50% { transform: rotate(180deg) scale(1.3); }
            100% { transform: rotate(360deg) scale(1); }
        }
        @keyframes megaPulse {
            0% { transform: scale(0); opacity: 0; }
            60% { transform: scale(1.1); }
            100% { transform: scale(1); opacity: 1; }
        }
        @keyframes megaSlideUp {
            from { transform: translateY(30px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        @keyframes matrixFall {
            to { transform: translateY(120vh); }
        }
        @keyframes floatUp {
            0% { transform: translateY(0) rotate(0deg); opacity: 1; }
            100% { transform: translateY(-120vh) rotate(360deg); opacity: 0; }
        }
        @keyframes rainbowSpin {
            to { transform: rotate(360deg); }
        }

        /* Easter Egg: Upside Down Mode */
        .upside-down #mainContainer {
            transform: rotate(180deg);
            transition: transform 1s cubic-bezier(0.68, -0.55, 0.265, 1.55);
        }
        .upside-down .bottom-nav {
            top: 0;
            bottom: auto;
            transform: rotate(180deg);
        }

        /* Easter Egg: Credits Roll */
        .credits-overlay {
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: linear-gradient(to bottom, #0f0f23, #1a1a3e);
            z-index: 99999;
            display: flex;
            align-items: flex-end;
            justify-content: center;
            overflow: hidden;
        }
        .credits-content {
            text-align: center;
            color: #ffd700;
            font-family: Georgia, serif;
            animation: creditsRoll 45s linear forwards;
            padding-bottom: 100vh;
            padding-top: 100vh;
        }
        .credits-content h2 {
            font-size: 28px;
            margin: 40px 0 20px;
            color: #fff;
            text-shadow: 0 0 20px #ffd700;
        }
        .credits-content p {
            font-size: 18px;
            margin: 10px 0;
            color: #ccc;
        }
        .credits-content .star {
            font-size: 24px;
            color: #ffd700;
        }
        @keyframes creditsRoll {
            0% { transform: translateY(100vh); }
            100% { transform: translateY(-100%); }
        }

        /* Easter Egg: Animal Fusion */
        .fusion-overlay {
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: radial-gradient(circle, #1a0a2e 0%, #0d0015 100%);
            z-index: 99999;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
        }
        .magic-circle {
            width: 250px; height: 250px;
            border: 3px solid #8b5cf6;
            border-radius: 50%;
            position: relative;
            animation: magicPulse 2s ease-in-out infinite, magicSpin 10s linear infinite;
            box-shadow: 0 0 50px #8b5cf6, inset 0 0 50px rgba(139, 92, 246, 0.3);
        }
        .magic-circle::before {
            content: '✦';
            position: absolute;
            top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            font-size: 60px;
            color: #ffd700;
            animation: starPulse 1s ease-in-out infinite;
        }
        @keyframes magicPulse {
            0%, 100% { box-shadow: 0 0 50px #8b5cf6, inset 0 0 50px rgba(139, 92, 246, 0.3); }
            50% { box-shadow: 0 0 100px #a78bfa, inset 0 0 80px rgba(167, 139, 250, 0.5); }
        }
        @keyframes magicSpin {
            to { transform: rotate(360deg); }
        }
        @keyframes starPulse {
            0%, 100% { transform: translate(-50%, -50%) scale(1); }
            50% { transform: translate(-50%, -50%) scale(1.3); }
        }
        .fusion-animals {
            position: absolute;
            font-size: 40px;
            animation: orbitAnimal 3s linear infinite;
        }
        @keyframes orbitAnimal {
            to { transform: rotate(360deg) translateX(120px) rotate(-360deg); }
        }
        .mega-creature {
            font-size: 120px;
            animation: creatureAppear 1s cubic-bezier(0.68, -0.55, 0.265, 1.55);
            text-shadow: 0 0 50px #ffd700;
        }
        @keyframes creatureAppear {
            0% { transform: scale(0) rotate(-180deg); opacity: 0; }
            100% { transform: scale(1) rotate(0deg); opacity: 1; }
        }
        .fusion-text {
            color: #ffd700;
            font-size: 18px;
            margin-top: 30px;
            text-align: center;
            font-style: italic;
            text-shadow: 0 0 10px #ffd700;
        }
        .banish-btn {
            margin-top: 20px;
            padding: 15px 30px;
            background: linear-gradient(135deg, #ef4444, #dc2626);
            border: none;
            border-radius: 30px;
            color: white;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            animation: banishPulse 1s ease-in-out infinite;
        }
        @keyframes banishPulse {
            0%, 100% { box-shadow: 0 0 20px #ef4444; }
            50% { box-shadow: 0 0 40px #f87171; }
        }
        .task.banished {
            background: linear-gradient(90deg, #1a1a1a, #2d1f1f) !important;
            position: relative;
            overflow: hidden;
        }
        .task.banished::after {
            content: '🔥';
            position: absolute;
            right: 10px;
            animation: flameDance 0.5s ease-in-out infinite;
        }
        @keyframes flameDance {
            0%, 100% { transform: scale(1) rotate(-5deg); }
            50% { transform: scale(1.2) rotate(5deg); }
        }
        .task.done:hover {
            opacity: 1;
            background: #fef3c7;
        }
        .task.done .check::after {
            content: '↩';
            position: absolute;
            font-size: 10px;
            bottom: -2px;
            right: -2px;
            opacity: 0;
            transition: opacity 0.2s;
        }
        .task.done:hover .check::after {
            opacity: 1;
        }
        .task.done .check {
            position: relative;
        }
        .task .check {
            width: 28px;
            height: 28px;
            border: 3px solid #cbd5e1;
            border-radius: 50%;
            margin-right: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            flex-shrink: 0;
        }
        .task.done .check {
            background: #22c55e;
            border-color: #22c55e;
            color: white;
        }
        .task .info { flex: 1; }
        .task .name { font-weight: 600; color: #1e293b; }
        .task .time { font-size: 13px; color: #64748b; }
        .task .why-btn {
            width: 32px;
            height: 32px;
            border: 2px solid #cbd5e1;
            border-radius: 50%;
            background: white;
            color: #64748b;
            font-weight: bold;
            font-size: 14px;
            cursor: pointer;
            flex-shrink: 0;
            margin-left: 8px;
        }
        .task .why-btn:hover {
            border-color: #4f46e5;
            color: #4f46e5;
        }
        .task.loading {
            pointer-events: none;
            opacity: 0.6;
        }
        .task.loading .check {
            animation: pulse 0.8s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }
        .task .delete-btn {
            width: 28px;
            height: 28px;
            border: 2px solid #fca5a5;
            border-radius: 50%;
            background: white;
            color: #ef4444;
            font-weight: bold;
            font-size: 18px;
            cursor: pointer;
            flex-shrink: 0;
            margin-left: 8px;
            line-height: 1;
        }
        .task .delete-btn:hover {
            background: #fef2f2;
            border-color: #ef4444;
        }

        /* Modal styling */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 100;
            align-items: center;
            justify-content: center;
        }
        .modal-overlay.show { display: flex; }
        .modal {
            background: white;
            border-radius: 16px;
            padding: 24px;
            max-width: 360px;
            width: 90%;
            max-height: 80vh;
            overflow-y: auto;
            position: relative;
        }
        .modal h2 {
            color: #1e293b;
            font-size: 18px;
            margin-bottom: 16px;
        }
        .modal .close-btn {
            position: absolute;
            top: 12px;
            right: 12px;
            width: 30px;
            height: 30px;
            border: none;
            background: #f1f5f9;
            border-radius: 50%;
            font-size: 18px;
            cursor: pointer;
            color: #64748b;
        }
        .modal section {
            margin-bottom: 16px;
        }
        .modal section h3 {
            font-size: 14px;
            color: #4f46e5;
            margin-bottom: 8px;
        }
        .comparison-row {
            display: flex;
            align-items: center;
            padding: 6px 0;
            font-size: 14px;
        }
        .comparison-row.assigned {
            background: #f0fdf4;
            margin: 0 -8px;
            padding: 6px 8px;
            border-radius: 8px;
        }
        .comparison-row .name {
            width: 60px;
            font-weight: 500;
        }
        .comparison-row .bar {
            font-family: monospace;
            margin: 0 8px;
            color: #4f46e5;
        }
        .comparison-row .value {
            color: #64748b;
        }
        .comparison-row .marker {
            margin-left: auto;
            color: #22c55e;
        }
        .modal .conclusion {
            background: #f8fafc;
            padding: 12px;
            border-radius: 8px;
            font-size: 14px;
            color: #1e293b;
        }
        .empty {
            text-align: center;
            color: #64748b;
            padding: 30px;
        }
        .loading {
            text-align: center;
            color: white;
            padding: 40px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 12px;
        }
        .spinner {
            width: 32px;
            height: 32px;
            border: 3px solid rgba(102,126,234,0.3);
            border-top-color: #667eea;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        .loading {
            color: #667eea;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .summary {
            text-align: center;
            color: #64748b;
            font-size: 14px;
            margin-top: 12px;
        }
        .refresh {
            display: block;
            margin: 20px auto;
            padding: 12px 30px;
            background: rgba(255,255,255,0.2);
            color: white;
            border: none;
            border-radius: 25px;
            font-size: 16px;
            cursor: pointer;
        }

        /* Dag navigatie */
        .date-nav {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(255,255,255,0.15);
            border-radius: 16px;
            padding: 12px 16px;
            margin-bottom: 16px;
        }
        .nav-arrow {
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            font-size: 18px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .nav-arrow:active { background: rgba(255,255,255,0.3); }
        .date-display {
            text-align: center;
            color: white;
        }
        .date-day {
            font-size: 18px;
            font-weight: 600;
        }
        .date-full {
            font-size: 13px;
            opacity: 0.8;
        }
        .date-nav.is-today .date-day::before {
            content: '📅 ';
        }
        .date-nav.is-past .date-day::before {
            content: '⏪ ';
        }
        .date-nav.is-future .date-day::before {
            content: '⏩ ';
        }

        /* Taak toevoegen knop */
        .add-task-btn {
            display: block;
            width: 100%;
            margin: 16px 0 8px 0;
            padding: 14px;
            background: rgba(255,255,255,0.9);
            color: #4f46e5;
            border: 2px dashed #4f46e5;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
        }
        .add-task-btn:active { background: rgba(255,255,255,1); }

        /* Extra taak indicator */
        .task.extra::before {
            content: '➕ ';
        }

        /* Fenna's katjes */
        .cats-container {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            overflow: hidden;
            z-index: 0;
            opacity: 0;
            transition: opacity 0.5s;
        }
        .cats-container.active { opacity: 1; }
        .cat {
            position: absolute;
            font-size: 24px;
            animation: float 6s ease-in-out infinite;
            opacity: 0.6;
            pointer-events: auto;
            cursor: pointer;
            transition: transform 0.1s;
        }
        .cat:hover { transform: scale(1.3); }
        .cat.flying-away {
            animation: flyAway 0.8s ease-in forwards !important;
            pointer-events: none;
        }
        @keyframes float {
            0%, 100% { transform: translateY(0) rotate(0deg); }
            25% { transform: translateY(-15px) rotate(5deg); }
            50% { transform: translateY(-5px) rotate(-3deg); }
            75% { transform: translateY(-20px) rotate(3deg); }
        }

        /* Nora's pinguïn */
        .penguin-container {
            display: none;
            text-align: center;
            font-size: 60px;
            padding: 20px 0 40px;
            opacity: 0;
            transition: opacity 0.5s;
        }
        .penguin-container.active {
            display: block;
            opacity: 1;
            animation: waddle 2s ease-in-out infinite;
        }
        @keyframes waddle {
            0%, 100% { transform: rotate(-5deg); }
            50% { transform: rotate(5deg); }
        }

        /* Nora's otters */
        .otters-container {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            overflow: hidden;
            z-index: 0;
            opacity: 0;
            transition: opacity 0.5s;
        }
        .otters-container.active { opacity: 1; }
        .otter {
            position: absolute;
            font-size: 28px;
            animation: swim 5s ease-in-out infinite;
            opacity: 0.7;
            pointer-events: auto;
            cursor: pointer;
            transition: transform 0.1s;
        }
        .otter:hover { transform: scale(1.3); }
        .otter.flying-away {
            animation: flyAway 0.8s ease-in forwards !important;
            pointer-events: none;
        }
        @keyframes swim {
            0%, 100% { transform: translateX(0) translateY(0) rotate(0deg); }
            25% { transform: translateX(10px) translateY(-10px) rotate(10deg); }
            50% { transform: translateX(-5px) translateY(5px) rotate(-5deg); }
            75% { transform: translateX(-10px) translateY(-15px) rotate(5deg); }
        }

        /* Linde's beren en honing */
        .bears-container {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            overflow: hidden;
            z-index: 0;
            opacity: 0;
            transition: opacity 0.5s;
        }
        .bears-container.active { opacity: 1; }
        .bear {
            position: absolute;
            font-size: 28px;
            animation: wobble 3s ease-in-out infinite;
            opacity: 0.85;
            pointer-events: auto;
            cursor: pointer;
            transition: transform 0.1s;
        }
        .bear:hover { transform: scale(1.3); }
        .bear.flying-away {
            animation: flyAway 0.8s ease-in forwards !important;
            pointer-events: none;
        }
        @keyframes wobble {
            0%, 100% { transform: translateY(0) rotate(0deg) scale(1); }
            25% { transform: translateY(-8px) rotate(-5deg) scale(1.05); }
            50% { transform: translateY(0) rotate(5deg) scale(1); }
            75% { transform: translateY(-5px) rotate(-3deg) scale(1.02); }
        }
        @keyframes flyAway {
            0% { transform: scale(1) rotate(0deg); opacity: 1; }
            20% { transform: scale(1.5) rotate(-10deg); opacity: 1; }
            100% { transform: scale(0) rotate(720deg) translateY(-500px); opacity: 0; }
        }
        @keyframes flyAwaySpiral {
            0% { transform: scale(1) rotate(0deg) translate(0, 0); opacity: 1; }
            100% { transform: scale(0) rotate(1080deg) translate(var(--tx), var(--ty)); opacity: 0; }
        }
        @keyframes flyAwayBounce {
            0% { transform: scale(1); opacity: 1; }
            30% { transform: scale(1.8) translateY(20px); opacity: 1; }
            100% { transform: scale(0) translateY(-600px); opacity: 0; }
        }
        @keyframes flyAwayExplode {
            0% { transform: scale(1); opacity: 1; filter: blur(0); }
            50% { transform: scale(2); opacity: 0.8; filter: blur(0); }
            100% { transform: scale(4); opacity: 0; filter: blur(10px); }
        }
        @keyframes flyAwayZoom {
            0% { transform: scale(1) perspective(500px) translateZ(0); opacity: 1; }
            100% { transform: scale(0.1) perspective(500px) translateZ(-1000px) rotate(360deg); opacity: 0; }
        }

        .picker button[data-member="Fenna"].active::after { content: ' 🐱'; }
        .picker button[data-member="Nora"].active::after { content: ' 🐧'; }
        .picker button[data-member="Linde"].active::after { content: ' 🐻'; }
    </style>
</head>
<body>
    <!-- Fenna's zwevende katjes -->
    <div class="cats-container" id="catsContainer"></div>
    <!-- Nora's otters -->
    <div class="otters-container" id="ottersContainer"></div>
    <!-- Linde's beren en honing -->
    <div class="bears-container" id="bearsContainer"></div>

    <div class="container" id="mainContainer">
        <h1 id="appTitle" onclick="handleTitleTap()">Family Chores</h1>

        <div class="picker" id="picker">
            <button data-member="Nora" onclick="selectMember('Nora')">Nora</button>
            <button data-member="Linde" onclick="selectMember('Linde')">Linde</button>
            <button data-member="Fenna" onclick="selectMember('Fenna')">Fenna</button>
        </div>

        <!-- VIEW: Vandaag -->
        <div class="view active" id="viewToday">
            <!-- Dag navigatie -->
            <div class="date-nav">
                <button class="nav-arrow" onclick="changeDay(-1)">◀</button>
                <div class="date-display">
                    <div class="date-day" id="currentDayName">Vandaag</div>
                    <div class="date-full" id="currentDateFull"></div>
                </div>
                <button class="nav-arrow" onclick="changeDay(1)">▶</button>
            </div>

            <div class="card">
                <div id="tasks">
                    <div class="loading">Kies je naam...</div>
                </div>
                <div class="summary" id="summary"></div>
            </div>

            <!-- Taak toevoegen knop -->
            <button class="add-task-btn" onclick="showAddTaskModal()">+ Taak toevoegen</button>
            <button class="refresh" onclick="loadTasks()">Vernieuwen</button>
        </div>

        <!-- VIEW: Weekrooster -->
        <div class="view" id="viewWeek">
            <div class="card">
                <div id="weekSchedule">
                    <div class="loading"><div class="spinner"></div>Laden...</div>
                </div>
            </div>
        </div>

        <!-- VIEW: Stand -->
        <div class="view" id="viewStand">
            <div id="standContent">
                <div class="loading"><div class="spinner"></div>Laden...</div>
            </div>
        </div>

        <!-- VIEW: Afwezigheid -->
        <div class="view" id="viewAbsence">
            <div class="card">
                <h2 style="margin-bottom:16px;color:#1e293b;">🏖️ Afwezigheid melden</h2>
                <div class="form-group">
                    <label>Wie is afwezig?</label>
                    <select id="absenceMember">
                        <option value="Nora">Nora</option>
                        <option value="Linde">Linde</option>
                        <option value="Fenna">Fenna</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Van</label>
                    <input type="date" id="absenceStart">
                </div>
                <div class="form-group">
                    <label>Tot en met</label>
                    <input type="date" id="absenceEnd">
                </div>
                <div class="form-group">
                    <label>Reden (optioneel)</label>
                    <input type="text" id="absenceReason" placeholder="bijv. Logeren bij oma">
                </div>
                <button class="submit-btn" onclick="submitAbsence()">Melden</button>
                <div id="absenceResult"></div>
            </div>

            <div class="card" style="margin-top:16px;">
                <h2 style="margin-bottom:16px;color:#1e293b;">📅 Geplande afwezigheden</h2>
                <div id="upcomingAbsences">
                    <div class="loading"><div class="spinner"></div>Laden...</div>
                </div>
            </div>
        </div>

        <!-- VIEW: Instellingen -->
        <div class="view" id="viewSettings">
            <div class="card">
                <h2 style="margin-bottom:16px;color:#1e293b;">⚙️ Planningsregels</h2>
                <p style="color:#64748b;font-size:14px;margin-bottom:16px;">
                    Voeg regels toe voor wie wanneer welke taken NIET kan doen.
                    Bijv: "Nora kan op donderdag geen glas wegbrengen".
                </p>

                <div class="form-group">
                    <label>Wie?</label>
                    <select id="ruleMember">
                        <option value="Nora">Nora</option>
                        <option value="Linde">Linde</option>
                        <option value="Fenna">Fenna</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Kan niet:</label>
                    <select id="ruleTask">
                        <option value="">Alle taken</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Op dag:</label>
                    <select id="ruleDay">
                        <option value="">Elke dag</option>
                        <option value="0">Maandag</option>
                        <option value="1">Dinsdag</option>
                        <option value="2">Woensdag</option>
                        <option value="3">Donderdag</option>
                        <option value="4">Vrijdag</option>
                        <option value="5">Zaterdag</option>
                        <option value="6">Zondag</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Reden (optioneel)</label>
                    <input type="text" id="ruleDescription" placeholder="bijv. Heeft dan hockey">
                </div>
                <button class="submit-btn" onclick="addRule()">Regel toevoegen</button>
                <div id="ruleResult"></div>
            </div>

            <div class="card" style="margin-top:16px;">
                <h2 style="margin-bottom:16px;color:#1e293b;">📋 Actieve regels</h2>
                <div id="rulesList">
                    <div class="loading">Laden...</div>
                </div>
            </div>

            <div class="card" style="margin-top:16px;">
                <h2 style="margin-bottom:16px;color:#1e293b;">🔄 Rooster opnieuw plannen</h2>
                <p style="color:#64748b;font-size:14px;margin-bottom:16px;">
                    Na het aanpassen van regels moet je het rooster opnieuw genereren.
                </p>
                <button class="submit-btn" onclick="regenerateSchedule()" style="background:#ef4444;">
                    Rooster opnieuw plannen
                </button>
                <div id="regenerateResult"></div>
            </div>

            <div class="card" style="margin-top:16px;">
                <h2 style="margin-bottom:16px;color:#1e293b;">🔔 Push Notificaties</h2>
                <p style="color:#64748b;font-size:14px;margin-bottom:16px;">
                    Ontvang herinneringen op je telefoon: 's ochtends om 7:00 je taken voor vandaag, 's avonds om 18:00 welke nog open staan.
                </p>
                <div id="pushNotSupported" style="display:none;background:#fef3c7;padding:12px;border-radius:8px;margin-bottom:12px;">
                    <span style="color:#92400e;font-size:14px;">
                        ⚠️ Push notificaties worden niet ondersteund.<br>
                        <small>Tip: Installeer de app op je homescreen (iOS 16.4+)</small>
                    </span>
                </div>
                <div id="pushStatus" style="margin-bottom:12px;font-size:14px;"></div>
                <div style="display:flex;flex-direction:column;gap:10px;">
                    <button class="submit-btn" id="enablePushBtn" onclick="enablePushNotifications()" style="background:#22c55e;">
                        🔔 Notificaties inschakelen
                    </button>
                    <button class="submit-btn" id="disablePushBtn" onclick="disablePushNotifications()" style="background:#ef4444;display:none;">
                        🔕 Notificaties uitschakelen
                    </button>
                    <button class="submit-btn" id="testPushBtn" onclick="testPushNotification()" style="background:#8b5cf6;display:none;">
                        📤 Test notificatie sturen
                    </button>
                </div>
                <div id="pushResult" style="margin-top:12px;text-align:center;font-size:13px;"></div>
            </div>

            <div class="card" style="margin-top:16px;">
                <h2 style="margin-bottom:16px;color:#1e293b;">📆 Kalender abonnement</h2>
                <p style="color:#64748b;font-size:14px;margin-bottom:16px;">
                    Voeg je taken toe aan je telefoon-kalender. Kies jouw naam en krijg een herinnering 15 min van tevoren.
                </p>
                <div style="display:flex;flex-direction:column;gap:10px;">
                    <div style="display:flex;gap:8px;">
                        <button class="submit-btn" onclick="subscribeCalendar('nora')" style="background:#ec4899;flex:1;">
                            📅 Nora
                        </button>
                        <button class="submit-btn" onclick="copyCalendarUrl('nora')" style="background:#f9a8d4;padding:14px 16px;" title="Kopieer URL">
                            📋
                        </button>
                    </div>
                    <div style="display:flex;gap:8px;">
                        <button class="submit-btn" onclick="subscribeCalendar('linde')" style="background:#8b5cf6;flex:1;">
                            📅 Linde
                        </button>
                        <button class="submit-btn" onclick="copyCalendarUrl('linde')" style="background:#c4b5fd;padding:14px 16px;" title="Kopieer URL">
                            📋
                        </button>
                    </div>
                    <div style="display:flex;gap:8px;">
                        <button class="submit-btn" onclick="subscribeCalendar('fenna')" style="background:#06b6d4;flex:1;">
                            📅 Fenna
                        </button>
                        <button class="submit-btn" onclick="copyCalendarUrl('fenna')" style="background:#67e8f9;padding:14px 16px;" title="Kopieer URL">
                            📋
                        </button>
                    </div>
                </div>
                <div id="copyResult" style="margin-top:12px;text-align:center;font-size:13px;"></div>
            </div>

            <!-- Tijdelijk uitgeschakeld
            <div class="card" style="margin-top:16px;">
                <h2 style="margin-bottom:16px;color:#1e293b;">🔄 Taken ruilen</h2>
                <p style="color:#64748b;font-size:14px;margin-bottom:16px;">
                    Willen jullie ruilen? Spreek het eerst even af en vul dan hieronder in wie wat ruilt.
                </p>
                <div class="form-group">
                    <label>Datum</label>
                    <input type="date" id="swapDate">
                </div>
                <div style="display:flex;gap:12px;margin-bottom:16px;">
                    <div style="flex:1;">
                        <div class="form-group">
                            <label>Kind 1</label>
                            <select id="swapMember1">
                                <option value="Nora">Nora</option>
                                <option value="Linde">Linde</option>
                                <option value="Fenna">Fenna</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Geeft taak</label>
                            <select id="swapTask1">
                                <option value="">Laden...</option>
                            </select>
                        </div>
                    </div>
                    <div style="display:flex;align-items:center;font-size:24px;padding-top:20px;">⇄</div>
                    <div style="flex:1;">
                        <div class="form-group">
                            <label>Kind 2</label>
                            <select id="swapMember2">
                                <option value="Linde">Linde</option>
                                <option value="Nora">Nora</option>
                                <option value="Fenna">Fenna</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Geeft taak</label>
                            <select id="swapTask2">
                                <option value="">Laden...</option>
                            </select>
                        </div>
                    </div>
                </div>
                <button class="submit-btn" onclick="submitSwap()" style="background:#f59e0b;">🔄 Ruilen</button>
                <div id="swapResult"></div>
            </div>
            -->

            <div class="card" style="margin-top:16px;">
                <label style="display:flex;align-items:center;gap:12px;cursor:pointer;">
                    <input type="checkbox" id="disableEmojis" onchange="toggleEmojis()" style="width:20px;height:20px;">
                    <span style="color:#64748b;font-size:14px;">Enough with the flying emojis! 🙄</span>
                </label>
            </div>
        </div>

        <!-- Nora's pinguïn onderaan content -->
        <div class="penguin-container" id="penguinContainer">🐧</div>

    </div>

    <!-- Bottom Navigation -->
    <nav class="bottom-nav">
        <button class="nav-item active" onclick="showView('viewToday', this)">
            <span class="icon">📋</span>
            <span>Vandaag</span>
        </button>
        <button class="nav-item" onclick="showView('viewWeek', this)">
            <span class="icon">📅</span>
            <span>Week</span>
        </button>
        <button class="nav-item" onclick="showView('viewStand', this)">
            <span class="icon">📊</span>
            <span>Stand</span>
        </button>
        <button class="nav-item" onclick="showView('viewAbsence', this)">
            <span class="icon">🏖️</span>
            <span>Afwezig</span>
        </button>
        <button class="nav-item" onclick="showView('viewSettings', this)">
            <span class="icon">⚙️</span>
            <span>Regels</span>
        </button>
    </nav>

    <!-- What's New Modal -->
    <div class="modal-overlay" id="whatsNewModal" onclick="closeWhatsNew(event)">
        <div class="modal" onclick="event.stopPropagation()" style="text-align:center;">
            <button class="close-btn" onclick="closeWhatsNew()">&times;</button>
            <div style="font-size:48px;margin-bottom:12px;">🔔</div>
            <h2 style="color:#4f46e5;margin-bottom:12px;">Nieuw: Push Notificaties!</h2>
            <p style="color:#1e293b;font-size:15px;line-height:1.5;margin-bottom:16px;">
                Je kunt nu <strong>herinneringen</strong> krijgen op je telefoon:
            </p>
            <div style="background:#f0fdf4;border-radius:12px;padding:14px;margin-bottom:16px;text-align:left;">
                <div style="margin-bottom:8px;">
                    <span style="font-size:18px;">🌅</span>
                    <strong>07:00</strong> - Welke taken je vandaag hebt
                </div>
                <div>
                    <span style="font-size:18px;">🌆</span>
                    <strong>18:00</strong> - Reminder als er nog taken open staan
                </div>
            </div>
            <p style="color:#64748b;font-size:14px;margin-bottom:20px;">
                Zo vergeet je nooit meer je taken!
            </p>
            <button class="submit-btn" onclick="goToNotificationSettings()" style="background:linear-gradient(135deg, #22c55e 0%, #16a34a 100%);margin-bottom:10px;">
                🔔 Notificaties aanzetten
            </button>
            <button onclick="closeWhatsNew()" style="background:none;border:none;color:#64748b;font-size:14px;cursor:pointer;padding:8px;">
                Later misschien
            </button>
        </div>
    </div>

    <!-- Waarom Modal -->
    <div class="modal-overlay" id="whyModal" onclick="closeModal(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <button class="close-btn" onclick="closeModal()">&times;</button>
            <h2 id="modalTitle">Waarom ik?</h2>
            <div id="modalContent">Laden...</div>
        </div>
    </div>

    <!-- Taak Toevoegen Modal -->
    <div class="modal-overlay" id="addTaskModal" onclick="closeAddTaskModal(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <button class="close-btn" onclick="closeAddTaskModal()">&times;</button>
            <h2>Taak toevoegen</h2>
            <p style="color:#64748b;font-size:14px;margin-bottom:16px;">
                Selecteer een taak die je hebt gedaan (of gaat doen) op <span id="addTaskDate"></span>.
            </p>
            <div class="form-group">
                <label>Welke taak?</label>
                <select id="addTaskSelect">
                    <option value="">Laden...</option>
                </select>
            </div>
            <button class="submit-btn" onclick="submitAddTask()">Toevoegen</button>
            <div id="addTaskResult"></div>
        </div>
    </div>

    <script>
        const API = '';
        let currentMember = localStorage.getItem('member');
        let currentDate = new Date();  // Huidige geselecteerde datum
        const catEmojis = ['🐱', '😺', '😸', '🐈', '🐈‍⬛', '😻', '🙀', '😹'];
        const otterEmoji = '🦦';
        const bearEmojis = ['🐻', '🍯', '🐻', '🍯', '🐻‍❄️', '🧸'];
        const dayNamesNL = ['zondag', 'maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag'];

        // === What's New Modal ===
        const WHATS_NEW_VERSION = 'push-notifications-v1';

        function checkWhatsNew() {
            const seen = localStorage.getItem('whatsNewSeen');
            if (seen !== WHATS_NEW_VERSION) {
                // Wacht even tot de app geladen is
                setTimeout(() => {
                    document.getElementById('whatsNewModal').classList.add('show');
                }, 1000);
            }
        }

        function closeWhatsNew(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('whatsNewModal').classList.remove('show');
            localStorage.setItem('whatsNewSeen', WHATS_NEW_VERSION);
        }

        function goToNotificationSettings() {
            closeWhatsNew();
            // Ga naar Regels tab
            showView('viewSettings', document.querySelector('.nav-item:last-child'));
        }

        // Positie aan de rand (niet in het midden waar UI is)
        function edgePosition() {
            // Kies een rand: 0=links, 1=rechts, 2=boven, 3=onder
            const edge = Math.floor(Math.random() * 4);
            let left, top;
            if (edge === 0) { left = Math.random() * 15; top = Math.random() * 100; }
            else if (edge === 1) { left = 85 + Math.random() * 15; top = Math.random() * 100; }
            else if (edge === 2) { left = Math.random() * 100; top = Math.random() * 10; }
            else { left = Math.random() * 100; top = 85 + Math.random() * 15; }
            return { left: left + '%', top: top + '%' };
        }

        // Genereer zwevende katjes voor Fenna
        function initCats() {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            const container = document.getElementById('catsContainer');
            for (let i = 0; i < 12; i++) {
                const cat = document.createElement('div');
                cat.className = 'cat';
                cat.textContent = catEmojis[Math.floor(Math.random() * catEmojis.length)];
                const pos = edgePosition();
                cat.style.left = pos.left;
                cat.style.top = pos.top;
                cat.style.animationDelay = (Math.random() * 6) + 's';
                cat.style.fontSize = (18 + Math.random() * 16) + 'px';
                cat.onclick = (e) => flyAwayFigure(cat, e);
                container.appendChild(cat);
            }
        }

        // Genereer zwevende otters voor Nora (rond de pinguïn)
        function initOtters() {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            const container = document.getElementById('ottersContainer');
            for (let i = 0; i < 10; i++) {
                const otter = document.createElement('div');
                otter.className = 'otter';
                otter.textContent = otterEmoji;
                const pos = edgePosition();
                otter.style.left = pos.left;
                otter.style.top = pos.top;
                otter.style.animationDelay = (Math.random() * 5) + 's';
                otter.style.fontSize = (24 + Math.random() * 20) + 'px';
                otter.onclick = (e) => flyAwayFigure(otter, e);
                container.appendChild(otter);
            }
        }

        // Genereer beren en honing voor Linde
        function initBears() {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            const container = document.getElementById('bearsContainer');
            for (let i = 0; i < 12; i++) {
                const bear = document.createElement('div');
                bear.className = 'bear';
                bear.textContent = bearEmojis[Math.floor(Math.random() * bearEmojis.length)];
                const pos = edgePosition();
                bear.style.left = pos.left;
                bear.style.top = pos.top;
                bear.style.animationDelay = (Math.random() * 4) + 's';
                bear.style.fontSize = (22 + Math.random() * 16) + 'px';
                bear.onclick = (e) => flyAwayFigure(bear, e);
                container.appendChild(bear);
            }
        }

        // Fly away effect voor zwevende figuurtjes
        const flyAwayAnimations = ['flyAway', 'flyAwaySpiral', 'flyAwayBounce', 'flyAwayExplode', 'flyAwayZoom'];
        const flyAwaySounds = [
            [800, 1200, 0.15], // whoosh up
            [400, 200, 0.1],  // pop down
            [600, 900, 0.12], // zip
            [300, 600, 0.1],  // boing
        ];

        function flyAwayFigure(el, event) {
            if (el.classList.contains('flying-away')) return;

            // Random animation
            const anim = flyAwayAnimations[Math.floor(Math.random() * flyAwayAnimations.length)];

            // Random direction for spiral
            const angle = Math.random() * Math.PI * 2;
            const distance = 300 + Math.random() * 400;
            el.style.setProperty('--tx', Math.cos(angle) * distance + 'px');
            el.style.setProperty('--ty', Math.sin(angle) * distance - 200 + 'px');

            // Apply animation
            el.style.animation = `${anim} ${0.5 + Math.random() * 0.5}s ease-in forwards`;
            el.classList.add('flying-away');

            // Sound effect
            playFlyAwaySound();

            // Sparkle burst
            const rect = el.getBoundingClientRect();
            createMiniSparkles(rect.left + rect.width/2, rect.top + rect.height/2, el.textContent);

            // Haptic
            if (navigator.vibrate) navigator.vibrate(30);

            // Remove after animation
            setTimeout(() => el.remove(), 1000);
        }

        function playFlyAwaySound() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                const sound = flyAwaySounds[Math.floor(Math.random() * flyAwaySounds.length)];
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.frequency.setValueAtTime(sound[0], audioCtx.currentTime);
                osc.frequency.exponentialRampToValueAtTime(sound[1], audioCtx.currentTime + 0.15);
                osc.type = 'sine';
                gain.gain.setValueAtTime(sound[2], audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.2);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.2);
            } catch(e) {}
        }

        function createMiniSparkles(x, y, emoji) {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            for (let i = 0; i < 6; i++) {
                const spark = document.createElement('div');
                spark.textContent = emoji;
                spark.style.cssText = `
                    position: fixed; left: ${x}px; top: ${y}px;
                    font-size: ${10 + Math.random() * 10}px;
                    pointer-events: none; z-index: 9999;
                    opacity: 0.8;
                `;
                document.body.appendChild(spark);

                const angle = (i / 6) * Math.PI * 2;
                const dist = 30 + Math.random() * 50;
                spark.animate([
                    { transform: 'scale(1) translate(0, 0)', opacity: 1 },
                    { transform: `scale(0.5) translate(${Math.cos(angle)*dist}px, ${Math.sin(angle)*dist}px)`, opacity: 0 }
                ], { duration: 400, easing: 'ease-out' });

                setTimeout(() => spark.remove(), 400);
            }
        }

        initCats();
        initOtters();
        initBears();

        // === EASTER EGGS ===

        // Easter Egg 1: Title taps (10 = credits, 3 rapid = upside down)
        let titleTaps = 0;
        let titleTapTimer = null;
        let lastTapTime = 0;
        let rapidTaps = 0;

        function handleTitleTap() {
            const now = Date.now();
            titleTaps++;

            // Check for rapid taps (within 300ms)
            if (now - lastTapTime < 300) {
                rapidTaps++;
                if (rapidTaps >= 2) {
                    // Triple rapid tap = upside down mode
                    toggleUpsideDown();
                    rapidTaps = 0;
                    titleTaps = 0;
                    return;
                }
            } else {
                rapidTaps = 0;
            }
            lastTapTime = now;

            // Reset after 2 seconds of no tapping
            clearTimeout(titleTapTimer);
            titleTapTimer = setTimeout(() => {
                titleTaps = 0;
                rapidTaps = 0;
            }, 2000);

            // 10 taps = credits
            if (titleTaps >= 10) {
                showCredits();
                titleTaps = 0;
            }
        }

        // Easter Egg 2: Upside Down Mode
        let isUpsideDown = false;
        function toggleUpsideDown() {
            isUpsideDown = !isUpsideDown;
            document.body.classList.toggle('upside-down', isUpsideDown);

            // Play flip sound
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.frequency.setValueAtTime(isUpsideDown ? 200 : 400, audioCtx.currentTime);
            osc.frequency.exponentialRampToValueAtTime(isUpsideDown ? 400 : 200, audioCtx.currentTime + 0.3);
            gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.3);
            osc.start();
            osc.stop(audioCtx.currentTime + 0.3);

            if (navigator.vibrate) navigator.vibrate([50, 50, 50]);
        }

        // Easter Egg 3: Credits Roll
        function showCredits() {
            if (navigator.vibrate) navigator.vibrate([100, 50, 100, 50, 100]);

            const overlay = document.createElement('div');
            overlay.className = 'credits-overlay';
            overlay.innerHTML = `
                <div class="credits-content">
                    <p class="star">⭐ ⭐ ⭐</p>
                    <h2>FAMILY CHORES</h2>
                    <p>Een productie van</p>
                    <h2>De Familie Cahn</h2>

                    <p style="margin-top: 60px;">~ De Cast ~</p>
                    <h2>🐧 Nora 🦦</h2>
                    <p>Als de Takenverdelingsexpert</p>
                    <h2>🐻 Linde 🍯</h2>
                    <p>Als de Kookprinses</p>
                    <h2>🐱 Fenna 🐈</h2>
                    <p>Als de Mysterieuze Helper</p>

                    <p style="margin-top: 60px;">~ Crew ~</p>
                    <p>Regie: Het Algoritme</p>
                    <p>Productie: Claude AI</p>
                    <p>Catering: De Koelkast</p>
                    <p>Stuntwerk: De Afwasmachine</p>
                    <p>Special Effects: Confetti.js</p>

                    <p style="margin-top: 60px;">~ Speciale Dank ~</p>
                    <p>De Vaatwasser</p>
                    <p>Het Aanrecht</p>
                    <p>De Glasbak</p>
                    <p>De Papierbak</p>

                    <p style="margin-top: 80px; font-size: 14px; color: #666;">
                        Geen taken werden beschadigd<br>
                        tijdens het maken van deze app
                    </p>

                    <p style="margin-top: 60px;" class="star">🌟 THE END 🌟</p>

                    <p style="margin-top: 40px; font-size: 12px; color: #444;">
                        Tik om te sluiten
                    </p>
                </div>
            `;

            // Play credits music
            playCreditsMusic();

            overlay.onclick = () => {
                overlay.style.opacity = '0';
                overlay.style.transition = 'opacity 0.5s';
                setTimeout(() => overlay.remove(), 500);
            };

            document.body.appendChild(overlay);

            // Auto close after animation
            setTimeout(() => {
                if (overlay.parentNode) {
                    overlay.style.opacity = '0';
                    overlay.style.transition = 'opacity 0.5s';
                    setTimeout(() => overlay.remove(), 500);
                }
            }, 16000);
        }

        function playCreditsMusic() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                // Simple epic melody
                const notes = [
                    {f: 261.63, d: 0.5}, {f: 329.63, d: 0.5}, {f: 392.00, d: 0.5}, {f: 523.25, d: 1},
                    {f: 392.00, d: 0.5}, {f: 440.00, d: 0.5}, {f: 523.25, d: 1.5}
                ];
                let time = audioCtx.currentTime;
                notes.forEach(note => {
                    const osc = audioCtx.createOscillator();
                    const gain = audioCtx.createGain();
                    osc.connect(gain);
                    gain.connect(audioCtx.destination);
                    osc.frequency.value = note.f;
                    osc.type = 'sine';
                    gain.gain.setValueAtTime(0.15, time);
                    gain.gain.exponentialRampToValueAtTime(0.01, time + note.d);
                    osc.start(time);
                    osc.stop(time + note.d);
                    time += note.d * 0.9;
                });
            } catch(e) {}
        }

        // Easter Egg 4: Animal Fusion Ritual
        const fusionSequence = [];
        const fusionRequired = ['cat', 'otter', 'bear'];
        let fusionTimeout = null;

        function trackFusionClick(type) {
            fusionSequence.push(type);

            // Reset after 5 seconds of no clicks
            clearTimeout(fusionTimeout);
            fusionTimeout = setTimeout(() => fusionSequence.length = 0, 5000);

            // Check if sequence matches
            let matches = true;
            for (let i = 0; i < fusionSequence.length; i++) {
                if (fusionSequence[i] !== fusionRequired[i]) {
                    matches = false;
                    fusionSequence.length = 0;
                    break;
                }
            }

            if (matches && fusionSequence.length >= fusionRequired.length) {
                fusionSequence.length = 0;
                triggerAnimalFusion();
            }
        }

        function triggerAnimalFusion() {
            if (navigator.vibrate) navigator.vibrate([100, 100, 100, 100, 300]);

            const overlay = document.createElement('div');
            overlay.className = 'fusion-overlay';

            // Phase 1: Magic circle with orbiting animals
            overlay.innerHTML = `
                <div class="magic-circle">
                    <div class="fusion-animals" style="animation-delay: 0s;">🐱</div>
                    <div class="fusion-animals" style="animation-delay: -1s;">🦦</div>
                    <div class="fusion-animals" style="animation-delay: -2s;">🐻</div>
                </div>
                <div class="fusion-text">De dieren fuseren...</div>
            `;

            document.body.appendChild(overlay);
            playFusionSound();

            // Phase 2: Creature appears
            setTimeout(() => {
                overlay.innerHTML = `
                    <div class="mega-creature">🦁</div>
                    <div class="fusion-text">
                        <strong>DE TAKENVERNIETIGER IS ONTWAAKT!</strong><br><br>
                        "Sterfelijke ${currentMember}... je hebt me gesommoned.<br>
                        Kies één taak om voor EEUWIG te BANNEN!"
                    </div>
                    <button class="banish-btn" onclick="chooseBanishTask(this.parentNode.parentNode)">
                        🔥 KIES EEN TAAK OM TE VERNIETIGEN 🔥
                    </button>
                `;

                // Epic reveal sound
                playCreatureRevealSound();
            }, 3000);
        }

        function playFusionSound() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                // Rising mystical sound
                for (let i = 0; i < 10; i++) {
                    setTimeout(() => {
                        const osc = audioCtx.createOscillator();
                        const gain = audioCtx.createGain();
                        osc.connect(gain);
                        gain.connect(audioCtx.destination);
                        osc.frequency.value = 200 + i * 50;
                        osc.type = 'sine';
                        gain.gain.setValueAtTime(0.1, audioCtx.currentTime);
                        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.3);
                        osc.start();
                        osc.stop(audioCtx.currentTime + 0.3);
                    }, i * 250);
                }
            } catch(e) {}
        }

        function playCreatureRevealSound() {
            try {
                const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                // Dramatic chord
                [261.63, 329.63, 392.00, 523.25].forEach(freq => {
                    const osc = audioCtx.createOscillator();
                    const gain = audioCtx.createGain();
                    osc.connect(gain);
                    gain.connect(audioCtx.destination);
                    osc.frequency.value = freq;
                    osc.type = 'triangle';
                    gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
                    gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 2);
                    osc.start();
                    osc.stop(audioCtx.currentTime + 2);
                });
            } catch(e) {}
        }

        function chooseBanishTask(overlay) {
            overlay.remove();

            // Let user pick a task
            const tasks = document.querySelectorAll('.task:not(.done):not(.banished)');
            if (tasks.length === 0) {
                alert('Er zijn geen taken om te vernietigen! 😈');
                return;
            }

            // Highlight tasks as clickable
            tasks.forEach(task => {
                task.style.cursor = 'pointer';
                task.style.boxShadow = '0 0 20px #ef4444';
                task.style.animation = 'banishPulse 0.5s ease-in-out infinite';

                const handler = () => {
                    // Banish this task!
                    task.classList.add('banished');
                    task.style.boxShadow = '';
                    task.style.animation = '';
                    task.style.cursor = '';

                    // Remove handlers from other tasks
                    tasks.forEach(t => {
                        t.style.boxShadow = '';
                        t.style.animation = '';
                        t.style.cursor = '';
                        t.onclick = null;
                    });

                    // Epic banish effect
                    createConfetti(window.innerWidth / 2, window.innerHeight / 2);
                    if (navigator.vibrate) navigator.vibrate([200, 100, 200]);

                    // Restore original onclick after a moment
                    setTimeout(() => {
                        task.onclick = null;
                    }, 100);
                };

                task.addEventListener('click', handler, { once: true });
            });
        }

        // Track animal clicks for fusion
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('cat')) trackFusionClick('cat');
            if (e.target.classList.contains('otter')) trackFusionClick('otter');
            if (e.target.classList.contains('bear')) trackFusionClick('bear');
        }, true);

        if (currentMember) {
            selectMember(currentMember);
        }

        function selectMember(name) {
            currentMember = name;
            localStorage.setItem('member', name);
            document.querySelectorAll('.picker button').forEach(b => {
                b.classList.toggle('active', b.dataset.member === name);
            });

            // Toon decoraties per kind
            document.getElementById('catsContainer').classList.toggle('active', name === 'Fenna');
            document.getElementById('ottersContainer').classList.toggle('active', name === 'Nora');
            document.getElementById('penguinContainer').classList.toggle('active', name === 'Nora');
            document.getElementById('bearsContainer').classList.toggle('active', name === 'Linde');

            loadTasks();
        }

        // === DAG NAVIGATIE ===
        function formatDateISO(d) {
            return d.toISOString().split('T')[0];
        }

        function formatDateNL(d) {
            return d.toLocaleDateString('nl-NL', {weekday: 'long', day: 'numeric', month: 'long'});
        }

        function updateDateDisplay(data) {
            const nav = document.querySelector('.date-nav');
            const dayEl = document.getElementById('currentDayName');
            const fullEl = document.getElementById('currentDateFull');

            // Update classes
            nav.classList.remove('is-today', 'is-past', 'is-future');
            if (data.is_today) nav.classList.add('is-today');
            else if (data.is_past) nav.classList.add('is-past');
            else nav.classList.add('is-future');

            // Update tekst
            if (data.is_today) {
                dayEl.textContent = 'Vandaag';
            } else {
                // Capitalize first letter
                dayEl.textContent = data.day.charAt(0).toUpperCase() + data.day.slice(1);
            }
            fullEl.textContent = formatDateNL(currentDate);
        }

        function changeDay(delta) {
            currentDate.setDate(currentDate.getDate() + delta);
            loadTasks();
        }

        async function loadTasks() {
            if (!currentMember) return;

            document.getElementById('tasks').innerHTML = '<div class="loading"><div class="spinner"></div>Laden...</div>';

            try {
                const dateStr = formatDateISO(currentDate);
                const res = await fetch(API + '/api/my-tasks/' + currentMember + '?date=' + dateStr);
                const data = await res.json();
                updateDateDisplay(data);
                renderTasks(data);
            } catch (e) {
                document.getElementById('tasks').innerHTML = '<div class="empty">Fout bij laden</div>';
            }
        }

        function renderTasks(data) {
            const tasks = [...data.open, ...data.done];

            if (tasks.length === 0) {
                const dayLabel = data.is_today ? 'vandaag' : 'op ' + data.day;
                document.getElementById('tasks').innerHTML = '<div class="empty">Geen taken ' + dayLabel + '!</div>';
                document.getElementById('summary').textContent = '';
                return;
            }

            const html = tasks.map(t => {
                const timeLabel = {ochtend: 'Ochtend', middag: 'Middag', avond: 'Avond'}[t.time_of_day] || '';
                const isExtra = t.extra === true;
                const extraClass = isExtra ? 'extra' : '';
                const deleteBtn = isExtra && t.extra_id ? `<button class="delete-btn" onclick="removeExtraTask(${t.extra_id}, event)" title="Verwijder extra taak">×</button>` : '';
                return `
                    <div class="task ${t.completed ? 'done' : ''} ${extraClass}" data-task="${t.task_name}">
                        <div class="check" onclick="toggleTask('${t.task_name}', ${t.completed}, event)">${t.completed ? '✓' : ''}</div>
                        <div class="info" onclick="toggleTask('${t.task_name}', ${t.completed}, event)">
                            <div class="name">${t.task_name}</div>
                            <div class="time">${timeLabel}${isExtra ? ' (extra)' : ''}</div>
                        </div>
                        ${deleteBtn}
                        <button class="why-btn" onclick="showWhy('${t.task_name}')" title="Waarom ik?">?</button>
                    </div>
                `;
            }).join('');

            document.getElementById('tasks').innerHTML = html;
            document.getElementById('summary').textContent = data.summary;
        }

        // === CELEBRATION EFFECTS ===
        const celebrationSound = new Audio('data:audio/wav;base64,UklGRl4FAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YToFAAB4eHh5eXp6e3t8fH19fn5/f4CAgYGCgoODhISFhYaGh4eIiImJioqLi4yMjY2Ojo+PkJCRkZKSk5OUlJWVlpaXl5iYmZmampubm5ycnZ2enp+foKChoaKio6OkpKWlpqanp6ioqamqqqqrq6ysra2urq+vsLCxsbKys7O0tLW1tra3t7i4ubm6uru7vLy9vb6+v7/AwMHBwsLDw8TExcXGxsfHyMjJycrKy8vMzM3Nzs7Pz9DQ0dHS0tPT1NTV1dbW19fY2NnZ2tra29vb3Nzd3d7e39/g4OHh4uLj4+Tk5eXm5ufn6Ojp6erq6+vs7O3t7u7v7/Dw8fHy8vPz9PT19fb29/f4+Pn5+vr7+/z8/f3+/v//');

        function triggerCelebration(taskEl, event) {
            // Haptic feedback
            if (navigator.vibrate) navigator.vibrate([50, 30, 100]);

            // Sound
            celebrationSound.currentTime = 0;
            celebrationSound.volume = 0.3;
            celebrationSound.play().catch(() => {});

            // Animation class
            taskEl.classList.add('celebrating');
            setTimeout(() => taskEl.classList.remove('celebrating'), 600);

            // Confetti burst from click position
            const rect = taskEl.getBoundingClientRect();
            const x = event ? event.clientX : rect.left + rect.width / 2;
            const y = event ? event.clientY : rect.top + rect.height / 2;
            createConfetti(x, y);
            createSparkles(x, y);
        }

        function createConfetti(x, y) {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            const container = document.createElement('div');
            container.className = 'confetti-container';
            document.body.appendChild(container);

            const colors = ['#22c55e', '#4f46e5', '#f97316', '#ec4899', '#eab308', '#06b6d4'];
            const shapes = ['●', '■', '▲', '★', '♦', '●'];

            for (let i = 0; i < 30; i++) {
                const confetti = document.createElement('div');
                confetti.className = 'confetti';
                confetti.textContent = shapes[Math.floor(Math.random() * shapes.length)];
                confetti.style.left = x + 'px';
                confetti.style.top = y + 'px';
                confetti.style.color = colors[Math.floor(Math.random() * colors.length)];
                confetti.style.fontSize = (8 + Math.random() * 12) + 'px';
                confetti.style.setProperty('--tx', (Math.random() - 0.5) * 200 + 'px');
                confetti.style.animation = `confettiFall ${0.8 + Math.random() * 0.7}s ease-out forwards`;
                confetti.style.animationDelay = (Math.random() * 0.1) + 's';

                // Custom trajectory
                const angle = (Math.random() * 360) * (Math.PI / 180);
                const velocity = 50 + Math.random() * 100;
                const tx = Math.cos(angle) * velocity;
                const ty = Math.sin(angle) * velocity - 50;
                confetti.animate([
                    { transform: 'translate(0, 0) rotate(0deg) scale(1)', opacity: 1 },
                    { transform: `translate(${tx}px, ${ty}px) rotate(${Math.random() * 720}deg) scale(0.5)`, opacity: 0 }
                ], { duration: 1000 + Math.random() * 500, easing: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)' });

                container.appendChild(confetti);
            }

            setTimeout(() => container.remove(), 2000);
        }

        function createSparkles(x, y) {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            const sparkles = ['✨', '⭐', '🌟', '💫', '✧', '★'];
            for (let i = 0; i < 8; i++) {
                const sparkle = document.createElement('div');
                sparkle.className = 'sparkle';
                sparkle.textContent = sparkles[Math.floor(Math.random() * sparkles.length)];
                sparkle.style.left = (x + (Math.random() - 0.5) * 60) + 'px';
                sparkle.style.top = (y + (Math.random() - 0.5) * 60) + 'px';
                sparkle.style.animationDelay = (Math.random() * 0.2) + 's';
                document.body.appendChild(sparkle);
                setTimeout(() => sparkle.remove(), 1000);
            }
        }

        // === MEGA CELEBRATION - ALL TASKS DONE! ===
        const megaMessages = [
            { text: "LEGENDARY! 🏆", sub: "Alle taken gedaan!" },
            { text: "UNSTOPPABLE! 🔥", sub: "Je bent on fire!" },
            { text: "SUPERSTER! ⭐", sub: "Wat een held!" },
            { text: "PERFECTIE! 💎", sub: "Alles afgevinkt!" },
            { text: "CHAMPION! 🥇", sub: "De beste!" },
            { text: "GEWELDIG! 🚀", sub: "To the moon!" },
            { text: "HELD! 🦸", sub: "Super powers!" },
            { text: "KING/QUEEN! 👑", sub: "Bow down!" },
            { text: "EPIC WIN! 🎮", sub: "Achievement unlocked!" },
            { text: "FLAWLESS! 💯", sub: "100% complete!" }
        ];

        const megaThemes = [
            { colors: ['#22c55e', '#4ade80', '#86efac'], bg: 'linear-gradient(135deg, #22c55e, #16a34a)', emoji: '🌟' },
            { colors: ['#8b5cf6', '#a78bfa', '#c4b5fd'], bg: 'linear-gradient(135deg, #8b5cf6, #7c3aed)', emoji: '💜' },
            { colors: ['#f97316', '#fb923c', '#fdba74'], bg: 'linear-gradient(135deg, #f97316, #ea580c)', emoji: '🔥' },
            { colors: ['#ec4899', '#f472b6', '#f9a8d4'], bg: 'linear-gradient(135deg, #ec4899, #db2777)', emoji: '💖' },
            { colors: ['#eab308', '#facc15', '#fde047'], bg: 'linear-gradient(135deg, #eab308, #ca8a04)', emoji: '⭐' },
            { colors: ['#06b6d4', '#22d3ee', '#67e8f9'], bg: 'linear-gradient(135deg, #06b6d4, #0891b2)', emoji: '💎' },
            { colors: ['#ef4444', '#f87171', '#fca5a5'], bg: 'linear-gradient(135deg, #ef4444, #dc2626)', emoji: '❤️‍🔥' }
        ];

        const megaEffects = ['fireworks', 'rainbow', 'matrix', 'hearts', 'stars'];

        function triggerMegaCelebration() {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            if (navigator.vibrate) navigator.vibrate([100, 50, 100, 50, 200, 100, 300]);

            const message = megaMessages[Math.floor(Math.random() * megaMessages.length)];
            const theme = megaThemes[Math.floor(Math.random() * megaThemes.length)];
            const effect = megaEffects[Math.floor(Math.random() * megaEffects.length)];

            // Create overlay
            const overlay = document.createElement('div');
            overlay.id = 'megaCelebration';
            overlay.innerHTML = `
                <div class="mega-bg"></div>
                <div class="mega-content">
                    <div class="mega-emoji">${theme.emoji}</div>
                    <div class="mega-text">${message.text}</div>
                    <div class="mega-sub">${message.sub}</div>
                    <div class="mega-name">${currentMember}</div>
                </div>
            `;
            overlay.style.cssText = `
                position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                z-index: 10000; display: flex; align-items: center; justify-content: center;
                animation: megaFadeIn 0.3s ease-out;
            `;
            document.body.appendChild(overlay);

            // Style the content
            const bg = overlay.querySelector('.mega-bg');
            bg.style.cssText = `
                position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                background: ${theme.bg}; opacity: 0.95;
            `;

            const content = overlay.querySelector('.mega-content');
            content.style.cssText = `
                position: relative; text-align: center; color: white; z-index: 1;
                animation: megaBounceIn 0.6s cubic-bezier(0.68, -0.55, 0.265, 1.55);
            `;

            overlay.querySelector('.mega-emoji').style.cssText = `
                font-size: 80px; animation: megaSpin 1s ease-out;
            `;
            overlay.querySelector('.mega-text').style.cssText = `
                font-size: 42px; font-weight: 900; margin: 20px 0 10px;
                text-shadow: 0 4px 20px rgba(0,0,0,0.3);
                animation: megaPulse 0.5s ease-out 0.3s both;
            `;
            overlay.querySelector('.mega-sub').style.cssText = `
                font-size: 20px; opacity: 0.9;
                animation: megaSlideUp 0.5s ease-out 0.5s both;
            `;
            overlay.querySelector('.mega-name').style.cssText = `
                font-size: 28px; font-weight: 700; margin-top: 30px;
                animation: megaSlideUp 0.5s ease-out 0.7s both;
            `;

            // Trigger effect
            if (effect === 'fireworks') createFireworks(theme.colors);
            else if (effect === 'rainbow') createRainbow();
            else if (effect === 'matrix') createMatrix(theme.colors[0]);
            else if (effect === 'hearts') createFloatingEmojis(['❤️', '💖', '💕', '💗', '💓']);
            else if (effect === 'stars') createFloatingEmojis(['⭐', '🌟', '✨', '💫', '🌠']);

            // Mega confetti from multiple points
            setTimeout(() => {
                for (let i = 0; i < 5; i++) {
                    setTimeout(() => {
                        createConfetti(Math.random() * window.innerWidth, Math.random() * window.innerHeight * 0.5);
                    }, i * 200);
                }
            }, 300);

            // Play victory sound
            playVictorySound();

            // Close on click or after delay
            overlay.onclick = () => closeMegaCelebration(overlay);
            setTimeout(() => closeMegaCelebration(overlay), 5000);
        }

        function closeMegaCelebration(overlay) {
            if (!overlay.parentNode) return;
            overlay.style.animation = 'megaFadeOut 0.3s ease-out forwards';
            setTimeout(() => overlay.remove(), 300);
        }

        function createFireworks(colors) {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            for (let i = 0; i < 8; i++) {
                setTimeout(() => {
                    const x = Math.random() * window.innerWidth;
                    const y = Math.random() * window.innerHeight * 0.6;
                    createFirework(x, y, colors);
                }, i * 300);
            }
        }

        function createFirework(x, y, colors) {
            const container = document.createElement('div');
            container.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10001;';
            document.body.appendChild(container);

            for (let i = 0; i < 20; i++) {
                const particle = document.createElement('div');
                const angle = (i / 20) * Math.PI * 2;
                const velocity = 100 + Math.random() * 100;
                const color = colors[Math.floor(Math.random() * colors.length)];

                particle.style.cssText = `
                    position: absolute; left: ${x}px; top: ${y}px;
                    width: 8px; height: 8px; border-radius: 50%;
                    background: ${color}; box-shadow: 0 0 10px ${color};
                `;

                particle.animate([
                    { transform: 'translate(0, 0) scale(1)', opacity: 1 },
                    { transform: `translate(${Math.cos(angle) * velocity}px, ${Math.sin(angle) * velocity}px) scale(0)`, opacity: 0 }
                ], { duration: 1000, easing: 'cubic-bezier(0, 0.5, 0.5, 1)' });

                container.appendChild(particle);
            }

            setTimeout(() => container.remove(), 1500);
        }

        function createRainbow() {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            const rainbow = document.createElement('div');
            rainbow.style.cssText = `
                position: fixed; top: -50%; left: -25%; width: 150%; height: 150%;
                background: conic-gradient(from 0deg, #ff0000, #ff8800, #ffff00, #00ff00, #0088ff, #8800ff, #ff0088, #ff0000);
                opacity: 0; z-index: 10001; pointer-events: none; mix-blend-mode: overlay;
                animation: rainbowSpin 3s linear infinite;
            `;
            document.body.appendChild(rainbow);
            rainbow.animate([
                { opacity: 0 },
                { opacity: 0.3 },
                { opacity: 0.3 },
                { opacity: 0 }
            ], { duration: 3000 });
            setTimeout(() => rainbow.remove(), 3000);
        }

        function createMatrix(color) {
            if (localStorage.getItem('disableEmojis') === 'true') return;
            const container = document.createElement('div');
            container.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10001;overflow:hidden;';
            document.body.appendChild(container);

            const chars = '01アイウエオカキクケコサシスセソタチツテト';
            for (let i = 0; i < 30; i++) {
                const column = document.createElement('div');
                column.style.cssText = `
                    position: absolute; top: -100px; left: ${Math.random() * 100}%;
                    font-family: monospace; font-size: 20px; color: ${color};
                    text-shadow: 0 0 10px ${color}; writing-mode: vertical-rl;
                    animation: matrixFall ${2 + Math.random() * 2}s linear forwards;
                    animation-delay: ${Math.random() * 1}s;
                `;
                let text = '';
                for (let j = 0; j < 20; j++) text += chars[Math.floor(Math.random() * chars.length)];
                column.textContent = text;
                container.appendChild(column);
            }
            setTimeout(() => container.remove(), 4000);
        }

        function createFloatingEmojis(emojis) {
            const container = document.createElement('div');
            container.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10001;overflow:hidden;';
            document.body.appendChild(container);

            for (let i = 0; i < 40; i++) {
                const emoji = document.createElement('div');
                emoji.textContent = emojis[Math.floor(Math.random() * emojis.length)];
                emoji.style.cssText = `
                    position: absolute; font-size: ${20 + Math.random() * 30}px;
                    left: ${Math.random() * 100}%; bottom: -50px;
                    animation: floatUp ${3 + Math.random() * 2}s ease-out forwards;
                    animation-delay: ${Math.random() * 2}s;
                `;
                container.appendChild(emoji);
            }
            setTimeout(() => container.remove(), 6000);
        }

        function playVictorySound() {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const notes = [523.25, 659.25, 783.99, 1046.50]; // C5, E5, G5, C6

            notes.forEach((freq, i) => {
                setTimeout(() => {
                    const osc = audioCtx.createOscillator();
                    const gain = audioCtx.createGain();
                    osc.connect(gain);
                    gain.connect(audioCtx.destination);
                    osc.frequency.value = freq;
                    osc.type = 'sine';
                    gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
                    gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.5);
                    osc.start(audioCtx.currentTime);
                    osc.stop(audioCtx.currentTime + 0.5);
                }, i * 150);
            });
        }

        function checkAllTasksDone() {
            const tasks = document.querySelectorAll('.task');
            const allDone = tasks.length > 0 && [...tasks].every(t => t.classList.contains('done'));
            if (allDone) {
                setTimeout(() => triggerMegaCelebration(), 600);
            }
        }

        async function toggleTask(taskName, isDone, event) {
            // Vind de task element en voorkom dubbele clicks
            const taskEl = event ? event.target.closest('.task') : document.querySelector(`.task[data-task="${taskName}"]`);
            if (!taskEl || taskEl.classList.contains('loading')) return;

            // Optimistische UI: direct visuele feedback
            taskEl.classList.add('loading');
            const checkEl = taskEl.querySelector('.check');
            const originalCheck = checkEl.textContent;

            if (isDone) {
                // Visueel: meteen unchecked tonen
                taskEl.classList.remove('done');
                checkEl.textContent = '';
            } else {
                // Visueel: meteen checked tonen + CELEBRATION!
                taskEl.classList.add('done');
                checkEl.textContent = '✓';
                triggerCelebration(taskEl, event);
            }

            const dateStr = formatDateISO(currentDate);
            try {
                let res;
                if (isDone) {
                    // Ongedaan maken
                    res = await fetch(API + '/api/undo/task', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            member_name: currentMember,
                            task_name: taskName,
                            completed_date: dateStr
                        })
                    });
                } else {
                    // Afvinken
                    res = await fetch(API + '/api/complete', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            member_name: currentMember,
                            task_name: taskName,
                            completed_date: dateStr
                        })
                    });
                }

                taskEl.classList.remove('loading');

                if (res.ok) {
                    // Check of alle taken nu klaar zijn (voor mega celebration)
                    if (!isDone) checkAllTasksDone();
                    // Succes: herlaad voor correcte data
                    loadTasks();
                } else {
                    // Fout: rollback naar originele staat
                    if (isDone) {
                        taskEl.classList.add('done');
                        checkEl.textContent = '✓';
                    } else {
                        taskEl.classList.remove('done');
                        checkEl.textContent = '';
                    }
                    alert(isDone ? 'Kon niet ongedaan maken' : 'Kon niet afvinken');
                }
            } catch (e) {
                // Fout: rollback naar originele staat
                taskEl.classList.remove('loading');
                if (isDone) {
                    taskEl.classList.add('done');
                    checkEl.textContent = '✓';
                } else {
                    taskEl.classList.remove('done');
                    checkEl.textContent = '';
                }
                alert('Fout bij verbinding');
            }
        }

        async function showWhy(taskName) {
            const modal = document.getElementById('whyModal');
            const content = document.getElementById('modalContent');
            const title = document.getElementById('modalTitle');

            title.textContent = 'Waarom moet ik ' + taskName + '?';
            content.innerHTML = '<div style="text-align:center;color:#64748b;">Laden...</div>';
            modal.classList.add('show');

            try {
                const res = await fetch(API + '/api/explain/' + encodeURIComponent(taskName) + '?member=' + currentMember);
                const data = await res.json();
                content.innerHTML = renderExplanation(data);
            } catch (e) {
                content.innerHTML = '<div style="color:#ef4444;">Kon uitleg niet laden</div>';
            }
        }

        function renderExplanation(data) {
            let html = '';

            // Taken deze week
            html += '<section><h3>📊 Taken deze week</h3>';
            data.comparison.forEach(c => {
                const marker = c.is_assigned ? '👈' : '';
                const cls = c.is_assigned ? 'comparison-row assigned' : 'comparison-row';
                const avail = c.is_available ? '' : ' (afwezig)';
                html += `<div class="${cls}">
                    <span class="name">${c.name}</span>
                    <span class="bar">${c.tasks_this_week_bar}</span>
                    <span class="value">${c.tasks_this_week} taken${avail}</span>
                    <span class="marker">${marker}</span>
                </div>`;
            });
            html += '</section>';

            // Deze taak deze maand
            html += '<section><h3>🔄 ' + data.task + ' deze maand</h3>';
            data.comparison.forEach(c => {
                const marker = c.is_assigned ? '👈' : '';
                const cls = c.is_assigned ? 'comparison-row assigned' : 'comparison-row';
                html += `<div class="${cls}">
                    <span class="name">${c.name}</span>
                    <span class="bar">${c.specific_task_bar}</span>
                    <span class="value">${c.specific_task_this_month}x</span>
                    <span class="marker">${marker}</span>
                </div>`;
            });
            html += '</section>';

            // Laatst gedaan
            html += '<section><h3>⏰ Laatst ' + data.task + '</h3>';
            data.comparison.forEach(c => {
                const marker = c.is_assigned ? '👈' : '';
                const cls = c.is_assigned ? 'comparison-row assigned' : 'comparison-row';
                html += `<div class="${cls}">
                    <span class="name">${c.name}</span>
                    <span class="value">${c.days_since_text}</span>
                    <span class="marker">${marker}</span>
                </div>`;
            });
            html += '</section>';

            // Conclusie
            html += '<div class="conclusion">' + data.conclusion + '</div>';

            return html;
        }

        function closeModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('whyModal').classList.remove('show');
        }

        // === TAAK TOEVOEGEN ===
        async function showAddTaskModal() {
            if (!currentMember) {
                alert('Kies eerst je naam!');
                return;
            }

            const modal = document.getElementById('addTaskModal');
            const dateLabel = document.getElementById('addTaskDate');
            const select = document.getElementById('addTaskSelect');
            const result = document.getElementById('addTaskResult');

            // Reset
            result.innerHTML = '';
            dateLabel.textContent = formatDateNL(currentDate);

            // Laad beschikbare taken
            select.innerHTML = '<option value="">Laden...</option>';
            modal.classList.add('show');

            try {
                const res = await fetch(API + '/api/tasks');
                const tasks = await res.json();
                select.innerHTML = '<option value="">-- Kies een taak --</option>';
                tasks.forEach(t => {
                    select.innerHTML += '<option value="' + t.display_name + '">' + t.display_name + '</option>';
                });
            } catch (e) {
                select.innerHTML = '<option value="">Fout bij laden</option>';
            }
        }

        function closeAddTaskModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('addTaskModal').classList.remove('show');
        }

        async function submitAddTask() {
            const select = document.getElementById('addTaskSelect');
            const result = document.getElementById('addTaskResult');
            const taskName = select.value;

            if (!taskName) {
                result.innerHTML = '<div class="error-msg">Kies een taak</div>';
                return;
            }

            const dateStr = formatDateISO(currentDate);

            try {
                // Gebruik /api/tasks/extra om taak toe te voegen ZONDER af te vinken
                const res = await fetch(API + '/api/tasks/extra', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        member_name: currentMember,
                        task_name: taskName,
                        task_date: dateStr
                    })
                });
                const data = await res.json();

                if (res.ok) {
                    result.innerHTML = '<div class="success-msg">✅ ' + data.message + '</div>';
                    setTimeout(() => {
                        closeAddTaskModal();
                        loadTasks();
                    }, 1000);
                } else {
                    result.innerHTML = '<div class="error-msg">' + (data.detail || 'Fout') + '</div>';
                }
            } catch (e) {
                result.innerHTML = '<div class="error-msg">Kon niet opslaan</div>';
            }
        }

        async function removeExtraTask(extraId, event) {
            if (event) {
                event.stopPropagation();
            }

            if (!confirm('Weet je zeker dat je deze extra taak wilt verwijderen?')) return;

            // Optimistische UI: direct visueel verwijderen
            const taskEl = event ? event.target.closest('.task') : null;
            if (taskEl) {
                taskEl.style.opacity = '0.3';
                taskEl.style.pointerEvents = 'none';
            }

            try {
                const res = await fetch(API + '/api/tasks/extra/' + extraId, { method: 'DELETE' });
                if (res.ok) {
                    loadTasks();
                } else {
                    // Rollback bij fout
                    if (taskEl) {
                        taskEl.style.opacity = '';
                        taskEl.style.pointerEvents = '';
                    }
                    alert('Kon niet verwijderen');
                }
            } catch (e) {
                // Rollback bij fout
                if (taskEl) {
                    taskEl.style.opacity = '';
                    taskEl.style.pointerEvents = '';
                }
                alert('Fout bij verwijderen');
            }
        }

        // === VIEW NAVIGATION ===
        function showView(viewId, btn) {
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            document.getElementById(viewId).classList.add('active');
            if (btn) btn.classList.add('active');

            // Picker alleen tonen bij Vandaag view
            document.getElementById('picker').style.display = (viewId === 'viewToday') ? 'flex' : 'none';

            // Load data voor de view
            if (viewId === 'viewWeek') loadWeekSchedule();
            if (viewId === 'viewStand') loadStand();
            if (viewId === 'viewAbsence') loadUpcomingAbsences();
            if (viewId === 'viewSettings') { loadRules(); loadTaskOptions(); initPushNotifications(); }
        }

        // === WEEKROOSTER ===
        async function loadWeekSchedule() {
            const container = document.getElementById('weekSchedule');
            container.innerHTML = '<div class="loading"><div class="spinner"></div>Laden...</div>';

            try {
                const res = await fetch(API + '/api/schedule');
                const data = await res.json();
                renderWeekSchedule(data);
            } catch (e) {
                container.innerHTML = '<div class="empty">Kon rooster niet laden</div>';
            }
        }

        function renderWeekSchedule(data) {
            const days = ['maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag'];
            const today = new Date().toLocaleDateString('nl-NL', {weekday: 'long'}).toLowerCase();
            let html = '';

            days.forEach(day => {
                const dayData = data.schedule[day];
                if (!dayData) return;

                const isToday = day === today;
                const tasks = dayData.tasks || [];

                html += '<div class="day-section">';
                html += '<div class="day-header ' + (isToday ? 'today' : '') + '">';
                html += (isToday ? '👉 ' : '') + day.charAt(0).toUpperCase() + day.slice(1);
                html += ' <small style="color:#64748b;font-weight:normal;">' + dayData.date + '</small>';
                html += '</div>';

                if (tasks.length === 0) {
                    html += '<div style="padding:8px 0;color:#64748b;font-size:14px;">Geen taken</div>';
                } else {
                    tasks.forEach(t => {
                        const completed = t.completed;
                        const person = t.completed_by || t.assigned_to;
                        html += '<div class="day-task ' + (completed ? 'completed' : '') + '">';
                        html += '<span class="member">' + person + '</span>';
                        html += '<span class="task-name">' + t.task_name + '</span>';
                        html += '<span class="status">' + (completed ? '✅' : '⬜') + '</span>';
                        html += '</div>';
                    });
                }
                html += '</div>';
            });

            document.getElementById('weekSchedule').innerHTML = html;
        }

        // === STAND ===
        var statsData = null;
        var statsTab = 'week';

        function setStatsTab(tab) {
            statsTab = tab;
            renderStand();
        }

        function renderRadarChart(data) {
            const size = 280;
            const center = size / 2;
            const maxRadius = 100;
            const levels = 4;
            const memberColors = {Nora: '#8b5cf6', Linde: '#f97316', Fenna: '#22c55e'};

            // Get task categories (simplified)
            const categories = ['uitruimen', 'inruimen', 'dekken', 'koken', 'karton', 'glas'];
            const categoryLabels = ['Uitruimen', 'Inruimen', 'Dekken', 'Koken', 'Karton', 'Glas'];
            const numCategories = categories.length;
            const angleSlice = (2 * Math.PI) / numCategories;

            let svg = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '">';

            // Background circles
            for (let i = 1; i <= levels; i++) {
                const r = (maxRadius / levels) * i;
                svg += '<circle cx="' + center + '" cy="' + center + '" r="' + r + '" fill="none" stroke="#e2e8f0" stroke-width="1"/>';
            }

            // Axis lines and labels
            for (let i = 0; i < numCategories; i++) {
                const angle = angleSlice * i - Math.PI / 2;
                const x = center + maxRadius * Math.cos(angle);
                const y = center + maxRadius * Math.sin(angle);
                svg += '<line x1="' + center + '" y1="' + center + '" x2="' + x + '" y2="' + y + '" stroke="#cbd5e1" stroke-width="1"/>';

                // Labels
                const labelRadius = maxRadius + 20;
                const lx = center + labelRadius * Math.cos(angle);
                const ly = center + labelRadius * Math.sin(angle);
                svg += '<text x="' + lx + '" y="' + ly + '" text-anchor="middle" dominant-baseline="middle" font-size="11" fill="#64748b">' + categoryLabels[i] + '</text>';
            }

            // Calculate max values for scaling
            const taskTotals = {};
            categories.forEach(cat => {
                taskTotals[cat] = 0;
                Object.keys(data.members).forEach(name => {
                    Object.keys(data.members[name].tasks || {}).forEach(taskName => {
                        if (taskName.toLowerCase().includes(cat)) {
                            taskTotals[cat] += data.members[name].tasks[taskName];
                        }
                    });
                });
            });
            // Also check all-time data
            if (data.task_breakdown) {
                Object.entries(data.task_breakdown).forEach(([taskName, taskData]) => {
                    categories.forEach(cat => {
                        if (taskName.toLowerCase().includes(cat)) {
                            Object.values(taskData.month).forEach(v => taskTotals[cat] = Math.max(taskTotals[cat], v * 3));
                        }
                    });
                });
            }
            const maxValue = Math.max(10, ...Object.values(taskTotals));

            // Draw member polygons
            Object.entries(data.members).forEach(([name, info]) => {
                const color = memberColors[name] || '#4f46e5';
                const points = [];

                categories.forEach((cat, i) => {
                    let value = 0;
                    // Sum tasks matching this category
                    if (data.task_breakdown) {
                        Object.entries(data.task_breakdown).forEach(([taskName, taskData]) => {
                            if (taskName.toLowerCase().includes(cat)) {
                                value += taskData.month[name] || 0;
                            }
                        });
                    }
                    const radius = (value / maxValue) * maxRadius;
                    const angle = angleSlice * i - Math.PI / 2;
                    const x = center + radius * Math.cos(angle);
                    const y = center + radius * Math.sin(angle);
                    points.push(x + ',' + y);
                });

                svg += '<polygon points="' + points.join(' ') + '" fill="' + color + '" fill-opacity="0.2" stroke="' + color + '" stroke-width="2.5"/>';

                // Dots on vertices
                categories.forEach((cat, i) => {
                    let value = 0;
                    if (data.task_breakdown) {
                        Object.entries(data.task_breakdown).forEach(([taskName, taskData]) => {
                            if (taskName.toLowerCase().includes(cat)) {
                                value += taskData.month[name] || 0;
                            }
                        });
                    }
                    const radius = (value / maxValue) * maxRadius;
                    const angle = angleSlice * i - Math.PI / 2;
                    const x = center + radius * Math.cos(angle);
                    const y = center + radius * Math.sin(angle);
                    svg += '<circle cx="' + x + '" cy="' + y + '" r="4" fill="' + color + '"/>';
                });
            });

            svg += '</svg>';
            return svg;
        }

        async function loadStand() {
            const container = document.getElementById('standContent');
            container.innerHTML = '<div class="loading"><div class="spinner"></div>Laden...</div>';

            try {
                const res = await fetch(API + '/api/stats');
                statsData = await res.json();
                renderStand();
            } catch (e) {
                container.innerHTML = '<div class="empty">Kon statistieken niet laden</div>';
            }
        }

        function renderStand() {
            if (!statsData) return;
            const data = statsData;
            const members = Object.entries(data.members);

            let html = '';

            // Achievements banner
            if (data.achievements && data.achievements.length > 0) {
                html += '<div class="stats-section" style="background:linear-gradient(135deg,#fef3c7,#fde68a);">';
                html += '<h3>🏅 Achievements</h3>';
                html += '<div style="display:flex;flex-wrap:wrap;">';
                data.achievements.forEach(a => {
                    html += '<div class="achievement-badge"><span class="emoji">' + a.badge + '</span>' + a.member + ': ' + a.text + '</div>';
                });
                html += '</div></div>';
            }

            // Animated Progress Rings
            html += '<div class="stats-section">';
            html += '<h3>🎯 Voortgang deze maand</h3>';
            html += '<div class="progress-rings">';
            const memberColors = {Nora: '#8b5cf6', Linde: '#f97316', Fenna: '#22c55e'};
            const monthTarget = 30; // Roughly 8 per week * 4 weeks
            Object.entries(data.members).forEach(([name, info]) => {
                const pct = Math.min(Math.round((info.this_month / monthTarget) * 100), 100);
                const color = memberColors[name] || '#4f46e5';
                const circumference = 2 * Math.PI * 36;
                const offset = circumference - (pct / 100) * circumference;
                html += '<div class="ring-container">';
                html += '<svg width="90" height="90" viewBox="0 0 90 90">';
                html += '<circle cx="45" cy="45" r="36" fill="none" stroke="#e2e8f0" stroke-width="8"/>';
                html += '<circle cx="45" cy="45" r="36" fill="none" stroke="' + color + '" stroke-width="8" ';
                html += 'stroke-linecap="round" stroke-dasharray="' + circumference + '" ';
                html += 'stroke-dashoffset="' + offset + '" transform="rotate(-90 45 45)" ';
                html += 'style="transition: stroke-dashoffset 1s ease-out;"/>';
                html += '<text x="45" y="45" text-anchor="middle" dy="6" font-size="18" font-weight="bold" fill="' + color + '">' + info.this_month + '</text>';
                html += '</svg>';
                html += '<div class="ring-label">' + name + '</div>';
                html += '<div class="ring-value">' + pct + '% van doel</div>';
                html += '</div>';
            });
            html += '</div></div>';

            // Radar Chart - Task Profile per Member
            html += '<div class="stats-section">';
            html += '<h3>🕸️ Takenprofiel</h3>';
            html += '<div class="radar-container">';
            html += renderRadarChart(data);
            html += '</div>';
            html += '<div class="radar-legend">';
            Object.entries(memberColors).forEach(([name, color]) => {
                html += '<div class="radar-legend-item"><div class="radar-legend-dot" style="background:' + color + '"></div>' + name + '</div>';
            });
            html += '</div></div>';

            // Leaderboard with tabs
            html += '<div class="stats-section">';
            html += '<h3>🏆 Leaderboard</h3>';
            html += '<div class="tabs">';
            html += '<button class="tab-btn ' + (statsTab === 'week' ? 'active' : '') + '" onclick="setStatsTab(\\\'week\\\')">Deze week</button>';
            html += '<button class="tab-btn ' + (statsTab === 'month' ? 'active' : '') + '" onclick="setStatsTab(\\\'month\\\')">Deze maand</button>';
            html += '<button class="tab-btn ' + (statsTab === 'alltime' ? 'active' : '') + '" onclick="setStatsTab(\\\'alltime\\\')">All-time</button>';
            html += '</div>';

            const leaderboard = statsTab === 'week' ? data.leaderboard.week :
                               statsTab === 'month' ? data.leaderboard.month : data.leaderboard.all_time;
            const ranks = ['🥇', '🥈', '🥉'];
            const classes = ['gold', 'silver', 'bronze'];

            leaderboard.forEach(([name, score], idx) => {
                const memberData = data.members[name];
                let trend = '';
                if (statsTab === 'week' && memberData.last_week > 0) {
                    const diff = memberData.this_week - memberData.last_week;
                    if (diff > 0) trend = '<span class="leaderboard-trend up">▲' + diff + '</span>';
                    else if (diff < 0) trend = '<span class="leaderboard-trend down">▼' + Math.abs(diff) + '</span>';
                }
                html += '<div class="leaderboard-item ' + (classes[idx] || '') + '">';
                html += '<div class="leaderboard-rank">' + (ranks[idx] || (idx + 1)) + '</div>';
                html += '<div class="leaderboard-name">' + name + '</div>';
                html += '<div class="leaderboard-score">' + score + trend + '</div>';
                html += '</div>';
            });
            html += '</div>';

            // Personal stats per member
            members.forEach(([name, info]) => {
                html += '<div class="stats-section">';
                html += '<h3>' + name + '</h3>';

                // Stat grid
                html += '<div class="stat-grid">';
                html += '<div class="stat-card"><div class="value">' + info.this_week + '</div><div class="label">Deze week</div></div>';
                html += '<div class="stat-card"><div class="value">' + info.this_month + '</div><div class="label">Deze maand</div></div>';
                html += '<div class="stat-card streak"><div class="value">' + info.streak + '🔥</div><div class="label">Huidige streak</div></div>';
                html += '<div class="stat-card alltime"><div class="value">' + info.all_time + '</div><div class="label">All-time</div></div>';
                html += '</div>';

                // Favorite task
                if (info.favorite_task) {
                    html += '<div style="margin-top:12px;padding:10px;background:#f0fdf4;border-radius:8px;">';
                    html += '<span style="font-size:13px;">⭐ Specialist in: <strong>' + info.favorite_task + '</strong> (' + info.favorite_count + 'x)</span>';
                    html += '</div>';
                }

                // Time of day distribution
                const total = info.by_time_of_day.ochtend + info.by_time_of_day.middag + info.by_time_of_day.avond;
                if (total > 0) {
                    html += '<div style="margin-top:12px;">';
                    html += '<div style="font-size:12px;color:#64748b;margin-bottom:4px;">Wanneer actief (deze maand)</div>';
                    html += '<div class="time-bar">';
                    const pctO = Math.round((info.by_time_of_day.ochtend / total) * 100);
                    const pctM = Math.round((info.by_time_of_day.middag / total) * 100);
                    const pctA = Math.round((info.by_time_of_day.avond / total) * 100);
                    if (pctO > 0) html += '<div class="segment ochtend" style="width:' + pctO + '%">☀️' + pctO + '%</div>';
                    if (pctM > 0) html += '<div class="segment middag" style="width:' + pctM + '%">🌤️' + pctM + '%</div>';
                    if (pctA > 0) html += '<div class="segment avond" style="width:' + pctA + '%">🌙' + pctA + '%</div>';
                    html += '</div>';
                    html += '</div>';
                }

                // Task breakdown this week
                const taskEntries = Object.entries(info.tasks);
                if (taskEntries.length > 0) {
                    html += '<div style="margin-top:12px;">';
                    html += '<div style="font-size:12px;color:#64748b;margin-bottom:6px;">Taken deze week</div>';
                    html += '<div class="task-breakdown">';
                    taskEntries.forEach(([task, count]) => {
                        html += '<span class="task-chip">' + task + ' ×' + count + '</span>';
                    });
                    html += '</div></div>';
                }

                html += '</div>';
            });

            // Gedetailleerde taak breakdown tabel
            if (data.task_breakdown) {
                html += '<div class="stats-section">';
                html += '<h3>📋 Taken per persoon</h3>';
                html += '<div class="tabs">';
                html += '<button class="tab-btn ' + (statsTab === 'week' ? 'active' : '') + '" onclick="setStatsTab(\\\'week\\\')">Week</button>';
                html += '<button class="tab-btn ' + (statsTab === 'month' ? 'active' : '') + '" onclick="setStatsTab(\\\'month\\\')">Maand</button>';
                html += '<button class="tab-btn ' + (statsTab === 'alltime' ? 'active' : '') + '" onclick="setStatsTab(\\\'alltime\\\')">All-time</button>';
                html += '</div>';
                html += '<div class="task-table">';
                html += '<div class="task-table-header"><div class="task-col">Taak</div>';
                const memberNames = Object.keys(data.members);
                memberNames.forEach(name => {
                    html += '<div class="member-col">' + name + '</div>';
                });
                html += '</div>';

                const period = statsTab === 'week' ? 'week' : statsTab === 'month' ? 'month' : 'all_time';
                Object.entries(data.task_breakdown).forEach(([taskName, taskData]) => {
                    const counts = taskData[period];
                    const total = memberNames.reduce((sum, name) => sum + (counts[name] || 0), 0);
                    if (total === 0 && statsTab !== 'alltime') return; // Skip taken zonder data (behalve all-time)

                    html += '<div class="task-table-row">';
                    html += '<div class="task-col">' + taskName + '</div>';
                    memberNames.forEach(name => {
                        const count = counts[name] || 0;
                        const maxCount = Math.max(...memberNames.map(n => counts[n] || 0));
                        const isMax = count > 0 && count === maxCount;
                        html += '<div class="member-col ' + (isMax ? 'highlight' : '') + '">' + count + '</div>';
                    });
                    html += '</div>';
                });
                html += '</div></div>';
            }

            document.getElementById('standContent').innerHTML = html;
        }

        // === AFWEZIGHEID ===
        async function submitAbsence() {
            const member = document.getElementById('absenceMember').value;
            const start = document.getElementById('absenceStart').value;
            const end = document.getElementById('absenceEnd').value;
            const reason = document.getElementById('absenceReason').value;
            const result = document.getElementById('absenceResult');

            if (!start || !end) {
                result.innerHTML = '<div class="error-msg">Vul beide datums in</div>';
                return;
            }

            result.innerHTML = '<div class="loading"><div class="spinner"></div>Opslaan...</div>';

            try {
                const res = await fetch(API + '/api/absence', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        member_name: member,
                        start_date: start,
                        end_date: end,
                        reason: reason || null
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    result.innerHTML = '<div class="success-msg">✅ ' + data.message + '</div>';
                    // Reset form
                    document.getElementById('absenceStart').value = '';
                    document.getElementById('absenceEnd').value = '';
                    document.getElementById('absenceReason').value = '';
                    // Herlaad de lijst
                    loadUpcomingAbsences();
                } else {
                    result.innerHTML = '<div class="error-msg">' + (data.detail || 'Fout') + '</div>';
                }
            } catch (e) {
                result.innerHTML = '<div class="error-msg">Kon niet opslaan</div>';
            }
        }

        // === AANKOMENDE AFWEZIGHEDEN ===
        async function deleteAbsence(id) {
            if (!confirm('Weet je zeker dat je deze afwezigheid wilt verwijderen?')) return;

            const container = document.getElementById('upcomingAbsences');
            container.innerHTML = '<div class="loading"><div class="spinner"></div>Verwijderen...</div>';

            try {
                const res = await fetch(API + '/api/absence/' + id, { method: 'DELETE' });
                const data = await res.json();
                if (res.ok) {
                    loadUpcomingAbsences();
                } else {
                    alert(data.detail || 'Kon niet verwijderen');
                    loadUpcomingAbsences();
                }
            } catch (e) {
                alert('Fout bij verwijderen');
                loadUpcomingAbsences();
            }
        }

        async function loadUpcomingAbsences() {
            const container = document.getElementById('upcomingAbsences');
            container.innerHTML = '<div class="loading"><div class="spinner"></div>Laden...</div>';

            try {
                const res = await fetch(API + '/api/absences/upcoming');
                const absences = await res.json();

                if (absences.length === 0) {
                    container.innerHTML = '<div class="empty">Geen geplande afwezigheden</div>';
                    return;
                }

                let html = '';
                absences.forEach(a => {
                    const start = new Date(a.start).toLocaleDateString('nl-NL', {weekday: 'short', day: 'numeric', month: 'short'});
                    const end = new Date(a.end).toLocaleDateString('nl-NL', {weekday: 'short', day: 'numeric', month: 'short'});
                    const dateStr = a.start === a.end ? start : start + ' - ' + end;
                    html += '<div class="absence-item">' +
                        '<span class="emoji">🏖️</span>' +
                        '<div class="details">' +
                        '<div class="name">' + a.member + '</div>' +
                        '<div class="dates">' + dateStr + '</div>' +
                        (a.reason ? '<div class="reason">' + a.reason + '</div>' : '') +
                        '</div>' +
                        '<button class="delete-btn" onclick="deleteAbsence(' + "'" + a.id + "'" + ')" title="Verwijderen">✕</button>' +
                        '</div>';
                });

                container.innerHTML = html;
            } catch (e) {
                container.innerHTML = '<div class="empty">Kon niet laden</div>';
            }
        }

        // Set default dates voor afwezigheid
        const today = new Date().toISOString().split('T')[0];
        document.getElementById('absenceStart').value = today;
        document.getElementById('absenceEnd').value = today;
        if (currentMember) {
            document.getElementById('absenceMember').value = currentMember;
        }

        // === REGELS / SETTINGS ===
        const dayNames = ['Maandag', 'Dinsdag', 'Woensdag', 'Donderdag', 'Vrijdag', 'Zaterdag', 'Zondag'];

        async function loadTaskOptions() {
            try {
                const res = await fetch(API + '/api/tasks');
                const tasks = await res.json();
                const select = document.getElementById('ruleTask');
                // Clear except first option
                select.innerHTML = '<option value="">Alle taken</option>';
                tasks.forEach(t => {
                    select.innerHTML += '<option value="' + t.display_name + '">' + t.display_name + '</option>';
                });

                // Vul ook de swap task dropdowns
                const swap1 = document.getElementById('swapTask1');
                const swap2 = document.getElementById('swapTask2');
                if (swap1 && swap2) {
                    swap1.innerHTML = '<option value="">Kies taak...</option>';
                    swap2.innerHTML = '<option value="">Kies taak...</option>';
                    tasks.forEach(t => {
                        swap1.innerHTML += '<option value="' + t.display_name + '">' + t.display_name + '</option>';
                        swap2.innerHTML += '<option value="' + t.display_name + '">' + t.display_name + '</option>';
                    });
                }
            } catch (e) {
                console.error('Kon taken niet laden', e);
            }
        }

        // === RUILEN === (tijdelijk uitgeschakeld)
        // Zet standaard datum op vandaag
        if (document.getElementById('swapDate')) {
            document.getElementById('swapDate').value = new Date().toISOString().split('T')[0];
        }

        // === EMOJI VOORKEUR ===
        // Laad voorkeur uit localStorage
        if (localStorage.getItem('disableEmojis') === 'true') {
            document.getElementById('disableEmojis').checked = true;
        }

        function toggleEmojis() {
            const disabled = document.getElementById('disableEmojis').checked;
            localStorage.setItem('disableEmojis', disabled ? 'true' : 'false');

            // Verwijder bestaande zwevende emojis direct
            if (disabled) {
                document.getElementById('catsContainer').innerHTML = '';
                document.getElementById('ottersContainer').innerHTML = '';
                document.getElementById('bearsContainer').innerHTML = '';
            } else {
                // Herlaad de pagina om ze terug te krijgen
                location.reload();
            }
        }

        async function submitSwap() {
            const swapDate = document.getElementById('swapDate').value;
            const member1 = document.getElementById('swapMember1').value;
            const task1 = document.getElementById('swapTask1').value;
            const member2 = document.getElementById('swapMember2').value;
            const task2 = document.getElementById('swapTask2').value;
            const result = document.getElementById('swapResult');

            if (!swapDate || !task1 || !task2) {
                result.innerHTML = '<div class="error-msg">Vul alle velden in</div>';
                return;
            }

            if (member1 === member2) {
                result.innerHTML = '<div class="error-msg">Kies twee verschillende kinderen</div>';
                return;
            }

            result.innerHTML = '<div class="loading"><div class="spinner"></div>Ruilen...</div>';

            try {
                const res = await fetch(API + '/api/swap/same-day', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        member1_name: member1,
                        member1_task: task1,
                        member2_name: member2,
                        member2_task: task2,
                        swap_date: swapDate
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    result.innerHTML = '<div class="success-msg">✅ ' + data.message + '</div>';
                    // Reset form
                    document.getElementById('swapTask1').value = '';
                    document.getElementById('swapTask2').value = '';
                } else {
                    result.innerHTML = '<div class="error-msg">' + (data.detail || 'Fout bij ruilen') + '</div>';
                }
            } catch (e) {
                result.innerHTML = '<div class="error-msg">Kon niet ruilen</div>';
            }
        }

        async function loadRules() {
            const container = document.getElementById('rulesList');
            container.innerHTML = '<div class="loading">Laden...</div>';

            try {
                const res = await fetch(API + '/api/rules');
                const data = await res.json();
                const rules = data.rules || [];

                if (rules.length === 0) {
                    container.innerHTML = '<div class="empty">Geen regels ingesteld</div>';
                    return;
                }

                let html = '';
                rules.forEach(r => {
                    const task = r.task_name || 'alle taken';
                    const day = r.day_of_week !== null ? dayNames[r.day_of_week] : 'elke dag';
                    html += '<div class="absence-item">' +
                        '<span class="emoji">🚫</span>' +
                        '<div class="details">' +
                        '<div class="name">' + r.member_name + ' kan niet: ' + task + '</div>' +
                        '<div class="dates">Op: ' + day + '</div>' +
                        (r.description ? '<div class="reason">' + r.description + '</div>' : '') +
                        '</div>' +
                        '<button class="delete-btn" onclick="deleteRule(' + "'" + r.id + "'" + ')" title="Verwijderen">✕</button>' +
                        '</div>';
                });

                container.innerHTML = html;
            } catch (e) {
                container.innerHTML = '<div class="empty">Kon niet laden</div>';
            }
        }

        async function addRule() {
            const member = document.getElementById('ruleMember').value;
            const task = document.getElementById('ruleTask').value || null;
            const dayStr = document.getElementById('ruleDay').value;
            const day = dayStr !== '' ? parseInt(dayStr) : null;
            const description = document.getElementById('ruleDescription').value;
            const result = document.getElementById('ruleResult');
            const ruleType = day === null && task !== null ? 'never' : 'unavailable';

            try {
                const res = await fetch(API + '/api/rules', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        member_name: member,
                        task_name: task,
                        day_of_week: day,
                        rule_type: ruleType,
                        description: description || null
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    result.innerHTML = '<div class="success-msg">✅ Regel toegevoegd</div>';
                    document.getElementById('ruleDescription').value = '';
                    loadRules();
                } else {
                    result.innerHTML = '<div class="error-msg">' + (data.detail || 'Fout') + '</div>';
                }
            } catch (e) {
                result.innerHTML = '<div class="error-msg">Kon niet opslaan</div>';
            }
        }

        async function deleteRule(id) {
            if (!confirm('Weet je zeker dat je deze regel wilt verwijderen?')) return;

            try {
                const res = await fetch(API + '/api/rules/' + id, { method: 'DELETE' });
                if (res.ok) {
                    loadRules();
                } else {
                    alert('Kon niet verwijderen');
                }
            } catch (e) {
                alert('Fout bij verwijderen');
            }
        }

        async function regenerateSchedule() {
            if (!confirm('Weet je zeker dat je het rooster opnieuw wilt plannen? Dit vervangt het huidige rooster.')) return;

            const result = document.getElementById('regenerateResult');
            result.innerHTML = '<div class="loading">Rooster wordt opnieuw gepland...</div>';

            try {
                const res = await fetch(API + '/api/schedule/regenerate', { method: 'POST' });
                const data = await res.json();
                if (res.ok) {
                    result.innerHTML = '<div class="success-msg">✅ ' + data.message + '</div>';
                    // Herlaad taken zodat Vandaag view up-to-date is
                    loadTasks();
                } else {
                    result.innerHTML = '<div class="error-msg">' + (data.detail || 'Fout') + '</div>';
                }
            } catch (e) {
                result.innerHTML = '<div class="error-msg">Kon rooster niet herplannen</div>';
            }
        }

        function getCalendarUrl(memberName) {
            // Gebruik window.location.origin voor volledige URL
            return window.location.origin + '/api/calendar/' + memberName + '.ics';
        }

        function subscribeCalendar(memberName) {
            const calendarUrl = getCalendarUrl(memberName);
            const webcalUrl = calendarUrl.replace('https://', 'webcal://').replace('http://', 'webcal://');

            // Probeer webcal protocol (native kalender-app)
            window.location.href = webcalUrl;
        }

        async function copyCalendarUrl(memberName) {
            const calendarUrl = getCalendarUrl(memberName);
            const resultEl = document.getElementById('copyResult');

            try {
                await navigator.clipboard.writeText(calendarUrl);
                resultEl.innerHTML = '<span style="color:#22c55e;">✅ URL gekopieerd!</span>';
                setTimeout(() => { resultEl.innerHTML = ''; }, 3000);
            } catch (e) {
                // Fallback voor browsers zonder clipboard API
                prompt('Kopieer deze URL:', calendarUrl);
            }
        }

        // === Push Notification Functions ===
        let swRegistration = null;
        let vapidPublicKey = null;

        async function initPushNotifications() {
            const notSupportedEl = document.getElementById('pushNotSupported');
            const statusEl = document.getElementById('pushStatus');
            const enableBtn = document.getElementById('enablePushBtn');
            const disableBtn = document.getElementById('disablePushBtn');
            const testBtn = document.getElementById('testPushBtn');

            // Check of push ondersteund wordt
            if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
                notSupportedEl.style.display = 'block';
                enableBtn.style.display = 'none';
                return;
            }

            // Check of we in standalone mode zijn (PWA)
            const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
                                 window.navigator.standalone === true;

            if (!isStandalone) {
                statusEl.innerHTML = '<span style="color:#f59e0b;">📱 Installeer eerst de app op je homescreen voor notificaties</span>';
                return;
            }

            // Wacht op service worker registration
            try {
                swRegistration = await navigator.serviceWorker.ready;

                // Haal VAPID public key op
                const keyRes = await fetch('/api/vapid-public-key');
                if (!keyRes.ok) {
                    statusEl.innerHTML = '<span style="color:#ef4444;">Push niet geconfigureerd op server</span>';
                    enableBtn.style.display = 'none';
                    return;
                }
                const keyData = await keyRes.json();
                vapidPublicKey = keyData.publicKey;

                // Check huidige subscription status
                const subscription = await swRegistration.pushManager.getSubscription();
                updatePushUI(subscription !== null);
            } catch (e) {
                console.error('Push init error:', e);
                statusEl.innerHTML = '<span style="color:#ef4444;">Fout bij initialisatie</span>';
            }
        }

        function updatePushUI(isSubscribed) {
            const statusEl = document.getElementById('pushStatus');
            const enableBtn = document.getElementById('enablePushBtn');
            const disableBtn = document.getElementById('disablePushBtn');
            const testBtn = document.getElementById('testPushBtn');

            if (isSubscribed) {
                statusEl.innerHTML = '<span style="color:#22c55e;">✅ Notificaties zijn ingeschakeld voor ' + currentMember + '</span>';
                enableBtn.style.display = 'none';
                disableBtn.style.display = 'block';
                testBtn.style.display = 'block';
            } else {
                statusEl.innerHTML = '<span style="color:#64748b;">Notificaties zijn uitgeschakeld</span>';
                enableBtn.style.display = 'block';
                disableBtn.style.display = 'none';
                testBtn.style.display = 'none';
            }
        }

        async function enablePushNotifications() {
            const resultEl = document.getElementById('pushResult');

            if (!currentMember) {
                resultEl.innerHTML = '<span style="color:#ef4444;">Selecteer eerst wie je bent (Vandaag tab)</span>';
                return;
            }

            try {
                resultEl.innerHTML = '<span style="color:#64748b;">Toestemming vragen...</span>';

                // Vraag notificatie toestemming
                const permission = await Notification.requestPermission();
                if (permission !== 'granted') {
                    resultEl.innerHTML = '<span style="color:#ef4444;">Toestemming geweigerd. Check je instellingen.</span>';
                    return;
                }

                resultEl.innerHTML = '<span style="color:#64748b;">Registreren...</span>';

                // Subscribe bij push service
                const subscription = await swRegistration.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)
                });

                // Stuur subscription naar server
                const subJson = subscription.toJSON();
                const res = await fetch('/api/push/subscribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        member_name: currentMember,
                        endpoint: subJson.endpoint,
                        p256dh: subJson.keys.p256dh,
                        auth: subJson.keys.auth
                    })
                });

                if (res.ok) {
                    resultEl.innerHTML = '<span style="color:#22c55e;">✅ Notificaties ingeschakeld!</span>';
                    updatePushUI(true);
                } else {
                    throw new Error('Server error');
                }
            } catch (e) {
                console.error('Push subscribe error:', e);
                resultEl.innerHTML = '<span style="color:#ef4444;">Fout: ' + e.message + '</span>';
            }

            setTimeout(() => { resultEl.innerHTML = ''; }, 5000);
        }

        async function disablePushNotifications() {
            const resultEl = document.getElementById('pushResult');

            try {
                const subscription = await swRegistration.pushManager.getSubscription();
                if (subscription) {
                    // Unsubscribe lokaal
                    await subscription.unsubscribe();

                    // Verwijder van server
                    await fetch('/api/push/unsubscribe', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ endpoint: subscription.endpoint })
                    });
                }

                resultEl.innerHTML = '<span style="color:#22c55e;">Notificaties uitgeschakeld</span>';
                updatePushUI(false);
            } catch (e) {
                console.error('Push unsubscribe error:', e);
                resultEl.innerHTML = '<span style="color:#ef4444;">Fout bij uitschakelen</span>';
            }

            setTimeout(() => { resultEl.innerHTML = ''; }, 3000);
        }

        async function testPushNotification() {
            const resultEl = document.getElementById('pushResult');

            if (!currentMember) {
                resultEl.innerHTML = '<span style="color:#ef4444;">Selecteer eerst wie je bent</span>';
                return;
            }

            try {
                resultEl.innerHTML = '<span style="color:#64748b;">Test versturen... (ochtend + avond)</span>';

                const res = await fetch('/api/push/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ member_name: currentMember })
                });

                const data = await res.json();

                // Check resultaten van morning en evening
                const morningSent = data.morning && data.morning.success > 0;
                const eveningSent = data.evening && data.evening.success > 0;
                const morningSkipped = data.morning && data.morning.skipped;
                const eveningSkipped = data.evening && data.evening.skipped;

                let msg = '';
                if (morningSent || eveningSent) {
                    msg = '<span style="color:#22c55e;">✅ Notificaties verstuurd! Check je telefoon.</span>';
                } else if (morningSkipped && eveningSkipped) {
                    // Geen taken vandaag
                    msg = '<span style="color:#f59e0b;">📭 ' + (data.morning.reason || 'Geen taken vandaag') + '</span>';
                } else {
                    msg = '<span style="color:#ef4444;">Kon niet versturen. Zijn notificaties ingeschakeld?</span>';
                }
                resultEl.innerHTML = msg;
            } catch (e) {
                resultEl.innerHTML = '<span style="color:#ef4444;">Fout: ' + e.message + '</span>';
            }

            setTimeout(() => { resultEl.innerHTML = ''; }, 5000);
        }

        // Helper: Base64 URL naar Uint8Array
        function urlBase64ToUint8Array(base64String) {
            const padding = '='.repeat((4 - base64String.length % 4) % 4);
            const base64 = (base64String + padding)
                .replace(/\\-/g, '+')
                .replace(/_/g, '/');

            const rawData = window.atob(base64);
            const outputArray = new Uint8Array(rawData.length);

            for (let i = 0; i < rawData.length; ++i) {
                outputArray[i] = rawData.charCodeAt(i);
            }
            return outputArray;
        }
    </script>
    <script>
        // Register Service Worker
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js')
                    .then(reg => {
                        console.log('SW registered:', reg.scope);
                        // Init push na SW registration
                        initPushNotifications();
                    })
                    .catch(err => console.log('SW registration failed:', err));
            });
        }

        // Check for What's New modal on load
        window.addEventListener('load', () => {
            checkWhatsNew();
        });
    </script>
</body>
</html>"""


@app.get("/api/my-tasks/{member_name}")
async def get_my_tasks_for_date(member_name: str, date: Optional[str] = None):
    """Haal taken op voor een specifiek gezinslid op een bepaalde datum.

    Args:
        member_name: Naam van het gezinslid
        date: Optionele datum (YYYY-MM-DD). Standaard vandaag.

    Returns:
        Taken voor die dag (zowel geplande als extra toegevoegde)
    """
    from .database import get_today_tasks_for_member, today_local
    from datetime import datetime

    # Bepaal de datum
    if date:
        try:
            target_date = datetime.fromisoformat(date).date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Ongeldige datum formaat. Gebruik YYYY-MM-DD")
    else:
        target_date = today_local()

    week_number = target_date.isocalendar()[1]
    year = target_date.isocalendar()[0]
    day_of_week = target_date.weekday()
    day_names = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
    day_name = day_names[day_of_week]

    # Database call voor deze dag
    data = get_today_tasks_for_member(member_name, week_number, year, day_of_week, target_date)

    my_tasks = []
    seen_tasks = set()

    # 1. Geplande taken voor deze dag (uit schedule)
    for a in data["assignments"]:
        completed_by = data["completions"].get(a["task_name"])
        is_completed = completed_by is not None
        is_mine = (a["member_name"] == member_name) or (completed_by == member_name)

        if is_mine:
            my_tasks.append({
                "task_name": a["task_name"],
                "time_of_day": a["time_of_day"] or "avond",
                "completed": is_completed,
                "scheduled": True,  # Was gepland
                "extra": False
            })
            seen_tasks.add(a["task_name"])

    # 2. Extra toegevoegde taken (handmatig gepland, bijv. "ik ga vrijdag koken")
    for ea in data.get("extra_assignments", []):
        if ea["member_name"] == member_name:
            task_name = ea["task_name"]
            # Check of deze extra taak al is afgevinkt
            completed_by = data["completions"].get(task_name)
            is_completed = completed_by == member_name

            # Alleen toevoegen als nog niet in seen_tasks
            if task_name not in seen_tasks:
                my_tasks.append({
                    "task_name": task_name,
                    "time_of_day": ea.get("time_of_day") or "avond",
                    "completed": is_completed,
                    "scheduled": False,
                    "extra": True,
                    "extra_id": str(ea["id"])  # Voor verwijderen
                })
                seen_tasks.add(task_name)

    # 3. Extra voltooide taken (niet gepland EN niet als extra toegevoegd, maar wel gedaan)
    for task_name, completer in data["completions"].items():
        if completer == member_name and task_name not in seen_tasks:
            my_tasks.append({
                "task_name": task_name,
                "time_of_day": "avond",
                "completed": True,
                "scheduled": False,
                "extra": False  # Was niet gepland, direct afgevinkt
            })

    open_tasks = [t for t in my_tasks if not t["completed"]]
    done_tasks = [t for t in my_tasks if t["completed"]]

    # Check of dit vandaag is, in het verleden, of in de toekomst
    today = today_local()
    is_today = target_date == today
    is_past = target_date < today
    is_future = target_date > today

    return {
        "member": member_name,
        "date": target_date.isoformat(),
        "day": day_name,
        "is_today": is_today,
        "is_past": is_past,
        "is_future": is_future,
        "open": open_tasks,
        "done": done_tasks,
        "summary": f"{len(open_tasks)} nog te doen, {len(done_tasks)} gedaan"
    }


@app.get("/api/schedule")
async def week_schedule():
    """Haal het weekrooster op met ASCII/emoji overzicht.

    Het rooster wordt persistent opgeslagen:
    - Eerste keer in een week: rooster genereren en opslaan
    - Daarna: opgeslagen rooster teruggeven

    Dit toont per dag wie welke taken moet doen, met afvinkbare checkboxes.
    """
    return engine.get_week_schedule()


@app.get("/api/calendar.ics")
async def get_calendar_feed():
    """
    iCal feed van het weekrooster.

    Subscribe URL: https://cahn-family-assistent.vercel.app/api/calendar.ics

    Familieleden kunnen deze URL toevoegen aan hun kalender app:
    - Google Calendar: Instellingen > Agenda toevoegen > Van URL
    - Apple Calendar: Archief > Nieuw agenda-abonnement
    - Outlook: Agenda toevoegen > Van internet

    De kalender toont:
    - Alle huishoudtaken voor de week
    - Wie aan de beurt is
    - Status (gedaan/nog te doen/gemist)
    """
    schedule_data = engine.get_week_schedule()

    # Haal emails op voor uitnodigingen
    members = get_all_members()
    member_emails = {m.name: m.email for m in members if m.email}

    cal = generate_ical(schedule_data["schedule"], member_emails)

    return Response(
        content=cal.to_ical(),
        media_type="text/calendar",
        headers={
            "Content-Disposition": "attachment; filename=cahn-taken.ics",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"
        }
    )


@app.get("/api/calendar/{member_name}.ics")
async def get_member_calendar_feed(member_name: str):
    """
    Persoonlijke iCal feed voor één gezinslid.

    Subscribe URLs:
    - https://cahn-family-assistent.vercel.app/api/calendar/nora.ics
    - https://cahn-family-assistent.vercel.app/api/calendar/linde.ics
    - https://cahn-family-assistent.vercel.app/api/calendar/fenna.ics

    Elk kind kan hun eigen URL toevoegen aan hun kalender app.
    De kalender toont alleen hun taken met reminders 15 min van tevoren.
    """
    # Valideer member naam
    valid_members = ["nora", "linde", "fenna"]
    member_lower = member_name.lower()

    if member_lower not in valid_members:
        raise HTTPException(
            status_code=404,
            detail=f"Onbekend gezinslid: {member_name}. Kies uit: Nora, Linde, Fenna"
        )

    # Kapitaliseer naam voor weergave
    member_display = member_lower.capitalize()

    schedule_data = engine.get_week_schedule()

    # Haal emails op
    members = get_all_members()
    member_emails = {m.name: m.email for m in members if m.email}

    cal = generate_ical(
        schedule_data["schedule"],
        member_emails,
        filter_member=member_display
    )

    return Response(
        content=cal.to_ical(),
        media_type="text/calendar",
        headers={
            "Content-Disposition": f"attachment; filename=taken-{member_lower}.ics",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"
        }
    )


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


@app.post("/api/swap/same-day")
async def swap_tasks_same_day(request: SameDaySwapRequest):
    """Ruil taken tussen twee kinderen op dezelfde dag.

    Dit is een directe ruil - geen verzoek/acceptatie nodig.
    Bedoeld voor wanneer kinderen onderling afspreken te ruilen.
    """
    try:
        result = engine.swap_same_day_tasks(
            member1_name=request.member1_name,
            member1_task=request.member1_task,
            member2_name=request.member2_name,
            member2_task=request.member2_task,
            swap_date=request.swap_date
        )
        return {
            "success": True,
            "message": f"Geruild! {request.member1_name} doet nu {request.member2_task}, {request.member2_name} doet nu {request.member1_task}"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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


# === Google Actions Webhook ===

@app.post("/webhook/google")
async def google_actions_webhook(request: dict):
    """
    Webhook endpoint voor Google Actions.
    Ontvangt requests van Google Assistant en stuurt responses terug.
    """
    return handle_google_action(request)


# === PWA Assets ===

@app.get("/manifest.json")
async def pwa_manifest():
    """Web App Manifest voor PWA installatie."""
    return JSONResponse({
        "name": "Family Chores",
        "short_name": "Chores",
        "description": "Huishoudelijke taken voor de familie Cahn",
        "start_url": "/taken",
        "display": "standalone",
        "background_color": "#667eea",
        "theme_color": "#4f46e5",
        "orientation": "portrait-primary",
        "icons": [
            {
                "src": "/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": "/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ],
        "categories": ["lifestyle", "utilities"],
        "lang": "nl"
    })


# SVG icon data - house with checkmark
ICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#667eea"/>
      <stop offset="100%" style="stop-color:#764ba2"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="96" fill="url(#bg)"/>
  <path d="M256 100 L420 220 L420 400 L92 400 L92 220 Z" fill="white" opacity="0.95"/>
  <rect x="200" y="300" width="70" height="100" fill="#667eea" rx="4"/>
  <rect x="290" y="250" width="50" height="50" fill="#87ceeb" rx="4" opacity="0.8"/>
  <path d="M256 80 L440 235 L420 255 L256 120 L92 255 L72 235 Z" fill="white"/>
  <circle cx="350" cy="350" r="60" fill="#10b981"/>
  <path d="M325 350 L345 370 L380 330" stroke="white" stroke-width="12" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
</svg>'''


def svg_to_png_data_uri(svg: str, size: int) -> bytes:
    """Convert SVG to a simple PNG representation.

    Note: For proper PNG conversion, we'd need Pillow or cairosvg.
    For now, we serve the SVG as-is since modern browsers support it.
    """
    return svg.encode('utf-8')


@app.get("/icon-192.png")
async def icon_192():
    """192x192 app icon."""
    # Serve SVG with PNG content-type (browsers handle this)
    # For true PNG, you'd need server-side rendering with Pillow/cairo
    return Response(
        content=ICON_SVG.replace('viewBox="0 0 512 512"', 'viewBox="0 0 512 512" width="192" height="192"').encode(),
        media_type="image/svg+xml"
    )


@app.get("/icon-512.png")
async def icon_512():
    """512x512 app icon."""
    return Response(
        content=ICON_SVG.replace('viewBox="0 0 512 512"', 'viewBox="0 0 512 512" width="512" height="512"').encode(),
        media_type="image/svg+xml"
    )


@app.get("/apple-touch-icon.png")
async def apple_touch_icon():
    """Apple touch icon (180x180)."""
    return Response(
        content=ICON_SVG.replace('viewBox="0 0 512 512"', 'viewBox="0 0 512 512" width="180" height="180"').encode(),
        media_type="image/svg+xml"
    )


@app.get("/sw.js")
async def service_worker():
    """Service Worker voor offline caching en push notificaties."""
    sw_code = '''
const CACHE_NAME = 'family-chores-v2';
const STATIC_ASSETS = [
    '/taken',
    '/manifest.json',
    '/icon-192.png',
    '/icon-512.png'
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

// Activate event - cleanup old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((name) => name !== CACHE_NAME)
                    .map((name) => caches.delete(name))
            );
        })
    );
    self.clients.claim();
});

// Fetch event - network first, fallback to cache
self.addEventListener('fetch', (event) => {
    // Skip non-GET requests
    if (event.request.method !== 'GET') return;

    // Skip API calls - always go to network
    if (event.request.url.includes('/api/')) {
        return;
    }

    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // Clone response for caching
                const responseClone = response.clone();
                caches.open(CACHE_NAME).then((cache) => {
                    cache.put(event.request, responseClone);
                });
                return response;
            })
            .catch(() => {
                // Fallback to cache
                return caches.match(event.request).then((cachedResponse) => {
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    // Return offline page for navigation requests
                    if (event.request.mode === 'navigate') {
                        return caches.match('/taken');
                    }
                    return new Response('Offline', { status: 503 });
                });
            })
    );
});

// Push event - toon notificatie
self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        data = { title: 'Family Chores', body: event.data ? event.data.text() : '' };
    }

    const title = data.title || 'Family Chores';
    const options = {
        body: data.body || '',
        icon: '/icon-192.png',
        badge: '/icon-192.png',
        vibrate: [200, 100, 200],
        data: data.data || {},
        tag: data.data?.type || 'default',
        renotify: true
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// Notificatie click - open app
self.addEventListener('notificationclick', (event) => {
    event.notification.close();

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then((clientList) => {
                // Check of de app al open is
                for (const client of clientList) {
                    if (client.url.includes('/taken') && 'focus' in client) {
                        return client.focus();
                    }
                }
                // Anders open een nieuwe window
                if (clients.openWindow) {
                    return clients.openWindow('/taken');
                }
            })
    );
});
'''
    return PlainTextResponse(
        content=sw_code.strip(),
        media_type="application/javascript"
    )


# === Local development ===

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
