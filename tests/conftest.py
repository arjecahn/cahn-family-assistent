"""
Pytest fixtures voor het testen van het taakverdelingsalgoritme.

Deze fixtures maken het mogelijk om het algoritme te testen ZONDER database,
zodat tests snel, deterministisch en reproduceerbaar zijn.
"""
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch
from typing import Optional
from zoneinfo import ZoneInfo

from src.models import Member, Task, Completion, Absence, ScheduleAssignment


# === MOCK DATA ===

@pytest.fixture
def members() -> list[Member]:
    """De drie gezinsleden."""
    return [
        Member(id="1", name="Nora"),
        Member(id="2", name="Linde"),
        Member(id="3", name="Fenna"),
    ]


@pytest.fixture
def tasks() -> list[Task]:
    """Alle taken volgens de 2026 afspraken."""
    return [
        Task(
            id="1",
            name="uitruimen_ochtend",
            display_name="uitruimen voor school",
            description="Afwasmachine uitruimen vóór school",
            weekly_target=3,
            per_child_target=1,
            rotation_weeks=1,
            time_of_day="ochtend"
        ),
        Task(
            id="2",
            name="uitruimen_avond",
            display_name="uitruimen avond",
            description="Afwasmachine uitruimen + pannen + planken",
            weekly_target=7,
            per_child_target=2,
            rotation_weeks=1,
            time_of_day="avond"
        ),
        Task(
            id="3",
            name="inruimen",
            display_name="inruimen",
            description="Afwasmachine inruimen + aanrecht schoon",
            weekly_target=7,
            per_child_target=2,
            rotation_weeks=1,
            time_of_day="avond"
        ),
        Task(
            id="4",
            name="dekken",
            display_name="dekken",
            description="Tafel dekken + afnemen na eten",
            weekly_target=7,
            per_child_target=2,
            rotation_weeks=1,
            time_of_day="avond"
        ),
        Task(
            id="5",
            name="karton_papier",
            display_name="karton en papier wegbrengen",
            description="Karton en oud papier naar container",
            weekly_target=2,
            per_child_target=1,
            rotation_weeks=1,
            time_of_day="middag"
        ),
        Task(
            id="6",
            name="glas",
            display_name="glas wegbrengen",
            description="Glas naar de glasbak",
            weekly_target=1,
            per_child_target=1,
            rotation_weeks=1,
            time_of_day="middag"
        ),
        Task(
            id="7",
            name="koken",
            display_name="koken",
            description="Maaltijd koken voor het gezin",
            weekly_target=1,
            per_child_target=1,
            rotation_weeks=4,
            time_of_day="avond"
        ),
    ]


@pytest.fixture
def tasks_by_name(tasks) -> dict[str, Task]:
    """Lookup dict voor taken op naam."""
    result = {}
    for t in tasks:
        result[t.name] = t
        result[t.display_name] = t
    return result


# === MOCK DATABASE ===

