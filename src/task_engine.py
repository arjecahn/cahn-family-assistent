"""Core logica voor eerlijke takenverdeling."""
from datetime import date, datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from .models import Member, Task, Completion
from . import database as db

# Dag namen in het Nederlands
DAY_NAMES = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
DAY_EMOJIS = ["🌙", "🔥", "💧", "⚡", "🌸", "🌟", "☀️"]


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
        return date.today().isocalendar()[1]

    def get_week_start(self, week_number: Optional[int] = None) -> date:
        """Geef de startdatum (maandag) van een week."""
        if week_number is None:
            week_number = self.get_current_week()
        year = date.today().year
        return date.fromisocalendar(year, week_number, 1)

    def is_member_available(self, member: Member, check_date: Optional[date] = None) -> bool:
        """Check of een gezinslid beschikbaar is (niet afwezig)."""
        if check_date is None:
            check_date = date.today()
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
            days_ago = (datetime.utcnow() - last_did).days
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

    def complete_task(self, member_name: str, task_name: str) -> Completion:
        """Registreer dat iemand een taak heeft voltooid."""
        member = db.get_member_by_name(member_name)
        if not member:
            raise ValueError(f"Gezinslid '{member_name}' niet gevonden")

        task = db.get_task_by_name(task_name)
        if not task:
            raise ValueError(f"Taak '{task_name}' niet gevonden")

        completion = db.add_completion({
            "task_id": task.id,
            "member_id": member.id,
            "member_name": member.name,
            "task_name": task.display_name,
            "week_number": self.get_current_week()
        })

        return completion

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
        Genereer een weekrooster met wie wat moet doen per dag.

        Returns een dict met:
        - schedule: per dag een lijst van taken met toegewezen persoon
        - completed: welke taken al zijn gedaan
        - ascii_overview: ASCII/emoji overzicht
        """
        week_number = self.get_current_week()
        week_start = self.get_week_start(week_number)
        members = db.get_all_members()
        tasks = db.get_all_tasks()

        # Haal alle completions voor deze week op
        all_completions = []
        for member in members:
            completions = db.get_completions_for_member(member.id, week_number)
            all_completions.extend(completions)

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

        # Verdeel taken over de week
        # Avondtaken: elke dag (behalve zondag voor sommige taken)
        # Ochtendtaken: schooldagen (ma-vr)
        # Middag taken: flexibel verdeeld over de week

        task_assignments = []

        for task in tasks:
            weekly_target = task.weekly_target

            if task.time_of_day == "ochtend":
                # Ochtendtaken alleen op schooldagen (ma-vr)
                days = [0, 1, 2, 3, 4][:weekly_target]
            elif task.time_of_day == "avond":
                # Avondtaken elke dag
                days = list(range(min(weekly_target, 7)))
            else:  # middag
                # Middag taken verspreid over de week
                if weekly_target <= 3:
                    days = [1, 3, 5][:weekly_target]  # di, do, za
                else:
                    days = list(range(weekly_target))

            for day_idx in days:
                if day_idx < 7:
                    task_assignments.append({
                        "task": task,
                        "day_idx": day_idx,
                        "day_name": DAY_NAMES[day_idx]
                    })

        # Tel hoeveel taken elk lid al heeft (voor eerlijke verdeling)
        member_task_counts = {m.name: 0 for m in members}
        for c in all_completions:
            if c.member_name in member_task_counts:
                member_task_counts[c.member_name] += 1

        # Wijs taken toe aan leden (round-robin, rekening houdend met huidige verdeling)
        # Sorteer leden op aantal taken (minste eerst)
        def get_next_member():
            sorted_members = sorted(members, key=lambda m: member_task_counts[m.name])
            return sorted_members[0]

        # Groepeer taken per dag en wijs toe
        daily_assignments = {day: [] for day in DAY_NAMES}

        for assignment in task_assignments:
            day_name = assignment["day_name"]
            task = assignment["task"]

            # Check of deze taak al is gedaan vandaag
            day_date = week_start + timedelta(days=assignment["day_idx"])
            already_done = False
            done_by = None

            for c in all_completions:
                if c.task_id == task.id and c.completed_at.date() == day_date:
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
                # Wijs toe aan lid met minste taken
                assigned = get_next_member()
                member_task_counts[assigned.name] += 1
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

        # Genereer ASCII/emoji overzicht
        ascii_overview = self._generate_ascii_schedule(schedule, week_start)

        return {
            "week_number": week_number,
            "week_start": week_start.isoformat(),
            "schedule": schedule,
            "ascii_overview": ascii_overview,
            "member_totals": member_task_counts
        }

    def _generate_ascii_schedule(self, schedule: dict, week_start: date) -> str:
        """Genereer een ASCII/emoji weekoverzicht."""
        lines = []

        # Header
        lines.append("╔════════════════════════════════════════════════════════════════╗")
        lines.append(f"║  📅 WEEKROOSTER week {self.get_current_week()}                                    ║")
        lines.append("╠════════════════════════════════════════════════════════════════╣")

        today = date.today()

        for day_idx, day_name in enumerate(DAY_NAMES):
            day_data = schedule[day_name]
            day_date = week_start + timedelta(days=day_idx)
            emoji = day_data["emoji"]

            # Markeer vandaag
            if day_date == today:
                day_marker = "👉"
            elif day_date < today:
                day_marker = "  "
            else:
                day_marker = "  "

            # Dag header
            date_str = day_date.strftime("%d/%m")
            lines.append(f"║ {day_marker}{emoji} {day_name.upper():<10} ({date_str})                              ║")

            tasks = day_data["tasks"]
            if not tasks:
                lines.append("║      (geen taken)                                             ║")
            else:
                for task in tasks:
                    check = "✅" if task["completed"] else "⬜"
                    name = task["assigned_to"]
                    task_name = task["task_name"][:20]
                    # Pad task line to fit in box
                    line = f"{check} {name}: {task_name}"
                    lines.append(f"║      {line:<56}║")

            if day_idx < 6:
                lines.append("║────────────────────────────────────────────────────────────────║")

        lines.append("╚════════════════════════════════════════════════════════════════╝")

        return "\n".join(lines)


# Singleton instance
engine = TaskEngine()
