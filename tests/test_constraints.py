"""
Tests voor harde constraints die NOOIT overtreden mogen worden.

Deze regels zijn afgesproken en moeten altijd gelden:
1. Uitruimen ochtend: alleen doordeweeks (ma-vr), niet in weekend
2. Karton/papier: minimaal 2 dagen tussen herhalingen
3. Glas: minimaal 5 dagen tussen herhalingen
4. Max 1 taak per tijdslot per persoon per dag
5. Max 5 taken per dag totaal (anders te druk)
"""
import pytest
from datetime import date, timedelta

from tests.conftest import get_tasks_per_day_per_member


class TestWeekdayOnlyConstraint:
    """Uitruimen ochtend mag alleen doordeweeks (ma-vr)."""

    def test_uitruimen_ochtend_not_on_saturday(self, patched_engine):
        """Uitruimen ochtend niet op zaterdag."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        saturday_tasks = schedule["zaterdag"]["tasks"]
        morning_tasks = [t for t in saturday_tasks
                        if "ochtend" in t.get("task_name", "").lower()
                        or "school" in t.get("task_name", "").lower()]

        assert len(morning_tasks) == 0, \
            f"Uitruimen ochtend op zaterdag gevonden: {morning_tasks}"

    def test_uitruimen_ochtend_not_on_sunday(self, patched_engine):
        """Uitruimen ochtend niet op zondag."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        sunday_tasks = schedule["zondag"]["tasks"]
        morning_tasks = [t for t in sunday_tasks
                        if "ochtend" in t.get("task_name", "").lower()
                        or "school" in t.get("task_name", "").lower()]

        assert len(morning_tasks) == 0, \
            f"Uitruimen ochtend op zondag gevonden: {morning_tasks}"

    def test_uitruimen_ochtend_on_weekdays(self, patched_engine):
        """Uitruimen ochtend moet wel op doordeweekse dagen voorkomen."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        weekdays = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag"]

        morning_count = 0
        for day in weekdays:
            for task in schedule[day]["tasks"]:
                if "ochtend" in task.get("task_name", "").lower() or \
                   "school" in task.get("task_name", "").lower():
                    morning_count += 1

        # Weekly target is 3, dus we verwachten 3 ochtendtaken op doordeweekse dagen
        assert morning_count == 3, \
            f"Verwacht 3 uitruimen ochtend taken, gevonden: {morning_count}"


class TestSpacingConstraints:
    """Test spacing rules voor karton en glas."""

    def test_karton_minimum_2_days_apart(self, patched_engine):
        """Karton/papier moet minimaal 2 dagen ertussen hebben."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        day_order = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]

        karton_days = []
        for i, day_name in enumerate(day_order):
            for task in schedule[day_name]["tasks"]:
                if "karton" in task.get("task_name", "").lower():
                    karton_days.append(i)

        # Check spacing tussen karton dagen
        for i in range(len(karton_days) - 1):
            spacing = karton_days[i + 1] - karton_days[i]
            assert spacing >= 2, \
                f"Karton op dag {karton_days[i]} en {karton_days[i+1]}, maar spacing moet >= 2 zijn (is {spacing})"

    def test_glas_minimum_5_days_apart(self, patched_engine):
        """Glas moet minimaal 5 dagen ertussen hebben (binnen 1 week is dat effectief max 1x)."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        day_order = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]

        glas_days = []
        for i, day_name in enumerate(day_order):
            for task in schedule[day_name]["tasks"]:
                if "glas" in task.get("task_name", "").lower():
                    glas_days.append(i)

        # Bij spacing van 5 dagen kan glas maar 1x per week
        assert len(glas_days) <= 1, \
            f"Glas meerdere keren per week: dagen {glas_days}"

    def test_karton_weekly_target_respected(self, patched_engine):
        """Karton moet 2x per week voorkomen (weekly_target)."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        karton_count = 0
        for day_data in schedule.values():
            for task in day_data["tasks"]:
                if "karton" in task.get("task_name", "").lower():
                    karton_count += 1

        assert karton_count == 2, \
            f"Karton weekly_target is 2, maar gevonden: {karton_count}"


class TestTimeslotConstraints:
    """Test tijdslot constraints - max 1 taak per slot per persoon."""

    def test_max_one_task_per_timeslot_per_person(self, patched_engine):
        """Niemand krijgt 2 taken in hetzelfde tijdslot op dezelfde dag."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Mapping van taken naar tijdslots
        task_timeslot = {
            "uitruimen voor school": "ochtend",
            "uitruimen avond": "avond",
            "inruimen": "avond",
            "dekken": "avond",
            "koken": "avond",
            "karton en papier wegbrengen": "middag",
            "glas wegbrengen": "middag",
        }

        for day_name, day_data in schedule.items():
            # Per persoon per tijdslot: welke taken
            person_slots = {}

            for task in day_data["tasks"]:
                assigned = task.get("assigned_to")
                task_name = task.get("task_name")
                if not assigned or not task_name:
                    continue

                slot = task_timeslot.get(task_name, "onbekend")

                key = (assigned, slot)
                if key not in person_slots:
                    person_slots[key] = []
                person_slots[key].append(task_name)

            # Check geen dubbele
            for (person, slot), tasks_list in person_slots.items():
                assert len(tasks_list) <= 1, \
                    f"{person} heeft {len(tasks_list)} taken in {slot} slot op {day_name}: {tasks_list}"


class TestMaxTasksPerDay:
    """Test maximum taken per dag limiet."""

    def test_max_5_tasks_per_day(self, patched_engine):
        """Niet meer dan 5 taken per dag (anders te druk)."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        for day_name, day_data in schedule.items():
            task_count = len(day_data["tasks"])
            assert task_count <= 5, \
                f"{day_name} heeft {task_count} taken, max is 5"