class MockDatabase:
    """
    In-memory mock van de database voor deterministische tests.

    Simuleert alle database operaties zonder echte DB connectie.
    """

    def __init__(self, members: list[Member], tasks: list[Task]):
        self.members = members
        self.tasks = tasks
        self.completions: list[Completion] = []
        self.absences: list[Absence] = []
        self.schedule_assignments: list[ScheduleAssignment] = []
        self._completion_id_counter = 1
        self._assignment_id_counter = 1

        # Configureerbare "huidige datum" voor tests
        self._current_date = date(2026, 1, 19)  # Een maandag
        self._timezone = ZoneInfo("Europe/Amsterdam")

    def set_current_date(self, d: date):
        """Zet de gesimuleerde huidige datum."""
        self._current_date = d

    def advance_days(self, days: int):
        """Ga N dagen vooruit in de tijd."""
        self._current_date += timedelta(days=days)

    def today_local(self) -> date:
        return self._current_date

    def now_local(self) -> datetime:
        return datetime.combine(self._current_date, datetime.min.time().replace(hour=12), tzinfo=self._timezone)

    # Members
    def get_all_members(self) -> list[Member]:
        return self.members.copy()

    def get_member_by_name(self, name: str) -> Optional[Member]:
        for m in self.members:
            if m.name.lower() == name.lower():
                return m
        return None

    # Tasks
    def get_all_tasks(self) -> list[Task]:
        return self.tasks.copy()

    def get_task_by_name(self, name: str) -> Optional[Task]:
        name_lower = name.lower().strip()
        # Special case: "uitruimen" zonder specificatie -> avond
        if name_lower in ("uitruimen", "uitgeruimd"):
            name_lower = "uitruimen_avond"

        for t in self.tasks:
            if t.name.lower() == name_lower or t.display_name.lower() == name_lower:
                return t
        # Fuzzy match
        for t in self.tasks:
            if name_lower in t.display_name.lower():
                return t
        return None

    # Completions
    def get_completions_for_member(self, member_id: str, week_number: int) -> list[Completion]:
        return [c for c in self.completions
                if c.member_id == member_id and c.week_number == week_number]

    def get_completions_for_week(self, week_number: int) -> list[Completion]:
        return [c for c in self.completions if c.week_number == week_number]

    def get_completions_for_month(self, year: int, month: int) -> list[Completion]:
        return [c for c in self.completions
                if c.completed_at.year == year and c.completed_at.month == month]

    def get_last_completion_for_task(self, member_id: str, task_id: str) -> Optional[Completion]:
        matching = [c for c in self.completions
                   if c.member_id == member_id and c.task_id == task_id]
        if not matching:
            return None
        return max(matching, key=lambda c: c.completed_at)

    def add_completion(self, data: dict) -> Completion:
        completed_date = data.get("completed_date", self._current_date)
        if isinstance(completed_date, date) and not isinstance(completed_date, datetime):
            completed_at = datetime.combine(completed_date, datetime.min.time().replace(hour=12), tzinfo=self._timezone)
        else:
            completed_at = self.now_local()

        completion = Completion(
            id=str(self._completion_id_counter),
            task_id=data["task_id"],
            member_id=data["member_id"],
            member_name=data["member_name"],
            task_name=data["task_name"],
            completed_at=completed_at,
            week_number=data["week_number"]
        )
        self._completion_id_counter += 1
        self.completions.append(completion)
        return completion

    def add_completions_bulk(self, completions_data: list[dict]) -> list[Completion]:
        results = []
        for data in completions_data:
            results.append(self.add_completion(data))
        return results

    def delete_completion(self, completion_id: str) -> bool:
        for i, c in enumerate(self.completions):
            if c.id == completion_id:
                self.completions.pop(i)
                return True
        return False

    # Absences
    def get_absence_for_date(self, member_id: str, check_date: date) -> Optional[Absence]:
        for a in self.absences:
            if a.member_id == member_id and a.start_date <= check_date <= a.end_date:
                return a
        return None

    def get_absences_for_week(self, week_start: date, week_end: date) -> list[Absence]:
        return [a for a in self.absences
                if a.start_date <= week_end and a.end_date >= week_start]

    def add_absence(self, data: dict) -> Absence:
        absence = Absence(
            id=str(len(self.absences) + 1),
            member_id=data["member_id"],
            member_name=data["member_name"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            reason=data.get("reason")
        )
        self.absences.append(absence)
        return absence

    # Schedule Assignments
    def schedule_exists_for_week(self, week_number: int, year: int) -> bool:
        return any(a.week_number == week_number and a.year == year
                  for a in self.schedule_assignments)

    def get_schedule_for_week(self, week_number: int, year: int) -> list[ScheduleAssignment]:
        return [a for a in self.schedule_assignments
                if a.week_number == week_number and a.year == year]

    def get_assignments_for_day(self, week_number: int, year: int, day_of_week: int) -> list[ScheduleAssignment]:
        return [a for a in self.schedule_assignments
                if a.week_number == week_number and a.year == year and a.day_of_week == day_of_week]

    def save_schedule_for_week(self, week_number: int, year: int, assignments: list[dict]) -> list[ScheduleAssignment]:
        results = []
        for data in assignments:
            assignment = ScheduleAssignment(
                id=str(self._assignment_id_counter),
                week_number=week_number,
                year=year,
                day_of_week=data["day_of_week"],
                task_id=data["task_id"],
                task_name=data["task_name"],
                member_id=data["member_id"],
                member_name=data["member_name"],
                created_at=self.now_local()
            )
            self._assignment_id_counter += 1
            self.schedule_assignments.append(assignment)
            results.append(assignment)
        return results

    def delete_schedule_for_week(self, week_number: int, year: int) -> int:
        before = len(self.schedule_assignments)
        self.schedule_assignments = [a for a in self.schedule_assignments
                                     if not (a.week_number == week_number and a.year == year)]
        return before - len(self.schedule_assignments)

    def update_assignment(self, assignment_id: str, member_id: str, member_name: str) -> bool:
        for a in self.schedule_assignments:
            if a.id == assignment_id:
                # Create new assignment with updated values (Pydantic models are immutable)
                idx = self.schedule_assignments.index(a)
                self.schedule_assignments[idx] = ScheduleAssignment(
                    id=a.id,
                    week_number=a.week_number,
                    year=a.year,
                    day_of_week=a.day_of_week,
                    task_id=a.task_id,
                    task_name=a.task_name,
                    member_id=member_id,
                    member_name=member_name,
                    created_at=a.created_at
                )
                return True
        return False

    def delete_assignment(self, assignment_id: str) -> bool:
        for i, a in enumerate(self.schedule_assignments):
            if a.id == assignment_id:
                self.schedule_assignments.pop(i)
                return True
        return False

    def delete_assignment_for_task(self, week_number: int, year: int, day_of_week: int, task_id: str) -> bool:
        for i, a in enumerate(self.schedule_assignments):
            if (a.week_number == week_number and a.year == year and
                a.day_of_week == day_of_week and a.task_id == task_id):
                self.schedule_assignments.pop(i)
                return True
        return False

    def add_assignment(self, week_number: int, year: int, day_of_week: int,
                       task_id: str, task_name: str, member_id: str, member_name: str) -> ScheduleAssignment:
        assignment = ScheduleAssignment(
            id=str(self._assignment_id_counter),
            week_number=week_number,
            year=year,
            day_of_week=day_of_week,
            task_id=task_id,
            task_name=task_name,
            member_id=member_id,
            member_name=member_name,
            created_at=self.now_local()
        )
        self._assignment_id_counter += 1
        self.schedule_assignments.append(assignment)
        return assignment

    # Batch query (voor performance in productie, hier gewoon samengesteld)
    def get_week_schedule_data(self, week_number: int, year: int,
                                week_start: date, week_end: date, month: int) -> dict:
        return {
            "members": self.get_all_members(),
            "tasks": self.get_all_tasks(),
            "completions": self.get_completions_for_week(week_number),
            "absences": self.get_absences_for_week(week_start, week_end),
            "schedule_exists": self.schedule_exists_for_week(week_number, year),
            "schedule": self.get_schedule_for_week(week_number, year),
            "month_completions": self.get_completions_for_month(year, month)
        }

    # Missed tasks (stub - niet kritisch voor fairness tests)
    def get_missed_tasks_for_week(self, week_number: int, year: int) -> list:
        return []

    def add_missed_task(self, **kwargs):
        pass


@pytest.fixture
def mock_db(members, tasks) -> MockDatabase:
    """Maak een verse mock database."""
    return MockDatabase(members, tasks)


@pytest.fixture
def patched_engine(mock_db):
    """
    Een TaskEngine met gemockte database calls.

    Dit patcht alle database imports in task_engine zodat
    de MockDatabase wordt gebruikt in plaats van echte DB.
    """
    with patch.multiple(
        'src.task_engine.db',
        get_all_members=mock_db.get_all_members,
        get_member_by_name=mock_db.get_member_by_name,
        get_all_tasks=mock_db.get_all_tasks,
        get_task_by_name=mock_db.get_task_by_name,
        get_completions_for_member=mock_db.get_completions_for_member,
        get_completions_for_week=mock_db.get_completions_for_week,
        get_completions_for_month=mock_db.get_completions_for_month,
        get_last_completion_for_task=mock_db.get_last_completion_for_task,
        add_completion=mock_db.add_completion,
        add_completions_bulk=mock_db.add_completions_bulk,
        delete_completion=mock_db.delete_completion,
        get_absence_for_date=mock_db.get_absence_for_date,
        get_absences_for_week=mock_db.get_absences_for_week,
        add_absence=mock_db.add_absence,
        schedule_exists_for_week=mock_db.schedule_exists_for_week,
        get_schedule_for_week=mock_db.get_schedule_for_week,
        get_assignments_for_day=mock_db.get_assignments_for_day,
        save_schedule_for_week=mock_db.save_schedule_for_week,
        delete_schedule_for_week=mock_db.delete_schedule_for_week,
        update_assignment=mock_db.update_assignment,
        delete_assignment=mock_db.delete_assignment,
        delete_assignment_for_task=mock_db.delete_assignment_for_task,
        add_assignment=mock_db.add_assignment,
        get_week_schedule_data=mock_db.get_week_schedule_data,
        get_missed_tasks_for_week=mock_db.get_missed_tasks_for_week,
        add_missed_task=mock_db.add_missed_task,
    ):
        with patch.multiple(
            'src.task_engine',
            today_local=mock_db.today_local,
            now_local=mock_db.now_local,
        ):
            from src.task_engine import TaskEngine
            engine = TaskEngine()
            # Geef toegang tot mock_db voor test manipulatie
            engine._mock_db = mock_db
            yield engine


# === TEST UTILITIES ===

def count_tasks_per_member(schedule: dict) -> dict[str, int]:
    """Tel hoeveel taken elke persoon heeft in een weekrooster."""
    counts = {}
    for day_data in schedule.values():
        for task in day_data.get("tasks", []):
            assigned = task.get("assigned_to") or task.get("completed_by")
            if assigned:
                counts[assigned] = counts.get(assigned, 0) + 1
    return counts


def count_tasks_per_member_per_type(schedule: dict) -> dict[str, dict[str, int]]:
    """Tel per persoon hoeveel van elke taak."""
    counts = {}
    for day_data in schedule.values():
        for task in day_data.get("tasks", []):
            assigned = task.get("assigned_to") or task.get("completed_by")
            task_name = task.get("task_name")
            if assigned and task_name:
                if assigned not in counts:
                    counts[assigned] = {}
                counts[assigned][task_name] = counts[assigned].get(task_name, 0) + 1
    return counts


def get_tasks_per_day_per_member(schedule: dict) -> dict[str, dict[str, list[str]]]:
    """Geef per dag per persoon welke taken."""
    result = {}
    for day_name, day_data in schedule.items():
        result[day_name] = {}
        for task in day_data.get("tasks", []):
            assigned = task.get("assigned_to")
            task_name = task.get("task_name")
            if assigned and task_name:
                if assigned not in result[day_name]:
                    result[day_name][assigned] = []
                result[day_name][assigned].append(task_name)
    return result


def calculate_fairness_metrics(counts: dict[str, int]) -> dict:
    """
    Bereken fairness metrics voor een verdeling.

    Returns:
        - mean: gemiddelde taken per persoon
        - std: standaarddeviatie
        - max_diff: grootste verschil tussen twee personen
        - fairness_pct: 100 - (max_diff / mean * 100)
    """
    if not counts:
        return {"mean": 0, "std": 0, "max_diff": 0, "fairness_pct": 100}

    values = list(counts.values())
    n = len(values)
    mean = sum(values) / n

    variance = sum((v - mean) ** 2 for v in values) / n
    std = variance ** 0.5

    max_diff = max(values) - min(values)
    fairness_pct = 100 - (max_diff / mean * 100) if mean > 0 else 100

    return {
        "mean": mean,
        "std": std,
        "max_diff": max_diff,
        "fairness_pct": fairness_pct
    }
