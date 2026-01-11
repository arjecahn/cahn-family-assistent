"""Core logica voor eerlijke takenverdeling."""
from datetime import date, datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from .models import Member, Task, Completion, ScheduleAssignment
from . import database as db
from .database import now_local, today_local, TIMEZONE

# Tijdslot mapping voor taken (voor herplanning)
# Taken in hetzelfde tijdslot zijn mutually exclusive per kind per dag
TIME_SLOT_GROUPS = {
    "avond": ["uitruimen_avond", "inruimen", "dekken", "koken"],
    "middag": ["karton_papier", "glas"],
    "ochtend": ["uitruimen_ochtend"]
}

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

        Bevat auto-herplanning: als iemand een andere taak doet dan gepland
        in hetzelfde tijdslot, wordt de originele taak herplant.

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

        # Bepaal week nummer en datum
        if completed_date:
            week_number = completed_date.isocalendar()[1]
            completion_day = completed_date
        else:
            week_number = self.get_current_week()
            completion_day = today_local()

        year = completion_day.isocalendar()[0]
        day_of_week = completion_day.weekday()  # 0=maandag

        # Registreer de completion
        completion = db.add_completion({
            "task_id": task.id,
            "member_id": member.id,
            "member_name": member.name,
            "task_name": task.display_name,
            "week_number": week_number,
            "completed_date": completed_date
        })

        # === AUTO-HERPLANNING ===
        # Check of er een rooster is voor deze week
        if db.schedule_exists_for_week(week_number, year):
            self._handle_rescheduling(member, task, week_number, year, day_of_week)

        return completion

    def _handle_batch_rescheduling(self, day_items: list, week_number: int, year: int,
                                      day_of_week: int, tasks_lookup: dict):
        """Handle herplanning voor een batch van completions op dezelfde dag.

        Dit is beter dan individuele herplanning omdat:
        1. Alle wijzigingen worden eerst geanalyseerd
        2. Swaps worden gedetecteerd (A deed B's taak en B deed A's taak)
        3. Daarna worden assignments in de juiste volgorde geüpdatet

        Args:
            day_items: Lijst van dicts met member, task, etc.
            week_number: ISO weeknummer
            year: Jaar
            day_of_week: 0=maandag, 6=zondag
            tasks_lookup: Dict van task display_name -> Task object
        """
        # Haal assignments voor deze dag op (EENMALIG)
        day_assignments = db.get_assignments_for_day(week_number, year, day_of_week)

        if not day_assignments:
            return

        # Check of dit verleden is
        today = today_local()
        week_start = self.get_week_start(week_number)
        completion_date = week_start + timedelta(days=day_of_week)
        is_past = completion_date < today

        # Bouw lookup: task_name -> assignment
        assignment_by_task = {a.task_name: a for a in day_assignments}
        # Bouw lookup: member_id -> [assignments]
        assignments_by_member = {}
        for a in day_assignments:
            if a.member_id not in assignments_by_member:
                assignments_by_member[a.member_id] = []
            assignments_by_member[a.member_id].append(a)

        # === FASE 1: Analyseer alle completions ===
        # completed_task -> member die het deed
        who_did_what = {}
        # member -> task die ze hadden moeten doen (in hetzelfde tijdslot)
        member_original_task = {}

        for item in day_items:
            member = item["member"]
            task = item["task"]
            who_did_what[task.display_name] = member

            # Vind wat dit lid eigenlijk zou moeten doen (zelfde tijdslot)
            time_slot = task.time_of_day
            member_assignments = assignments_by_member.get(member.id, [])
            for a in member_assignments:
                if a.task_name != task.display_name:
                    orig_task = tasks_lookup.get(a.task_name)
                    if orig_task and orig_task.time_of_day == time_slot:
                        member_original_task[member.id] = a
                        break

        # === FASE 2: Detecteer swaps ===
        # Een swap is wanneer A deed wat B zou doen EN B deed wat A zou doen
        swaps = []  # [(member_A, member_B, task_A, task_B)]
        processed_members = set()

        for item in day_items:
            member = item["member"]
            task = item["task"]

            if member.id in processed_members:
                continue

            # Vind wie deze taak oorspronkelijk zou doen
            original_assignment = assignment_by_task.get(task.display_name)
            if not original_assignment or original_assignment.member_id == member.id:
                continue  # Geen swap nodig

            original_member_id = original_assignment.member_id
            original_member_name = original_assignment.member_name

            # Check of de originele assignee een taak van dit member heeft gedaan
            my_original = member_original_task.get(member.id)
            if my_original:
                other_doer = who_did_what.get(my_original.task_name)
                if other_doer and other_doer.id == original_member_id:
                    # Het is een swap!
                    swaps.append((member, other_doer, task, tasks_lookup.get(my_original.task_name)))
                    processed_members.add(member.id)
                    processed_members.add(original_member_id)

        # === FASE 3: Pas swaps toe ===
        for member_a, member_b, task_a, task_b in swaps:
            # member_a deed task_a (was voor member_b)
            # member_b deed task_b (was voor member_a)
            assignment_a = assignment_by_task.get(task_a.display_name)
            assignment_b = assignment_by_task.get(task_b.display_name) if task_b else None

            if assignment_a:
                db.update_assignment(assignment_a.id, member_a.id, member_a.name)
            if assignment_b:
                db.update_assignment(assignment_b.id, member_b.id, member_b.name)

        # === FASE 4: Verwerk resterende (niet-swap) wijzigingen ===
        for item in day_items:
            member = item["member"]
            task = item["task"]

            if member.id in processed_members:
                continue  # Al verwerkt als swap

            # Normale herplanning voor individuele wijziging
            self._handle_rescheduling(
                member, task, week_number, year, day_of_week,
                tasks_lookup=tasks_lookup
            )

    def _handle_rescheduling(self, member: Member, completed_task: Task,
                               week_number: int, year: int, day_of_week: int,
                               tasks_lookup: Optional[dict] = None):
        """Handle herplanning wanneer iemand een andere taak deed dan gepland.

        Scenario: Nora stond ingepland voor inruimen, maar deed dekken.
        - De dekken assignment wordt geüpdatet naar Nora
        - Voor VANDAAG/TOEKOMST: inruimen wordt herplant naar andere dag/persoon
        - Voor VERLEDEN: inruimen assignment wordt geüpdatet naar wie vrijkwam (swap)
        """
        # Haal de assignments van deze dag op
        day_assignments = db.get_assignments_for_day(week_number, year, day_of_week)

        # Bouw tasks lookup als we die nog niet hebben (performance)
        if not tasks_lookup:
            all_tasks = db.get_all_tasks()
            tasks_lookup = {t.display_name: t for t in all_tasks}

        # Check of dit een verleden datum is
        today = today_local()
        week_start = self.get_week_start(week_number)
        completion_date = week_start + timedelta(days=day_of_week)
        is_past = completion_date < today

        # Vind de assignment voor de completed task
        completed_assignment = None
        for a in day_assignments:
            if a.task_name == completed_task.display_name:
                completed_assignment = a
                break

        # Vind wat dit lid eigenlijk zou moeten doen (zelfde tijdslot)
        time_slot = completed_task.time_of_day
        member_original_assignment = None

        for a in day_assignments:
            if a.member_id == member.id and a.task_name != completed_task.display_name:
                # Check of het in hetzelfde tijdslot zit (via lookup, geen DB query)
                original_task = tasks_lookup.get(a.task_name)
                if original_task and original_task.time_of_day == time_slot:
                    member_original_assignment = a
                    break

        # Update de completed_assignment naar de persoon die het echt deed
        original_assignee = None
        original_assignee_id = None
        if completed_assignment and completed_assignment.member_id != member.id:
            # Onthoud wie de taak oorspronkelijk zou doen
            original_assignee = completed_assignment.member_name
            original_assignee_id = completed_assignment.member_id
            # Update de assignment naar de persoon die het echt deed
            db.update_assignment(completed_assignment.id, member.id, member.name)

        # Handle de originele assignment van dit lid
        if member_original_assignment:
            if is_past:
                # VERLEDEN: Direct swappen - geef de taak aan wie vrijkwam
                if original_assignee and original_assignee_id:
                    # Directe swap: geef member's originele taak aan de vrijgekomen persoon
                    db.update_assignment(member_original_assignment.id,
                                        original_assignee_id, original_assignee)
                # Als niemand vrijkwam, laat de assignment zoals die is
                # (wordt later mogelijk door een andere completion geüpdatet)
            else:
                # VANDAAG/TOEKOMST: Herplan naar andere dag/persoon
                self._reschedule_task(
                    member_original_assignment,
                    week_number,
                    year,
                    day_of_week,
                    preferred_member=original_assignee,
                    tasks_lookup=tasks_lookup
                )

    def _reschedule_task(self, original_assignment: ScheduleAssignment,
                          week_number: int, year: int, current_day: int,
                          preferred_member: Optional[str] = None,
                          tasks_lookup: Optional[dict] = None):
        """Herplan een taak naar een andere dag/persoon.

        BELANGRIJK: Herplanning gebeurt alleen VOORUIT in de tijd.
        Als current_day in het verleden ligt, wordt er vanaf vandaag gepland.

        Prioriteit:
        1. De preferred_member (als die beschikbaar is en tijdslot vrij heeft)
        2. Dezelfde dag, ander beschikbaar kind
        3. Volgende dagen in de week
        """
        # Gebruik lookup dict als beschikbaar, anders ophalen
        if tasks_lookup:
            task = tasks_lookup.get(original_assignment.task_name)
        else:
            task = db.get_task_by_name(original_assignment.task_name)
        if not task:
            return

        week_start = self.get_week_start(week_number)
        week_end = week_start + timedelta(days=6)

        # === FORWARD-ONLY CHECK ===
        # Je kan niet herplannen naar het verleden. Als de completion_day in het
        # verleden ligt, start dan vanaf vandaag.
        today = today_local()
        today_weekday = today.weekday()

        # Check of we in dezelfde week zitten
        if today >= week_start and today <= week_end:
            # We zitten in de huidige week - herplan alleen naar vandaag of later
            earliest_day = max(current_day, today_weekday)
        else:
            # De week is volledig in het verleden - niets te herplannen
            db.delete_assignment(original_assignment.id)
            return

        # Als we op zondag zijn (dag 6), is er geen ruimte meer om te herplannen
        if earliest_day > 6:
            db.delete_assignment(original_assignment.id)
            return

        members = db.get_all_members()
        week_absences = db.get_absences_for_week(week_start, week_end)

        # Bereken beschikbaarheid
        day_availability = self._calculate_day_availability(members, week_start, week_absences)

        # Haal alle bestaande assignments op voor de week (VERS ophalen na update)
        all_assignments = db.get_schedule_for_week(week_number, year)

        # Bouw tasks lookup als we die nog niet hebben
        if not tasks_lookup:
            all_tasks = db.get_all_tasks()
            tasks_lookup = {t.display_name: t for t in all_tasks}

        # Track hoeveel taken per persoon deze week heeft
        member_counts = {m.name: 0 for m in members}
        for a in all_assignments:
            if a.id != original_assignment.id:  # Exclude de te herplannen assignment
                member_counts[a.member_name] = member_counts.get(a.member_name, 0) + 1

        # Track welke tijdslots al bezet zijn per dag per persoon
        member_day_slots = {day_idx: {m.name: set() for m in members} for day_idx in range(7)}
        for a in all_assignments:
            if a.id != original_assignment.id:
                a_task = tasks_lookup.get(a.task_name)
                if a_task:
                    member_day_slots[a.day_of_week][a.member_name].add(a_task.time_of_day)

        time_slot = task.time_of_day
        day_name = DAY_NAMES[earliest_day]
        available_today = day_availability.get(day_name, [])

        # Prioriteit 1: preferred_member (degene wiens taak werd overgenomen)
        if preferred_member:
            for m in available_today:
                if m.name == preferred_member:
                    if time_slot not in member_day_slots[earliest_day].get(m.name, set()):
                        # Preferred member kan het vandaag doen!
                        # Als we naar een andere dag moeten, verwijder en maak nieuwe assignment
                        if earliest_day != original_assignment.day_of_week:
                            db.delete_assignment(original_assignment.id)
                            db.add_assignment(
                                week_number=week_number,
                                year=year,
                                day_of_week=earliest_day,
                                task_id=task.id,
                                task_name=task.display_name,
                                member_id=m.id,
                                member_name=m.name
                            )
                        else:
                            db.update_assignment(original_assignment.id, m.id, m.name)
                        return
                    break  # Preferred member gevonden maar kan niet, ga door met anderen

        # Prioriteit 2: dezelfde dag (earliest_day), ander beschikbaar kind
        for m in sorted(available_today, key=lambda x: member_counts.get(x.name, 0)):
            if m.id == original_assignment.member_id:
                continue  # Skip de originele persoon
            if time_slot not in member_day_slots[earliest_day].get(m.name, set()):
                # Deze persoon kan het doen!
                if earliest_day != original_assignment.day_of_week:
                    db.delete_assignment(original_assignment.id)
                    db.add_assignment(
                        week_number=week_number,
                        year=year,
                        day_of_week=earliest_day,
                        task_id=task.id,
                        task_name=task.display_name,
                        member_id=m.id,
                        member_name=m.name
                    )
                else:
                    db.update_assignment(original_assignment.id, m.id, m.name)
                return

        # Als earliest_day niet lukt, probeer de resterende dagen van de week
        for day_idx in range(earliest_day + 1, 7):
            day_name = DAY_NAMES[day_idx]
            available = day_availability.get(day_name, [])

            for m in sorted(available, key=lambda x: member_counts.get(x.name, 0)):
                if time_slot not in member_day_slots[day_idx].get(m.name, set()):
                    # Deze persoon kan het op deze dag doen!
                    # Verwijder de oude assignment en maak een nieuwe voor de nieuwe dag
                    db.delete_assignment(original_assignment.id)
                    db.add_assignment(
                        week_number=week_number,
                        year=year,
                        day_of_week=day_idx,
                        task_id=task.id,
                        task_name=task.display_name,
                        member_id=m.id,
                        member_name=m.name
                    )
                    return

        # Als we hier komen, kon de taak niet herplant worden binnen de week
        # Verwijder de assignment - de eerlijkheid wordt over weken gebalanceerd
        db.delete_assignment(original_assignment.id)

    def regenerate_schedule(self, week_number: Optional[int] = None) -> dict:
        """Regenereer het rooster voor een week (verwijdert bestaand rooster).

        Gebruik dit alleen in uitzonderlijke gevallen, bijv. na database reset.
        """
        if week_number is None:
            week_number = self.get_current_week()

        year = today_local().year

        # Verwijder bestaand rooster
        db.delete_schedule_for_week(week_number, year)

        # Genereer nieuw rooster via get_week_schedule (die slaat automatisch op)
        return self.get_week_schedule()

    def complete_tasks_bulk(self, tasks_data: list[dict]) -> list[Completion]:
        """Registreer meerdere taken in één transactie.

        Args:
            tasks_data: Lijst van dicts met member_name, task_name, en optioneel completed_date

        Returns:
            Lijst van Completion objecten

        Raises:
            ValueError: Als een member of task niet gevonden wordt (geen enkele taak wordt opgeslagen)
        """
        # Eerst valideren en data voorbereiden
        completions_to_add = []
        validated_items = []  # Voor herplanning na opslaan

        # Bouw tasks lookup voor performance
        all_tasks = db.get_all_tasks()
        tasks_lookup = {t.display_name: t for t in all_tasks}
        tasks_by_name = {t.name: t for t in all_tasks}

        for item in tasks_data:
            member = db.get_member_by_name(item["member_name"])
            if not member:
                raise ValueError(f"Gezinslid '{item['member_name']}' niet gevonden")

            task = db.get_task_by_name(item["task_name"])
            if not task:
                raise ValueError(f"Taak '{item['task_name']}' niet gevonden")

            # Bepaal week nummer en datum
            completed_date = item.get("completed_date")
            if completed_date:
                week_number = completed_date.isocalendar()[1]
                year = completed_date.isocalendar()[0]
            else:
                completed_date = today_local()
                week_number = self.get_current_week()
                year = completed_date.isocalendar()[0]

            completions_to_add.append({
                "task_id": task.id,
                "member_id": member.id,
                "member_name": member.name,
                "task_name": task.display_name,
                "week_number": week_number,
                "completed_date": completed_date
            })

            validated_items.append({
                "member": member,
                "task": task,
                "week_number": week_number,
                "year": year,
                "day_of_week": completed_date.weekday()
            })

        # Alles gevalideerd - nu opslaan in één transactie
        completions = db.add_completions_bulk(completions_to_add)

        # === BATCH HERPLANNING ===
        # Groepeer items per (week, year, day) zodat alle wijzigingen voor dezelfde dag
        # in één keer worden verwerkt - dit voorkomt dat wijziging 1 invloed heeft op wijziging 2
        items_by_day = {}
        for item in validated_items:
            key = (item["week_number"], item["year"], item["day_of_week"])
            if key not in items_by_day:
                items_by_day[key] = []
            items_by_day[key].append(item)

        # Verwerk elke dag als batch
        for (week_number, year, day_of_week), day_items in items_by_day.items():
            if db.schedule_exists_for_week(week_number, year):
                self._handle_batch_rescheduling(
                    day_items, week_number, year, day_of_week, tasks_lookup
                )

        return completions

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

    def undo_task_completion(self, member_name: str, task_name: str,
                              completed_date: Optional[date] = None) -> dict:
        """Maak een specifieke taak voltooiing ongedaan.

        Dit is beter dan "undo last" omdat het specifiek is en geen conflicten
        geeft bij meerdere ChatGPT sessies.

        Args:
            member_name: Wie de taak had gedaan
            task_name: Welke taak
            completed_date: Wanneer (default: vandaag)

        Returns:
            dict met success, message, en info over herplanning
        """
        member = db.get_member_by_name(member_name)
        if not member:
            raise ValueError(f"Gezinslid '{member_name}' niet gevonden")

        task = db.get_task_by_name(task_name)
        if not task:
            raise ValueError(f"Taak '{task_name}' niet gevonden")

        if completed_date is None:
            completed_date = today_local()

        week_number = completed_date.isocalendar()[1]
        year = completed_date.isocalendar()[0]
        day_of_week = completed_date.weekday()

        # Zoek de completion
        completions = db.get_completions_for_week(week_number)
        target_completion = None
        for c in completions:
            if (c.member_name == member.name and
                c.task_name == task.display_name and
                c.completed_at.date() == completed_date):
                target_completion = c
                break

        if not target_completion:
            return {
                "success": False,
                "message": f"{member_name} heeft {task_name} niet gedaan op {completed_date}",
                "rescheduled": False
            }

        # Verwijder de completion
        db.delete_completion(target_completion.id)

        # === HERPLANNING ===
        # De taak moet weer op het rooster komen
        rescheduled_to = None

        if db.schedule_exists_for_week(week_number, year):
            # Check of deze taak in het rooster staat
            assignments = db.get_assignments_for_day(week_number, year, day_of_week)
            task_assignment = None
            for a in assignments:
                if a.task_name == task.display_name:
                    task_assignment = a
                    break

            if task_assignment:
                # De assignment bestaat nog, check of die moet worden hersteld
                if task_assignment.member_id == member.id:
                    # De assignment was op deze persoon, maar nu is de taak niet meer done
                    # We hoeven niets te herplannen, de taak komt gewoon terug
                    rescheduled_to = member.name
                else:
                    # De assignment was al op iemand anders (door eerdere herplanning)
                    # Laat het zo - die persoon kan het alsnog doen
                    rescheduled_to = task_assignment.member_name
            else:
                # De assignment was verwijderd (kon niet herplant worden)
                # Probeer de taak nu te herplannen naar iemand
                new_member = self._find_member_for_task(task, week_number, year, day_of_week)
                if new_member:
                    db.add_assignment(
                        week_number=week_number,
                        year=year,
                        day_of_week=day_of_week,
                        task_id=task.id,
                        task_name=task.display_name,
                        member_id=new_member.id,
                        member_name=new_member.name
                    )
                    rescheduled_to = new_member.name

        return {
            "success": True,
            "message": f"Ongedaan gemaakt: {task_name} van {member_name} op {completed_date}",
            "undone_task": task.display_name,
            "rescheduled": rescheduled_to is not None,
            "rescheduled_to": rescheduled_to
        }

    def _find_member_for_task(self, task: Task, week_number: int, year: int,
                                day_of_week: int, tasks_lookup: Optional[dict] = None) -> Optional[Member]:
        """Vind een geschikt lid om een taak te doen op een specifieke dag."""
        week_start = self.get_week_start(week_number)
        week_end = week_start + timedelta(days=6)

        members = db.get_all_members()
        week_absences = db.get_absences_for_week(week_start, week_end)
        day_availability = self._calculate_day_availability(members, week_start, week_absences)

        day_name = DAY_NAMES[day_of_week]
        available = day_availability.get(day_name, [])

        if not available:
            return None

        # Bouw tasks lookup als we die nog niet hebben (performance)
        if not tasks_lookup:
            all_tasks = db.get_all_tasks()
            tasks_lookup = {t.display_name: t for t in all_tasks}

        # Tel taken per persoon
        all_assignments = db.get_schedule_for_week(week_number, year)
        member_counts = {m.name: 0 for m in members}
        member_day_slots = {m.name: set() for m in members}

        for a in all_assignments:
            member_counts[a.member_name] = member_counts.get(a.member_name, 0) + 1
            if a.day_of_week == day_of_week:
                a_task = tasks_lookup.get(a.task_name)
                if a_task:
                    member_day_slots[a.member_name].add(a_task.time_of_day)

        time_slot = task.time_of_day

        # Vind lid met minste taken en beschikbare tijdslot
        for m in sorted(available, key=lambda x: member_counts.get(x.name, 0)):
            if time_slot not in member_day_slots.get(m.name, set()):
                return m

        return None

    def get_week_schedule(self) -> dict:
        """
        Haal het weekrooster op of genereer een nieuw rooster.

        Het rooster wordt persistent opgeslagen in de database:
        - Eerste keer in een week: rooster genereren en opslaan
        - Daarna: opgeslagen rooster teruggeven
        - Completions worden gecombineerd om status te tonen

        Returns een dict met:
        - schedule: per dag een lijst van taken met toegewezen persoon
        - completed: welke taken al zijn gedaan
        - ascii_overview: ASCII/emoji overzicht
        """
        today = today_local()
        week_number = self.get_current_week()
        year = today.year
        month = today.month
        week_start = self.get_week_start(week_number)
        week_end = week_start + timedelta(days=6)

        # === SINGLE BATCH QUERY - alle data in 1 connectie ===
        batch_data = db.get_week_schedule_data(week_number, year, week_start, week_end, month)

        members = batch_data["members"]
        tasks = batch_data["tasks"]
        all_completions = batch_data["completions"]
        week_absences = batch_data["absences"]
        month_completions = batch_data["month_completions"]

        # Maak lookup dict voor snelle task lookup (voorkomt N+1 queries)
        tasks_lookup = {t.display_name: t for t in tasks}

        # Bepaal per dag wie beschikbaar is
        day_availability = self._calculate_day_availability(members, week_start, week_absences)

        # Check of er al een rooster bestaat voor deze week
        if batch_data["schedule_exists"]:
            # Laad opgeslagen rooster (al opgehaald in batch)
            stored_assignments = batch_data["schedule"]
            schedule = self._build_schedule_from_stored(
                stored_assignments, all_completions, week_start, day_availability, tasks_lookup
            )
        else:
            # Genereer nieuw rooster en sla op
            schedule, assignments_to_save = self._generate_new_schedule(
                members, tasks, all_completions, day_availability, week_start
            )
            # Sla op in database
            db.save_schedule_for_week(week_number, year, assignments_to_save)

        # Tel taken per persoon (gebaseerd op assignments + completions)
        member_week_counts = self._count_member_tasks(schedule, members)

        # Genereer ASCII/emoji overzicht (met month_completions voor stats)
        ascii_overview = self._generate_ascii_schedule(
            schedule, week_start, day_availability, member_week_counts,
            members=members, tasks=tasks, month_completions=month_completions
        )

        return {
            "week_number": week_number,
            "week_start": week_start.isoformat(),
            "schedule": schedule,
            "ascii_overview": ascii_overview,
            "member_totals": member_week_counts,
            "day_availability": {day: [m.name for m in members] for day, members in day_availability.items()}
        }

    def _calculate_day_availability(self, members: list, week_start: date, week_absences: list) -> dict:
        """Bereken per dag wie beschikbaar is."""
        day_availability = {}
        for day_idx in range(7):
            day_date = week_start + timedelta(days=day_idx)
            day_name = DAY_NAMES[day_idx]
            available = []
            for m in members:
                is_absent = any(
                    a.member_id == m.id and a.start_date <= day_date <= a.end_date
                    for a in week_absences
                )
                if not is_absent:
                    available.append(m)
            day_availability[day_name] = available
        return day_availability

    def _build_schedule_from_stored(self, stored_assignments: list, completions: list,
                                      week_start: date, day_availability: dict,
                                      tasks_lookup: dict) -> dict:
        """Bouw het schedule-object op basis van opgeslagen assignments.

        Args:
            tasks_lookup: Dict van task display_name -> Task object voor snelle lookup
        """
        schedule = {}
        for day_idx in range(7):
            day_name = DAY_NAMES[day_idx]
            schedule[day_name] = {
                "date": (week_start + timedelta(days=day_idx)).isoformat(),
                "emoji": DAY_EMOJIS[day_idx],
                "tasks": []
            }

        # Track welke completions al zijn gematcht met een assignment
        matched_completions = set()

        # Groepeer assignments per dag
        for assignment in stored_assignments:
            day_idx = assignment.day_of_week
            day_name = DAY_NAMES[day_idx]
            day_date = week_start + timedelta(days=day_idx)

            # Check of deze taak al is gedaan (door wie dan ook)
            completed = False
            done_by = assignment.member_name  # Default: wie was ingepland
            for c in completions:
                if c.task_name == assignment.task_name and c.completed_at.date() == day_date:
                    completed = True
                    done_by = c.member_name
                    matched_completions.add(c.id)
                    break

            # Haal time_of_day op uit de lookup (geen database query!)
            task = tasks_lookup.get(assignment.task_name)
            time_of_day = task.time_of_day if task else "avond"

            schedule[day_name]["tasks"].append({
                "task_name": assignment.task_name,
                "assigned_to": assignment.member_name,
                "completed": completed,
                "completed_by": done_by if completed else None,
                "time_of_day": time_of_day,
                "extra": False
            })

        # Voeg "extra" completions toe die niet in het rooster stonden
        for c in completions:
            if c.id in matched_completions:
                continue  # Al gematcht met een assignment

            day_date = c.completed_at.date()
            day_idx = (day_date - week_start).days
            if day_idx < 0 or day_idx > 6:
                continue  # Buiten deze week

            day_name = DAY_NAMES[day_idx]
            task = tasks_lookup.get(c.task_name)
            time_of_day = task.time_of_day if task else "avond"

            schedule[day_name]["tasks"].append({
                "task_name": c.task_name,
                "assigned_to": None,  # Was niet gepland
                "completed": True,
                "completed_by": c.member_name,
                "time_of_day": time_of_day,
                "extra": True  # Markeer als extra/bonus taak
            })

        # Sorteer taken per dag op time_of_day
        time_order = {"ochtend": 0, "middag": 1, "avond": 2}
        for day_name in schedule:
            schedule[day_name]["tasks"].sort(key=lambda t: time_order.get(t.get("time_of_day", "avond"), 1))

        return schedule

    def _generate_new_schedule(self, members: list, tasks: list, completions: list,
                                 day_availability: dict, week_start: date) -> tuple:
        """Genereer een nieuw weekrooster en geef ook de assignments terug voor opslag."""
        schedule = {}
        assignments_to_save = []

        for day_idx in range(7):
            day_name = DAY_NAMES[day_idx]
            schedule[day_name] = {
                "date": (week_start + timedelta(days=day_idx)).isoformat(),
                "emoji": DAY_EMOJIS[day_idx],
                "tasks": []
            }

        # Track hoeveel taken per persoon en per tijdslot per dag
        member_week_counts = {m.name: 0 for m in members}
        # Track welke tijdslots al bezet zijn per dag per persoon
        # Format: {day_idx: {member_name: set(time_slots)}}
        member_day_slots = {day_idx: {m.name: set() for m in members} for day_idx in range(7)}

        # Bepaal voor elke taak op welke dagen deze moet worden gedaan
        task_days = self._distribute_tasks_over_week(tasks, day_availability)

        for day_idx in range(7):
            day_name = DAY_NAMES[day_idx]
            day_date = week_start + timedelta(days=day_idx)
            available_members = day_availability[day_name]

            if not available_members:
                continue

            today_tasks = [t for t in tasks if day_idx in task_days.get(t.name, [])]

            for task in today_tasks:
                # Check of al gedaan vandaag
                already_done = False
                done_by = None
                for c in completions:
                    if c.task_name == task.display_name and c.completed_at.date() == day_date:
                        already_done = True
                        done_by = c.member_name
                        break

                if already_done:
                    schedule[day_name]["tasks"].append({
                        "task_name": task.display_name,
                        "assigned_to": done_by,
                        "completed": True,
                        "time_of_day": task.time_of_day
                    })
                    # Ook opslaan in assignments (als record)
                    member = next((m for m in members if m.name == done_by), None)
                    if member:
                        assignments_to_save.append({
                            "day_of_week": day_idx,
                            "task_id": task.id,
                            "task_name": task.display_name,
                            "member_id": member.id,
                            "member_name": member.name
                        })
                else:
                    # Kies beschikbare persoon met minste taken EN beschikbare tijdslot
                    assigned = self._select_member_for_task(
                        task, available_members, member_week_counts, member_day_slots[day_idx]
                    )

                    if assigned:
                        member_week_counts[assigned.name] += 1
                        member_day_slots[day_idx][assigned.name].add(task.time_of_day)

                        schedule[day_name]["tasks"].append({
                            "task_name": task.display_name,
                            "assigned_to": assigned.name,
                            "completed": False,
                            "time_of_day": task.time_of_day
                        })
                        assignments_to_save.append({
                            "day_of_week": day_idx,
                            "task_id": task.id,
                            "task_name": task.display_name,
                            "member_id": assigned.id,
                            "member_name": assigned.name
                        })

        # Sorteer taken per dag op time_of_day
        time_order = {"ochtend": 0, "middag": 1, "avond": 2}
        for day_name in schedule:
            schedule[day_name]["tasks"].sort(key=lambda t: time_order.get(t.get("time_of_day", "avond"), 1))

        return schedule, assignments_to_save

    def _select_member_for_task(self, task: Task, available_members: list,
                                   member_week_counts: dict, member_day_slots: dict) -> Optional[Member]:
        """Selecteer het beste lid voor een taak, rekening houdend met tijdslots."""
        time_slot = task.time_of_day

        # Filter op wie dit tijdslot nog vrij heeft vandaag
        eligible = [
            m for m in available_members
            if time_slot not in member_day_slots.get(m.name, set())
        ]

        if not eligible:
            # Als niemand dit tijdslot vrij heeft, kies dan gewoon degene met minste taken
            eligible = available_members

        if not eligible:
            return None

        # Sorteer op aantal taken (minste eerst)
        sorted_eligible = sorted(eligible, key=lambda m: member_week_counts.get(m.name, 0))
        return sorted_eligible[0]

    def _count_member_tasks(self, schedule: dict, members: list) -> dict:
        """Tel hoeveel taken per lid in het rooster staan."""
        counts = {m.name: 0 for m in members}
        for day_data in schedule.values():
            for task_info in day_data.get("tasks", []):
                name = task_info.get("assigned_to")
                if name in counts:
                    counts[name] += 1
        return counts

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
                                   members: list = None, tasks: list = None,
                                   month_completions: list = None) -> str:
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
                    # Bepaal icoon: ✅ gedaan, 🌟 extra (niet gepland), ⬜ nog te doen
                    if day_task.get("extra"):
                        check = "🌟"  # Extra taak (niet gepland maar wel gedaan)
                    elif day_task["completed"]:
                        check = "✅"
                    else:
                        check = "⬜"
                    # Toon wie het DEED (completed_by) als af, anders wie GEPLAND staat
                    name = (day_task.get("completed_by") or day_task.get("assigned_to") or "?")[:6]
                    task_display = day_task["task_name"][:25]  # Max 25 chars voor taak
                    line = f"{check} {name}: {task_display}"
                    lines.append(f"║    {line:<46}║")

            if day_idx < 6:
                lines.append("║───────────────────────────────────────────────────║")

        # Maandoverzicht per taak per persoon
        lines.append("╠═══════════════════════════════════════════════════╣")
        month_stats = self._get_monthly_task_stats(members=all_members, tasks=tasks, completions=month_completions)
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

    def _get_monthly_task_stats(self, members: list = None, tasks: list = None,
                                   completions: list = None) -> dict:
        """Bereken per taak hoeveel elke persoon heeft gedaan deze maand."""
        import calendar

        today = today_local()
        year = today.year
        month = today.month

        # Hoeveel weken zitten er in deze maand (voor targets)
        _, days_in_month = calendar.monthrange(year, month)
        weeks_in_month = days_in_month / 7

        # Gebruik meegegeven completions of haal op (fallback)
        if completions is None:
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
