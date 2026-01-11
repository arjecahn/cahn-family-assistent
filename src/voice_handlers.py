"""Voice handlers voor Google Actions integratie."""
from datetime import date, timedelta
from typing import Optional
import re

from .task_engine import engine


def handle_google_action(request: dict) -> dict:
    """
    Verwerk een Google Actions request en genereer een response.

    Google Actions stuurt een JSON request met intent informatie.
    We extraheren de intent en parameters, voeren de actie uit,
    en sturen een vriendelijke response terug.
    """
    # Extract intent en parameters uit de request
    intent = extract_intent(request)
    params = extract_parameters(request)

    # Route naar de juiste handler
    handlers = {
        "suggest_task": handle_suggest_task,
        "complete_task": handle_complete_task,
        "weekly_summary": handle_weekly_summary,
        "register_absence": handle_register_absence,
        "request_swap": handle_request_swap,
        "respond_swap": handle_respond_swap,
        "help": handle_help,
    }

    handler = handlers.get(intent, handle_unknown)
    response_text = handler(params)

    return format_google_response(response_text)


def extract_intent(request: dict) -> str:
    """Extract de intent naam uit een Google Actions request."""
    # Google Actions v3 format
    try:
        handler = request.get("handler", {})
        return handler.get("name", "unknown")
    except (KeyError, TypeError):
        pass

    # Fallback: check voor intent in session
    try:
        intent = request.get("intent", {})
        return intent.get("name", "unknown")
    except (KeyError, TypeError):
        return "unknown"


def extract_parameters(request: dict) -> dict:
    """Extract parameters uit een Google Actions request."""
    try:
        # Google Actions v3 format
        intent = request.get("intent", {})
        params = intent.get("params", {})
        # Flatten parameter values
        return {k: v.get("resolved", v.get("original", "")) for k, v in params.items()}
    except (KeyError, TypeError, AttributeError):
        return {}


def format_google_response(text: str, end_conversation: bool = False) -> dict:
    """Format een tekst response voor Google Actions."""
    return {
        "prompt": {
            "firstSimple": {
                "speech": text,
                "text": text
            }
        },
        "scene": {
            "next": {
                "name": "actions.scene.END_CONVERSATION" if end_conversation else "Conversation"
            }
        }
    }


# === Intent Handlers ===

def handle_suggest_task(params: dict) -> str:
    """Handle: 'Wie moet vanavond dekken?'"""
    task_name = params.get("task", "")

    if not task_name:
        return "Welke taak bedoel je? Je kunt vragen wie moet dekken, inruimen, of uitruimen."

    try:
        suggestion = engine.suggest_member_for_task(task_name)
        name = suggestion.suggested_member.name
        reason = suggestion.reason

        # Vriendelijke responses
        responses = [
            f"Ik denk dat {name} aan de beurt is voor {task_name}. {reason}",
            f"Volgens mijn berekening is {name} aan de beurt. {reason}",
            f"{name} zou {task_name} kunnen doen. {reason}",
        ]

        # Voeg toe: "Maar als jullie willen ruilen, laat het me weten!"
        import random
        response = random.choice(responses)
        response += " Maar als jullie willen ruilen, laat het me weten!"

        return response

    except ValueError as e:
        return f"Oeps, ik kon de taak niet vinden. {str(e)}"


def handle_complete_task(params: dict) -> str:
    """Handle: 'Ik heb uitgeruimd' of 'Nora heeft gedekt'"""
    member_name = params.get("member", "")
    task_name = params.get("task", "")

    if not task_name:
        return "Welke taak heb je gedaan?"

    if not member_name:
        return "Wie heeft de taak gedaan? Zeg bijvoorbeeld: Nora heeft gedekt."

    try:
        engine.complete_task(member_name, task_name)

        # Haal de nieuwe score op
        summary = engine.get_weekly_summary()
        total = summary.get(member_name, {}).get("total", 0)

        # Vriendelijke bevestigingen
        compliments = [
            f"Top {member_name}! Ik heb je {task_name} genoteerd.",
            f"Goed bezig {member_name}! {task_name} staat erbij.",
            f"Geregeld! {member_name} heeft {task_name} gedaan.",
        ]

        import random
        response = random.choice(compliments)
        response += f" Je staat nu op {total} taken deze week."

        return response

    except ValueError as e:
        return f"Hmm, dat ging niet helemaal goed. {str(e)}"


