"""FastAPI app voor de Cahn Family Task Assistant."""
import os
import secrets
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException, Depends, Header, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

from .task_engine import engine
from .database import (
    seed_initial_data, reset_tasks_2026, update_task_targets, get_all_tasks,
    get_member_by_name, get_last_completion_for_member, delete_completion,
    migrate_add_cascade_delete, migrate_add_schedule_table, migrate_add_missed_tasks_table,
    migrate_add_member_email, update_member_email, get_all_members,
    get_missed_tasks_for_week, get_missed_tasks_for_member
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
    <meta name="theme-color" content="#4f46e5">
    <title>Huishoudtaken</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
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
        }
        @keyframes float {
            0%, 100% { transform: translateY(0) rotate(0deg); }
            25% { transform: translateY(-15px) rotate(5deg); }
            50% { transform: translateY(-5px) rotate(-3deg); }
            75% { transform: translateY(-20px) rotate(3deg); }
        }

        /* Nora's pinguïn */
        .penguin-container {
            position: fixed;
            bottom: 20px;
            right: 20px;
            font-size: 120px;
            pointer-events: none;
            z-index: 0;
            opacity: 0;
            transition: opacity 0.5s, transform 0.5s;
            transform: translateY(20px);
        }
        .penguin-container.active {
            opacity: 1;
            transform: translateY(0);
            animation: waddle 2s ease-in-out infinite;
        }
        @keyframes waddle {
            0%, 100% { transform: rotate(-3deg); }
            50% { transform: rotate(3deg); }
        }

        .picker button[data-member="Fenna"].active::after { content: ' 🐱'; }
        .picker button[data-member="Nora"].active::after { content: ' 🐧'; }
    </style>
</head>
<body>
    <!-- Fenna's zwevende katjes -->
    <div class="cats-container" id="catsContainer"></div>
    <!-- Nora's grote pinguïn -->
    <div class="penguin-container" id="penguinContainer">🐧</div>

    <div class="container">
        <h1>Huishoudtaken</h1>

        <div class="picker" id="picker">
            <button data-member="Nora" onclick="selectMember('Nora')">Nora</button>
            <button data-member="Linde" onclick="selectMember('Linde')">Linde</button>
            <button data-member="Fenna" onclick="selectMember('Fenna')">Fenna</button>
        </div>

        <div class="card">
            <div id="tasks">
                <div class="loading">Kies je naam...</div>
            </div>
            <div class="summary" id="summary"></div>
        </div>

        <button class="refresh" onclick="loadTasks()">Vernieuwen</button>
    </div>

    <!-- Waarom Modal -->
    <div class="modal-overlay" id="whyModal" onclick="closeModal(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <button class="close-btn" onclick="closeModal()">&times;</button>
            <h2 id="modalTitle">Waarom ik?</h2>
            <div id="modalContent">Laden...</div>
        </div>
    </div>

    <script>
        const API = '';
        let currentMember = localStorage.getItem('member');
        const catEmojis = ['🐱', '😺', '😸', '🐈', '🐈‍⬛', '😻', '🙀', '😹'];

        // Genereer zwevende katjes voor Fenna
        function initCats() {
            const container = document.getElementById('catsContainer');
            for (let i = 0; i < 12; i++) {
                const cat = document.createElement('div');
                cat.className = 'cat';
                cat.textContent = catEmojis[Math.floor(Math.random() * catEmojis.length)];
                cat.style.left = Math.random() * 100 + '%';
                cat.style.top = Math.random() * 100 + '%';
                cat.style.animationDelay = (Math.random() * 6) + 's';
                cat.style.fontSize = (18 + Math.random() * 16) + 'px';
                container.appendChild(cat);
            }
        }
        initCats();

        if (currentMember) {
            selectMember(currentMember);
        }

        function selectMember(name) {
            currentMember = name;
            localStorage.setItem('member', name);
            document.querySelectorAll('.picker button').forEach(b => {
                b.classList.toggle('active', b.dataset.member === name);
            });

            // Toon katjes voor Fenna, pinguïn voor Nora
            document.getElementById('catsContainer').classList.toggle('active', name === 'Fenna');
            document.getElementById('penguinContainer').classList.toggle('active', name === 'Nora');

            loadTasks();
        }

        async function loadTasks() {
            if (!currentMember) return;

            document.getElementById('tasks').innerHTML = '<div class="loading">Laden...</div>';

            try {
                const res = await fetch(API + '/api/my-tasks/' + currentMember);
                const data = await res.json();
                renderTasks(data);
            } catch (e) {
                document.getElementById('tasks').innerHTML = '<div class="empty">Fout bij laden</div>';
            }
        }

        function renderTasks(data) {
            const tasks = [...data.open, ...data.done];

            if (tasks.length === 0) {
                document.getElementById('tasks').innerHTML = '<div class="empty">Geen taken vandaag!</div>';
                document.getElementById('summary').textContent = '';
                return;
            }

            const html = tasks.map(t => {
                const timeLabel = {ochtend: 'Ochtend', middag: 'Middag', avond: 'Avond'}[t.time_of_day] || '';
                return `
                    <div class="task ${t.completed ? 'done' : ''}">
                        <div class="check" onclick="toggleTask('${t.task_name}', ${t.completed})">${t.completed ? '✓' : ''}</div>
                        <div class="info" onclick="toggleTask('${t.task_name}', ${t.completed})">
                            <div class="name">${t.task_name}</div>
                            <div class="time">${timeLabel}</div>
                        </div>
                        <button class="why-btn" onclick="showWhy('${t.task_name}')" title="Waarom ik?">?</button>
                    </div>
                `;
            }).join('');

            document.getElementById('tasks').innerHTML = html;
            document.getElementById('summary').textContent = data.summary;
        }

        async function toggleTask(taskName, isDone) {
            if (isDone) return; // Kan niet un-doen via UI

            try {
                const res = await fetch(API + '/api/complete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({member_name: currentMember, task_name: taskName})
                });
                if (res.ok) {
                    loadTasks();
                }
            } catch (e) {
                alert('Fout bij afvinken');
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
    </script>
</body>
</html>"""


@app.get("/api/my-tasks/{member_name}")
async def get_my_tasks_today(member_name: str):
    """Haal taken van vandaag op voor een specifiek gezinslid.

    GEOPTIMALISEERD: Één database call, minimale data.
    Perfect voor PWA en iOS Shortcuts.
    """
    from .database import get_today_tasks_for_member, today_local

    today = today_local()
    week_number = today.isocalendar()[1]
    year = today.isocalendar()[0]
    day_of_week = today.weekday()
    day_names = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
    today_name = day_names[day_of_week]

    # Eén database call voor alles
    data = get_today_tasks_for_member(member_name, week_number, year, day_of_week, today)

    my_tasks = []
    for a in data["assignments"]:
        # Check of dit voor mij is of ik het heb gedaan
        completed_by = data["completions"].get(a["task_name"])
        is_completed = completed_by is not None
        is_mine = (a["member_name"] == member_name) or (completed_by == member_name)

        if is_mine:
            my_tasks.append({
                "task_name": a["task_name"],
                "time_of_day": a["time_of_day"] or "avond",
                "completed": is_completed
            })

    open_tasks = [t for t in my_tasks if not t["completed"]]
    done_tasks = [t for t in my_tasks if t["completed"]]

    return {
        "member": member_name,
        "date": today.isoformat(),
        "day": today_name,
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
            "Cache-Control": "no-cache, max-age=300"  # 5 min cache
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


# === Local development ===

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
