"""Core logica voor eerlijke takenverdeling."""
from datetime import date, datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from .models import Member, Task, Completion
from . import database as db
from .database import now_local, today_local, TIMEZONE

# Dag namen in het Nederlands
DAY_NAMES = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
DAY_EMOJIS = ["🌙", "🔥", "💧", "⚡", "🌸", "🌟", "☀️"]

# Maand namen in het Nederlands
MONTH_NAMES = ["", "januari", "februari", "maart", "april", "mei", "juni",
               "juli", "augustus", "september", "oktober", "november", "december"]


@dataclass
class MemberScore:
    """Score voor een gezinslid."""
    member: Member
    total_tasks_this_week: int
    specific_task_count: int
    last_did_task: Optional[datetime]
    is_available: bool
    weighted_score: float


@dataclass
class TaskSuggestion:
    """Suggestie voor wie een taak moet doen."""
    suggested_member: Member
    reason: str
    scores: list[MemberScore]


class TaskEngine:
    """Engine voor het beheren van huishoudelijke taken."""

    def get_current_week(self) -> int:
        """Geef het huidige ISO weeknummer."""
        return today_local().isocalendar()[1]

    def get_week_start(self, week_number: Optional[int] = None) -> date:
        """Geef de startdatum (maandag) van een week."""
        if week_number is None:
            week_number = self.get_current_week()
        year = today_local().year
        return date.fromisocalendar(year, week_number, 1)

    def is_member_available(self, member: Member, check_date: Optional[date] = None) -> bool:
        """Check of een gezinslid beschikbaar is (niet afwezig)."""
        if check_date is None:
            check_date = today_local()
        absence = db.get_absence_for_date(member.id, check_date)
        return absence is None

    def get_available_members(self, check_date: Optional[date] = None) -> list[Member]:
        """Geef alle beschikbare gezinsleden."""
        all_members = db.get_all_members()
        return [m for m in all_members if self.is_member_available(m, check_date)]

    def get_task_count_this_week(self, member: Member, task: Optional[Task] = None) -> int:
        """Tel hoeveel taken een lid deze week heeft gedaan."""
        week_number = self.get_current_week()
        completions = db.get_completions_for_member(member.id, week_number)

        if task:
            return len([c for c in completions if c.task_id == task.id])
        return len(completions)

    def get_last_completion(self, member: Member, task: Task) -> Optional[datetime]:
        """Wanneer deed dit lid deze taak voor het laatst?"""
        completion = db.get_last_completion_for_task(member.id, task.id)
        return completion.completed_at if completion else None

    def calculate_weighted_score(
        self,
        member: Member,
        task: Task,
        available_members: list[Member]
    ) -> float:
        """
        Bereken een gewogen score voor wie de taak moet doen.
        Lagere score = meer aan de beurt.

        Weging:
        - 50%: Totaal aantal taken deze week
        - 30%: Aantal keer deze specifieke taak gedaan
        - 20%: Hoe lang geleden deze taak gedaan (recency)
        """
        total_tasks = self.get_task_count_this_week(member)
        specific_tasks = self.get_task_count_this_week(member, task)
        last_did = self.get_last_completion(member, task)

        # Normaliseer scores relatief aan andere beschikbare leden
        max_total = max(self.get_task_count_this_week(m) for m in available_members) or 1
        max_specific = max(self.get_task_count_this_week(m, task) for m in available_members) or 1

        # Recency score: 0 = net gedaan, 1 = lang geleden of nooit
        if last_did:
            days_ago = (now_local() - last_did).days
            recency_score = min(days_ago / 7, 1.0)
        else:
            recency_score = 1.0

        weighted = (
            (total_tasks / max_total) * 0.5 +
            (specific_tasks / max_specific) * 0.3 +
            (1 - recency_score) * 0.2
        )

        return weighted

    def suggest_member_for_task(self, task_name: str) -> TaskSuggestion:
        """Suggereer wie een taak moet doen."""
        task = db.get_task_by_name(task_name)
        if not task:
            raise ValueError(f"Taak '{task_name}' niet gevonden")

        available = self.get_available_members()
        if not available:
            raise ValueError("Niemand is beschikbaar!")

        scores = []
        for member in available:
            score = self.calculate_weighted_score(member, task, available)
            scores.append(MemberScore(
                member=member,
                total_tasks_this_week=self.get_task_count_this_week(member),
                specific_task_count=self.get_task_count_this_week(member, task),
                last_did_task=self.get_last_completion(member, task),
                is_available=True,
                weighted_score=score
            ))

        scores.sort(key=lambda s: s.weighted_score)
        suggested = scores[0]
        reason = self._generate_reason(suggested, scores, task)

        return TaskSuggestion(
            suggested_member=suggested.member,
            reason=reason,
            scores=scores
        )

    def _generate_reason(
        self,
        suggested: MemberScore,
        all_scores: list[MemberScore],
        task: Task
    ) -> str:
        """Genereer een vriendelijke uitleg waarom iemand aan de beurt is."""
        name = suggested.member.name
        total = suggested.total_tasks_this_week

        others = [s for s in all_scores if s.member.id != suggested.member.id]
        if not others:
            return f"{name} is de enige die beschikbaar is."

        other_totals = [s.total_tasks_this_week for s in others]
        max_other = max(other_totals)

        if total < max_other:
            diff = max_other - total
            return (
                f"{name} heeft deze week pas {total} taken gedaan, "
                f"terwijl anderen er al {max_other} hebben."
            )
        elif suggested.specific_task_count == 0:
            return f"{name} heeft {task.display_name} deze week nog niet gedaan."
        else:
            return f"{name} is het langst geleden dat die {task.display_name} heeft gedaan."

    def complete_task(self, member_name: str, task_name: str, completed_date: Optional[date] = None) -> Completion:
        """Registreer dat iemand een taak heeft voltooid.

        Args:
            member_name: Naam van het gezinslid
            task_name: Naam van de taak
            completed_date: Optioneel - datum waarop de taak is gedaan (default: vandaag)
        """
        member = db.get_member_by_name(member_name)
        if not member:
            raise ValueError(f"Gezinslid '{member_name}' niet gevonden")

        task = db.get_task_by_name(task_name)
        if not task:
            raise ValueError(f"Taak '{task_name}' niet gevonden")

        # Bepaal week nummer voor de completion date
        if completed_date:
            week_number = completed_date.isocalendar()[1]
        else:
            week_number = self.get_current_week()

        completion = db.add_completion({
            "task_id": task.id,
            "member_id": member.id,
            "member_name": member.name,
            "task_name": task.display_name,
            "week_number": week_number,
            "completed_date": completed_date
        })

        return completion

    def complete_tasks_bulk(self, tasks_data: list[dict]) -> list[Completion]:
        """Registreer meerdere taken in één transactie.

        Args:
            tasks_data: Lijst van dicts met member_name, task_name, en optioneel completed_date

        Returns:
            Lijst van Completion objecten

        Raises:
            ValueError: Als een member of task niet gevonden wordt (geen enkele taak wordt opgeslagen)
        """
        # Eerst valideren - als iets niet klopt, stoppen we voordat we iets opslaan
        completions_to_add = []

        for item in tasks_data:
            member = db.get_member_by_name(item["member_name"])
            if not member:
                raise ValueError(f"Gezinslid '{item['member_name']}' niet gevonden")

            task = db.get_task_by_name(item["task_name"])
            if not task:
                raise ValueError(f"Taak '{item['task_name']}' niet gevonden")

            # Bepaal week nummer
            completed_date = item.get("completed_date")
            if completed_date:
                week_number = completed_date.isocalendar()[1]
            else:
                week_number = self.get_current_week()

            completions_to_add.append({
                "task_id": task.id,
                "member_id": member.id,
                "member_name": member.name,
                "task_name": task.display_name,
                "week_number": week_number,
                "completed_date": completed_date
            })

        # Alles gevalideerd - nu opslaan in één transactie
        return db.add_completions_bulk(completions_to_add)

    def register_absence(
        self,
        member_name: str,
        start: date,
        end: date,
        reason: Optional[str] = None
    ):
        """Registreer afwezigheid van een gezinslid."""
        member = db.get_member_by_name(member_name)
        if not member:
            raise ValueError(f"Gezinslid '{member_name}' niet gevonden")

        return db.add_absence({
            "member_id": member.id,
            "member_name": member.name,
            "start_date": start,
            "end_date": end,
            "reason": reason
        })

    def get_weekly_summary(self) -> dict:
        """Geef een overzicht van de taken deze week."""
        week_number = self.get_current_week()
        members = db.get_all_members()

        summary = {}
        for member in members:
            completions = db.get_completions_for_member(member.id, week_number)
            summary[member.name] = {
                "total": len(completions),
                "tasks": {}
            }
            for completion in completions:
                task_name = completion.task_name
                if task_name not in summary[member.name]["tasks"]:
                    summary[member.name]["tasks"][task_name] = 0
                summary[member.name]["tasks"][task_name] += 1

        return summary

    def request_swap(
        self,
        requester_name: str,
        target_name: str,
        task_name: str,
        swap_date: date
    ):
        """Vraag een ruil aan."""
        requester = db.get_member_by_name(requester_name)
        target = db.get_member_by_name(target_name)
        task = db.get_task_by_name(task_name)

        if not requester:
            raise ValueError(f"'{requester_name}' niet gevonden")
        if not target:
            raise ValueError(f"'{target_name}' niet gevonden")
        if not task:
            raise ValueError(f"Taak '{task_name}' niet gevonden")

        return db.add_swap({
            "requester_id": requester.id,
            "requester_name": requester.name,
            "target_id": target.id,
            "target_name": target.name,
            "task_id": task.id,
            "task_name": task.display_name,
            "swap_date": swap_date,
            "status": "pending"
        })

    def respond_to_swap(self, swap_id: str, accept: bool):
        """Reageer op een ruil verzoek."""
        status = "accepted" if accept else "rejected"
        db.update_swap_status(swap_id, status)

    def get_pending_swaps(self, member_name: str):
        """Haal alle openstaande ruil verzoeken op voor een lid."""
        member = db.get_member_by_name(member_name)
        if not member:
            return []
        return db.get_pending_swaps_for_member(member.id)


    def get_week_schedule(self) -> dict:
        """
        Genereer een flexibel weekrooster met wie wat moet doen per dag.

        De planning is volledig flexibel:
        - Taken worden verdeeld op basis van aanwezigheid
        - Geen vaste dagen voor specifieke taken
        - Eerlijke verdeling over beschikbare personen
        - Zondag telt ook mee

        Returns een dict met:
        - schedule: per dag een lijst van taken met toegewezen persoon
        - completed: welke taken al zijn gedaan
        - ascii_overview: ASCII/emoji overzicht
        """
        week_number = self.get_current_week()
        week_start = self.get_week_start(week_number)
        week_end = week_start + timedelta(days=6)

        # === BATCH QUERIES: Alles in 4 queries ipv 30+ ===
        members = db.get_all_members()
        tasks = db.get_all_tasks()
        all_completions = db.get_completions_for_week(week_number)
        week_absences = db.get_absences_for_week(week_start, week_end)

        # Bouw het schema per dag
        schedule = {}
        for day_idx in range(7):
            day_date = week_start + timedelta(days=day_idx)
            day_name = DAY_NAMES[day_idx]
            schedule[day_name] = {
                "date": day_date.isoformat(),
                "emoji": DAY_EMOJIS[day_idx],
                "tasks": []
            }

        # Bepaal per dag wie beschikbaar is (in-memory lookup, geen DB calls)
        day_availability = {}
        for day_idx in range(7):
            day_date = week_start + timedelta(days=day_idx)
            day_name = DAY_NAMES[day_idx]
            available = []
            for m in members:
                # Check of er een afwezigheid is die deze dag overlapt
                is_absent = any(
                    a.member_id == m.id and a.start_date <= day_date <= a.end_date
                    for a in week_absences
                )
                if not is_absent:
                    available.append(m)
            day_availability[day_name] = available

        # Track hoeveel taken per persoon per week (inclusief al gedane taken)
        member_week_counts = {m.name: 0 for m in members}
        for c in all_completions:
            if c.member_name in member_week_counts:
                member_week_counts[c.member_name] += 1

        # Verdeel taken flexibel over de week
        # Stap 1: Bepaal voor elke taak op welke dagen deze moet worden gedaan
        task_days = self._distribute_tasks_over_week(tasks, day_availability)

        # Stap 2: Wijs taken toe aan beschikbare personen per dag
        daily_assignments = {day: [] for day in DAY_NAMES}

        for day_idx in range(7):
            day_name = DAY_NAMES[day_idx]
            day_date = week_start + timedelta(days=day_idx)
            available_members = day_availability[day_name]

            if not available_members:
                continue  # Niemand beschikbaar vandaag

            # Welke taken zijn vandaag gepland?
            today_tasks = [t for t in tasks if day_idx in task_days.get(t.name, [])]

            # Sorteer beschikbare leden op aantal taken (minste eerst)
            for task in today_tasks:
                # Check of deze taak al is gedaan vandaag
                already_done = False
                done_by = None

                for c in all_completions:
                    # Vergelijk op task_name want task_id kan veranderen na reset
                    if c.task_name == task.display_name and c.completed_at.date() == day_date:
                        already_done = True
                        done_by = c.member_name
                        break

                if already_done:
                    daily_assignments[day_name].append({
                        "task_name": task.display_name,
                        "assigned_to": done_by,
                        "completed": True,
                        "time_of_day": task.time_of_day
                    })
                else:
                    # Kies de beschikbare persoon met minste taken
                    sorted_available = sorted(
                        available_members,
                        key=lambda m: member_week_counts[m.name]
                    )
                    assigned = sorted_available[0]
                    member_week_counts[assigned.name] += 1

                    daily_assignments[day_name].append({
                        "task_name": task.display_name,
                        "assigned_to": assigned.name,
                        "completed": False,
                        "time_of_day": task.time_of_day
                    })

        # Sorteer taken per dag op time_of_day
        time_order = {"ochtend": 0, "middag": 1, "avond": 2}
        for day_name in daily_assignments:
            daily_assignments[day_name].sort(key=lambda t: time_order.get(t["time_of_day"], 1))
            schedule[day_name]["tasks"] = daily_assignments[day_name]

        # Genereer ASCII/emoji overzicht (geef members en tasks mee om queries te besparen)
        ascii_overview = self._generate_ascii_schedule(
            schedule, week_start, day_availability, member_week_counts,
            members=members, tasks=tasks
        )

        return {
            "week_number": week_number,
            "week_start": week_start.isoformat(),
            "schedule": schedule,
            "ascii_overview": ascii_overview,
            "member_totals": member_week_counts,
            "day_availability": {day: [m.name for m in members] for day, members in day_availability.items()}
        }

    def _distribute_tasks_over_week(self, tasks: list, day_availability: dict) -> dict:
        """
        Verdeel taken flexibel over de week.

        Regels:
        - Taken worden verdeeld op basis van weekly_target
        - Voorkeur voor dagen waar mensen beschikbaar zijn
        - Spreiding over de week voor afwisseling
        - Taken met lagere targets worden verspreid over verschillende dagen
        """
        task_days = {}

        # Sorteer taken op target (hoogste eerst), zodat dagelijkse taken eerst komen
        sorted_tasks = sorted(tasks, key=lambda t: -t.weekly_target)

        # Track hoeveel taken per dag al zijn toegewezen (voor balans)
        day_task_count = {day_idx: 0 for day_idx in range(7)}

        for task in sorted_tasks:
            target = task.weekly_target
            task_days[task.name] = []

            if target <= 0:
                continue

            # Bepaal geschikte dagen (waar minstens 1 persoon beschikbaar is)
            suitable_days = []
            for day_idx, day_name in enumerate(DAY_NAMES):
                if day_availability[day_name]:  # Er is iemand beschikbaar
                    suitable_days.append(day_idx)

            if not suitable_days:
                continue

            # Verdeel taken gelijkmatig over beschikbare dagen
            if target >= len(suitable_days):
                # Taak moet (bijna) elke dag: gebruik alle beschikbare dagen
                task_days[task.name] = suitable_days[:target]
                for day_idx in task_days[task.name]:
                    day_task_count[day_idx] += 1
            else:
                # Verspreid taken zo goed mogelijk met voorkeur voor minst belaste dagen
                # Sorteer geschikte dagen op huidige belasting
                sorted_suitable = sorted(suitable_days, key=lambda d: day_task_count[d])

                # Kies de dagen met minste taken, maar wel verspreid
                selected = []
                step = len(suitable_days) / target

                for i in range(target):
                    # Bereken ideale positie in de week
                    ideal_pos = i * step
                    # Vind de dag dichtstbij ideale positie die nog niet gekozen is
                    best_day = None
                    best_score = float('inf')

                    for day_idx in suitable_days:
                        if day_idx in selected:
                            continue
                        # Score = afstand van ideale positie + belasting penalty
                        pos_in_suitable = suitable_days.index(day_idx)
                        distance = abs(pos_in_suitable - ideal_pos)
                        load_penalty = day_task_count[day_idx] * 0.5
                        score = distance + load_penalty

                        if score < best_score:
                            best_score = score
                            best_day = day_idx

                    if best_day is not None:
                        selected.append(best_day)
                        day_task_count[best_day] += 1

                task_days[task.name] = sorted(selected)

        return task_days

    def _generate_ascii_schedule(self, schedule: dict, week_start: date,
                                   day_availability: dict, member_totals: dict,
                                   members: list = None, tasks: list = None) -> str:
        """Genereer een ASCII/emoji weekoverzicht."""
        lines = []

        # Header
        week_num = self.get_current_week()
        lines.append("╔═══════════════════════════════════════════════════╗")
        lines.append(f"║  📅 WEEKROOSTER week {week_num:<2}                          ║")
        lines.append("╠═══════════════════════════════════════════════════╣")

        today = today_local()
        # Gebruik meegegeven members of haal ze op (fallback)
        all_members = members if members else db.get_all_members()

        for day_idx, day_name in enumerate(DAY_NAMES):
            day_data = schedule[day_name]
            day_date = week_start + timedelta(days=day_idx)
            emoji = day_data["emoji"]
            available = day_availability.get(day_name, [])

            # Markeer vandaag
            if day_date == today:
                day_marker = "👉"
            else:
                day_marker = "  "

            # Dag header
            date_str = day_date.strftime("%d/%m")
            header = f"{day_marker}{emoji} {day_name.upper():<9} ({date_str})"
            lines.append(f"║ {header:<48}║")

            # Toon afwezigen als er iemand niet beschikbaar is
            absent = [m.name for m in all_members if m not in available]
            if absent:
                absent_str = ", ".join(absent)
                lines.append(f"║    🚫 Afwezig: {absent_str:<33}║")

            day_tasks = day_data["tasks"]
            if not day_tasks:
                if not absent:
                    lines.append("║    (geen taken gepland)                           ║")
            else:
                for day_task in day_tasks:
                    check = "✅" if day_task["completed"] else "⬜"
                    name = day_task["assigned_to"][:6]  # Max 6 chars voor naam
                    task_display = day_task["task_name"][:25]  # Max 25 chars voor taak
                    line = f"{check} {name}: {task_display}"
                    lines.append(f"║    {line:<46}║")

            if day_idx < 6:
                lines.append("║───────────────────────────────────────────────────║")

        # Maandoverzicht per taak per persoon
        lines.append("╠═══════════════════════════════════════════════════╣")
        month_stats = self._get_monthly_task_stats(members=all_members, tasks=tasks)
        month_name = MONTH_NAMES[today.month].upper()
        lines.append(f"║  📊 STAND {month_name:<38}║")
        lines.append("║                    Nora  Linde Fenna              ║")
        lines.append("║───────────────────────────────────────────────────║")

        for task_name, stats in month_stats.items():
            # Kort de taaknaam af indien nodig
            short_name = task_name[:14]
            nora = f"{stats['Nora']['done']}/{stats['Nora']['target']}"
            linde = f"{stats['Linde']['done']}/{stats['Linde']['target']}"
            fenna = f"{stats['Fenna']['done']}/{stats['Fenna']['target']}"
            lines.append(f"║  {short_name:<16} {nora:>5} {linde:>5} {fenna:>5}              ║")

        lines.append("╚═══════════════════════════════════════════════════╝")

        return "\n".join(lines)

    def _get_monthly_task_stats(self, members: list = None, tasks: list = None) -> dict:
        """Bereken per taak hoeveel elke persoon heeft gedaan deze maand."""
        import calendar

        today = today_local()
        year = today.year
        month = today.month

        # Hoeveel weken zitten er in deze maand (voor targets)
        _, days_in_month = calendar.monthrange(year, month)
        weeks_in_month = days_in_month / 7

        # Haal alle completions voor deze maand op (1 query)
        completions = db.get_completions_for_month(year, month)

        # Gebruik meegegeven data of haal op (fallback)
        if tasks is None:
            tasks = db.get_all_tasks()
        if members is None:
            members = db.get_all_members()
        member_names = [m.name for m in members]

        # Bouw de stats op
        stats = {}
        for task in tasks:
            # Maandelijks target per persoon = weekly_target * weken / 3 personen
            monthly_target_per_person = round(task.weekly_target * weeks_in_month / len(members))
            # Minimum 1 als er überhaupt een target is
            if task.weekly_target > 0 and monthly_target_per_person == 0:
                monthly_target_per_person = 1

            stats[task.display_name] = {}
            for name in member_names:
                # Tel hoeveel deze persoon deze taak heeft gedaan
                # Vergelijk op task_name (display_name) want task_id kan veranderen na reset
                done = sum(1 for c in completions
                          if c.member_name == name and c.task_name == task.display_name)
                stats[task.display_name][name] = {
                    "done": done,
                    "target": monthly_target_per_person
                }

        return stats


# Singleton instance
engine = TaskEngine()