class TestKokenConstraints:
    """Test koken specifieke regels."""

    def test_koken_max_once_per_week(self, patched_engine):
        """Koken mag max 1x per week voorkomen (weekly_target=1)."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        koken_count = 0
        for day_data in schedule.values():
            for task in day_data["tasks"]:
                if task.get("task_name") == "koken":
                    koken_count += 1

        assert koken_count <= 1, \
            f"Koken komt {koken_count}x voor, max is 1x per week"

    def test_koken_blocks_other_evening_tasks(self, patched_engine):
        """Wie kookt doet geen andere avondtaak die dag."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        for day_name, day_data in schedule.items():
            koken_person = None
            for task in day_data["tasks"]:
                if task.get("task_name") == "koken":
                    koken_person = task.get("assigned_to")
                    break

            if koken_person:
                # Check dat deze persoon geen andere avondtaken heeft
                evening_tasks = ["uitruimen avond", "inruimen", "dekken"]
                for task in day_data["tasks"]:
                    if task.get("assigned_to") == koken_person and \
                       task.get("task_name") in evening_tasks:
                        pytest.fail(
                            f"{koken_person} kookt op {day_name} maar heeft ook "
                            f"{task.get('task_name')}"
                        )


class TestConstraintCombinations:
    """Test combinaties van constraints."""

    def test_all_constraints_together(self, patched_engine, members):
        """Alle constraints samen in één check."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        violations = []

        task_timeslot = {
            "uitruimen voor school": "ochtend",
            "uitruimen avond": "avond",
            "inruimen": "avond",
            "dekken": "avond",
            "koken": "avond",
            "karton en papier wegbrengen": "middag",
            "glas wegbrengen": "middag",
        }

        day_order = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
        karton_days = []
        glas_days = []

        for day_idx, day_name in enumerate(day_order):
            day_data = schedule[day_name]

            # 1. Check max taken per dag
            if len(day_data["tasks"]) > 5:
                violations.append(f"{day_name}: teveel taken ({len(day_data['tasks'])})")

            # Tijdslot tracking per persoon
            person_slots = {m.name: set() for m in members}

            for task in day_data["tasks"]:
                task_name = task.get("task_name", "")
                assigned = task.get("assigned_to")

                # 2. Check weekday-only
                if ("ochtend" in task_name.lower() or "school" in task_name.lower()) and day_idx >= 5:
                    violations.append(f"{day_name}: uitruimen ochtend in weekend")

                # 3. Track spacing
                if "karton" in task_name.lower():
                    karton_days.append(day_idx)
                if "glas" in task_name.lower():
                    glas_days.append(day_idx)

                # 4. Check tijdslot conflict
                if assigned:
                    slot = task_timeslot.get(task_name, "onbekend")
                    if slot in person_slots.get(assigned, set()):
                        violations.append(f"{day_name}: {assigned} heeft dubbel {slot} slot")
                    person_slots[assigned].add(slot)

        # 5. Check karton spacing
        for i in range(len(karton_days) - 1):
            if karton_days[i + 1] - karton_days[i] < 2:
                violations.append(f"Karton spacing < 2: dagen {karton_days}")

        # 6. Check glas max 1x
        if len(glas_days) > 1:
            violations.append(f"Glas > 1x per week: dagen {glas_days}")

        assert len(violations) == 0, \
            f"Constraint violations gevonden:\n" + "\n".join(f"  - {v}" for v in violations)


class TestConstraintsWithAbsence:
    """Test dat constraints ook gelden bij afwezigheid."""

    def test_constraints_respected_with_one_absent(self, patched_engine, members):
        """Constraints gelden ook als 1 persoon afwezig is."""
        engine = patched_engine
        mock_db = engine._mock_db

        today = mock_db.today_local()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        # Fenna hele week afwezig
        mock_db.add_absence({
            "member_id": members[2].id,
            "member_name": members[2].name,
            "start_date": week_start,
            "end_date": week_end,
            "reason": "Vakantie"
        })

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Check uitruimen ochtend niet in weekend
        for day in ["zaterdag", "zondag"]:
            for task in schedule[day]["tasks"]:
                assert "ochtend" not in task.get("task_name", "").lower(), \
                    f"Uitruimen ochtend op {day} ondanks afwezigheid"

    def test_constraints_respected_with_two_absent(self, patched_engine, members):
        """Constraints gelden ook als 2 personen afwezig zijn."""
        engine = patched_engine
        mock_db = engine._mock_db

        today = mock_db.today_local()
        week_start = today - timedelta(days=today.weekday())

        # Linde en Fenna dinsdag en woensdag afwezig
        for member in members[1:]:
            mock_db.add_absence({
                "member_id": member.id,
                "member_name": member.name,
                "start_date": week_start + timedelta(days=1),  # dinsdag
                "end_date": week_start + timedelta(days=2),    # woensdag
                "reason": "Schoolreis"
            })

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Nora is enige beschikbare - check dat zij max 1 avondtaak per dag krijgt
        for day in ["dinsdag", "woensdag"]:
            nora_tasks = [t for t in schedule[day]["tasks"]
                        if t.get("assigned_to") == "Nora"]
            evening_tasks = [t for t in nora_tasks
                           if t.get("task_name") in ["uitruimen avond", "inruimen", "dekken", "koken"]]

            assert len(evening_tasks) <= 1, \
                f"Nora heeft {len(evening_tasks)} avondtaken op {day}: {evening_tasks}"