def handle_weekly_summary(params: dict) -> str:
    """Handle: 'Hoe staat de score?' of 'Weekoverzicht'"""
    summary = engine.get_weekly_summary()

    if not summary:
        return "Er zijn nog geen taken geregistreerd deze week."

    # Sorteer op aantal taken
    sorted_members = sorted(summary.items(), key=lambda x: x[1]["total"], reverse=True)

    lines = ["Dit is de stand van deze week:"]
    for name, data in sorted_members:
        total = data["total"]
        lines.append(f"{name}: {total} taken")

    # Voeg een vriendelijke opmerking toe
    if len(sorted_members) > 1:
        leader = sorted_members[0][0]
        lines.append(f"{leader} loopt voorop, goed bezig!")

    return " ".join(lines)


def handle_register_absence(params: dict) -> str:
    """Handle: 'Nora is dit weekend weg' of 'Linde is morgen afwezig'"""
    member_name = params.get("member", "")
    duration = params.get("duration", "")

    if not member_name:
        return "Wie is er afwezig?"

    # Parse duration naar start/end dates
    start_date, end_date = parse_absence_duration(duration)

    try:
        engine.register_absence(member_name, start_date, end_date)

        days = (end_date - start_date).days + 1
        if days == 1:
            return f"Oké, ik houd er rekening mee dat {member_name} morgen er niet is. De taken worden herverdeeld."
        else:
            return f"Begrepen! {member_name} is {days} dagen weg. Ik pas de verdeling aan."

    except ValueError as e:
        return f"Dat lukte niet helemaal. {str(e)}"


def parse_absence_duration(duration: str) -> tuple[date, date]:
    """Parse een duratie string naar start en end dates."""
    today = date.today()
    duration_lower = duration.lower() if duration else ""

    if "morgen" in duration_lower:
        tomorrow = today + timedelta(days=1)
        return tomorrow, tomorrow
    elif "weekend" in duration_lower:
        # Zoek de eerstvolgende zaterdag
        days_until_saturday = (5 - today.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7
        saturday = today + timedelta(days=days_until_saturday)
        sunday = saturday + timedelta(days=1)
        return saturday, sunday
    elif "week" in duration_lower:
        return today, today + timedelta(days=7)
    else:
        # Default: vandaag
        return today, today


def handle_request_swap(params: dict) -> str:
    """Handle: 'Mag ik ruilen met Linde?'"""
    requester = params.get("requester", "")
    target = params.get("target", "")
    task_name = params.get("task", "")

    if not requester or not target:
        return "Met wie wil je ruilen? Zeg bijvoorbeeld: Nora wil ruilen met Linde voor dekken."

    if not task_name:
        return f"Welke taak wil {requester} ruilen met {target}?"

    try:
        engine.request_swap(requester, target, task_name, date.today())
        return f"Oké, ik vraag aan {target} of die wil ruilen. {target}, wil je {task_name} overnemen van {requester}?"

    except ValueError as e:
        return f"Dat lukte niet. {str(e)}"


def handle_respond_swap(params: dict) -> str:
    """Handle: 'Ja ik wil ruilen' of 'Nee bedankt'"""
    accept = params.get("accept", False)

    # In een echte implementatie zouden we de swap_id uit de sessie halen
    if accept:
        return "Top! De ruil is geregeld. Ik pas de verdeling aan."
    else:
        return "Geen probleem, de ruil is afgewezen. De oorspronkelijke verdeling blijft staan."


def handle_help(params: dict) -> str:
    """Handle: 'Wat kun je allemaal?'"""
    return (
        "Ik kan je helpen met het verdelen van huishoudelijke taken. "
        "Je kunt me vragen: Wie moet vanavond dekken? Of zeggen: Ik heb uitgeruimd. "
        "Ook kun je afwezigheid doorgeven of taken ruilen met je zus. "
        "Vraag maar: Hoe staat de score? voor het weekoverzicht."
    )


def handle_unknown(params: dict) -> str:
    """Handle onbekende intents."""
    return (
        "Hmm, dat begreep ik niet helemaal. "
        "Je kunt me vragen wie er aan de beurt is voor een taak, "
        "of doorgeven dat je een taak hebt gedaan."
    )
