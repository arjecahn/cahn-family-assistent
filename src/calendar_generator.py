"""iCal generator voor het weekrooster."""
from datetime import datetime, timedelta
from icalendar import Calendar, Event

# Tijdslots voor taken - gekoppeld aan logische tijden
TIME_SLOTS = {
    "ochtend": (7, 30),   # 07:30 - voor school
    "middag": (14, 0),    # 14:00 - na school
    "avond": (18, 30),    # 18:30 - rond etenstijd
}


def generate_ical(schedule: dict, member_emails: dict = None) -> Calendar:
    """
    Genereer een iCal calendar van het weekrooster.

    Args:
        schedule: Dict met 'schedule' key containing days with tasks
        member_emails: Dict van naam -> email voor uitnodigingen

    Returns:
        icalendar.Calendar object
    """
    if member_emails is None:
        member_emails = {}

    cal = Calendar()
    cal.add('prodid', '-//Cahn Family Tasks//NL')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', 'Huishoudtaken Cahn')
    cal.add('x-wr-timezone', 'Europe/Amsterdam')

    # Loop door alle dagen in het rooster
    # schedule format: {"maandag": {"date": "2026-01-12", "tasks": [...]}, ...}
    for day_name, day_data in schedule.items():
        if not isinstance(day_data, dict):
            continue

        date_str = day_data.get("date")
        if not date_str:
            continue

        tasks = day_data.get("tasks", [])

        for task in tasks:
            event = Event()

            # Titel met status en wie het doet
            task_name = task.get("task_name", "Taak")
            assignee = task.get("assigned_to")
            completed = task.get("completed", False)
            completed_by = task.get("completed_by")
            is_missed = task.get("missed", False)

            # Korte leesbare titel: "Taak - Naam" met optionele emoji
            person = completed_by or assignee or "?"
            if completed:
                title = f"✓ {task_name} - {person}"
            elif is_missed:
                title = f"✗ {task_name} - {person}"
            else:
                title = f"{task_name} - {person}"

            event.add('summary', title)

            # Start/eindtijd bepalen
            time_of_day = task.get("time_of_day", "avond")
            hour, minute = TIME_SLOTS.get(time_of_day, (18, 30))

            try:
                start = datetime.fromisoformat(date_str).replace(hour=hour, minute=minute)
            except ValueError:
                continue  # Skip als datum ongeldig is

            event.add('dtstart', start)
            event.add('dtend', start + timedelta(minutes=30))

            # Status als beschrijving
            if completed:
                event.add('description', f'Voltooid door {completed_by or assignee}')
                event.add('status', 'CONFIRMED')
            elif is_missed:
                event.add('description', 'Niet gedaan (papa/mama heeft het gedaan)')
                event.add('status', 'CANCELLED')
            else:
                event.add('description', f'Toegewezen aan {assignee}')
                event.add('status', 'TENTATIVE')

            # Unieke ID voor deze taak op deze dag
            # Format: YYYY-MM-DD-taskname@cahn-family
            safe_task_name = task_name.replace(" ", "_").replace("/", "-")
            uid = f"{date_str}-{safe_task_name}@cahn-family"
            event.add('uid', uid)

            # Timestamp voor wanneer dit event is aangemaakt/gewijzigd
            event.add('dtstamp', datetime.now())

            # Niet als "busy" tonen in kalender
            event.add('transp', 'TRANSPARENT')

            # Voeg attendee toe voor uitnodiging
            attendee_person = assignee if not completed else (completed_by or assignee)
            if attendee_person and attendee_person in member_emails and member_emails[attendee_person]:
                event.add('organizer', 'mailto:arje@cahn.com')
                event.add('attendee', f'mailto:{member_emails[attendee_person]}',
                         parameters={
                             'CN': attendee_person,
                             'PARTSTAT': 'ACCEPTED' if completed else 'NEEDS-ACTION',
                             'ROLE': 'REQ-PARTICIPANT'
                         })

            cal.add_component(event)

    return cal
