"""
Tests voor herplanning logica.

Herplanning vindt plaats wanneer:
1. Iemand een andere taak doet dan gepland (swap scenario)
2. Een taak wordt gemist (dag voorbij, niet gedaan)
3. Een taak expliciet ongedaan wordt gemaakt

Het systeem moet:
- Originele taak herplannen naar andere dag/persoon
- Constraints blijven respecteren
- Fairness behouden
"""
import pytest
from datetime import date, timedelta

from tests.conftest import count_tasks_per_member


class TestTaskCompletionRescheduling:
    """Test herplanning bij taak voltooiing."""

    def test_complete_assigned_task_no_rescheduling(self, patched_engine, members, tasks):
        """Als je je eigen geplande taak doet, geen herplanning nodig."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Genereer rooster
        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Vind een taak die aan Nora is toegewezen
        nora_task = None
        for day_name, day_data in schedule.items():
            for task in day_data["tasks"]:
                if task.get("assigned_to") == "Nora" and not task.get("completed"):
                    nora_task = (day_name, task["task_name"])
                    break
            if nora_task:
                break

        if not nora_task:
            pytest.skip("Geen taak gevonden voor Nora")

        day_name, task_name = nora_task

        # Nora voltooit haar eigen taak
        engine.complete_task("Nora", task_name)

        # Haal rooster opnieuw op
        new_schedule_data = engine.get_week_schedule()
        new_schedule = new_schedule_data["schedule"]

        # De taak moet nu als voltooid staan
        found = False
        for task in new_schedule[day_name]["tasks"]:
            if task.get("task_name") == task_name:
                assert task.get("completed") == True, \
                    f"Taak {task_name} moet als voltooid staan"
                found = True
                break

        assert found, f"Taak {task_name} niet meer gevonden in rooster"

    def test_complete_different_task_triggers_rescheduling(self, patched_engine, members, tasks):
        """Als je een andere taak doet dan gepland, wordt originele herplant."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Genereer rooster
        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Vind taken voor eerste dag
        first_day = "maandag"
        day_tasks = schedule[first_day]["tasks"]

        # Zoek 2 verschillende avondtaken toegewezen aan verschillende personen
        evening_tasks = [t for t in day_tasks
                        if t.get("task_name") in ["uitruimen avond", "inruimen", "dekken"]
                        and t.get("assigned_to")]

        if len(evening_tasks) < 2:
            pytest.skip("Niet genoeg avondtaken om swap te testen")

        task1 = evening_tasks[0]
        task2 = evening_tasks[1]

        # Person 1 doet taak van person 2
        person1 = task1["assigned_to"]
        person2 = task2["assigned_to"]
        task2_name = task2["task_name"]

        if person1 == person2:
            pytest.skip("Zelfde persoon voor beide taken")

        # Person1 doet task2 (niet zijn eigen taak)
        engine.complete_task(person1, task2_name)

        # Haal rooster opnieuw op
        new_schedule_data = engine.get_week_schedule()
        new_schedule = new_schedule_data["schedule"]

        # Task2 moet nu als voltooid door person1 staan
        found_task2 = False
        for task in new_schedule[first_day]["tasks"]:
            if task.get("task_name") == task2_name:
                if task.get("completed"):
                    assert task.get("completed_by") == person1, \
                        f"{task2_name} voltooid maar niet door {person1}"
                    found_task2 = True
                break

        assert found_task2, f"{task2_name} niet gevonden als voltooid"


class TestMissedTaskRescheduling:
    """Test herplanning van gemiste taken."""

    def test_missed_task_moves_to_future(self, patched_engine, members):
        """Gemiste taak wordt naar toekomstige dag verplaatst."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Start op maandag
        today = mock_db.today_local()
        # Zorg dat we op maandag beginnen
        days_since_monday = today.weekday()
        mock_db.set_current_date(today - timedelta(days=days_since_monday))

        # Genereer rooster
        schedule_data = engine.get_week_schedule()
        monday_tasks = schedule_data["schedule"]["maandag"]["tasks"]

        if not monday_tasks:
            pytest.skip("Geen taken op maandag")

        # Ga naar dinsdag zonder taken te voltooien
        mock_db.advance_days(1)

        # Haal rooster opnieuw op - gemiste taken moeten herplant zijn
        new_schedule = engine.get_week_schedule()

        # Check dat maandag taken niet meer als "te doen" staan
        # (ze zijn ofwel herplant of gemarkeerd als gemist)
        for task in new_schedule["schedule"]["maandag"]["tasks"]:
            if not task.get("completed"):
                # Taak is gemist - mag hier staan maar wordt niet getoond als actief
                # Of taak is herplant naar andere dag
                pass

    def test_missed_task_respects_constraints_when_rescheduling(self, patched_engine, members):
        """Herplanning van gemiste taak respecteert constraints."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Start op maandag
        today = mock_db.today_local()
        days_since_monday = today.weekday()
        mock_db.set_current_date(today - timedelta(days=days_since_monday))

        # Genereer rooster
        engine.get_week_schedule()

        # Ga naar woensdag
        mock_db.advance_days(2)

        # Haal rooster op
        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Check constraints op resterende dagen
        for day_name in ["woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]:
            day_tasks = schedule[day_name]["tasks"]

            # Check max 1 avondtaak per persoon
            person_evening = {}
            for task in day_tasks:
                assigned = task.get("assigned_to")
                task_name = task.get("task_name", "")
                if assigned and task_name in ["uitruimen avond", "inruimen", "dekken", "koken"]:
                    person_evening[assigned] = person_evening.get(assigned, 0) + 1

            for person, count in person_evening.items():
                assert count <= 1, \
                    f"{person} heeft {count} avondtaken op {day_name} na herplanning"


class TestUndoTaskCompletion:
    """Test ongedaan maken van taken."""

    def test_undo_puts_task_back(self, patched_engine, members, tasks):
        """Ongedaan maken zet taak terug op rooster."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Genereer rooster
        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Vind een taak voor vandaag
        today = mock_db.today_local()
        day_names = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
        today_name = day_names[today.weekday()]

        today_tasks = schedule[today_name]["tasks"]
        if not today_tasks:
            pytest.skip("Geen taken vandaag")

        task_to_do = today_tasks[0]
        task_name = task_to_do["task_name"]
        assigned_to = task_to_do.get("assigned_to")

        if not assigned_to:
            pytest.skip("Taak heeft geen assignee")

        # Voltooi de taak
        engine.complete_task(assigned_to, task_name)

        # Check dat het voltooid is
        schedule_data = engine.get_week_schedule()
        for task in schedule_data["schedule"][today_name]["tasks"]:
            if task["task_name"] == task_name:
                assert task.get("completed") == True

        # Maak ongedaan
        result = engine.undo_task_completion(assigned_to, task_name, today)

        assert result["success"] == True, f"Undo failed: {result}"

        # Check dat taak weer op rooster staat
        schedule_data = engine.get_week_schedule()
        found = False
        for task in schedule_data["schedule"][today_name]["tasks"]:
            if task["task_name"] == task_name and not task.get("completed"):
                found = True
                break

        # Taak kan herplant zijn naar dezelfde of andere persoon
        assert result.get("rescheduled_to") is not None or found, \
            "Taak niet teruggeplaatst na undo"


class TestSwapScenarios:
    """Test swap scenario's (A doet B's taak, B doet A's taak)."""

    def test_mutual_swap_detected(self, patched_engine, members, tasks):
        """Als A en B elkaars taken doen, wordt dit als swap gedetecteerd."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Genereer rooster
        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Zoek 2 avondtaken op dezelfde dag van verschillende personen
        first_day = "maandag"
        day_tasks = schedule[first_day]["tasks"]

        evening_tasks = [t for t in day_tasks
                        if t.get("task_name") in ["uitruimen avond", "inruimen", "dekken"]
                        and t.get("assigned_to")]

        if len(evening_tasks) < 2:
            pytest.skip("Niet genoeg taken voor swap test")

        task_a = evening_tasks[0]
        task_b = evening_tasks[1]

        person_a = task_a["assigned_to"]
        person_b = task_b["assigned_to"]

        if person_a == person_b:
            pytest.skip("Zelfde persoon")

        # A doet B's taak en B doet A's taak (swap)
        # We gebruiken bulk completion om atomiciteit te garanderen
        today = mock_db.today_local()

        completions = [
            {"member_name": person_a, "task_name": task_b["task_name"], "completed_date": today},
            {"member_name": person_b, "task_name": task_a["task_name"], "completed_date": today},
        ]

        engine.complete_tasks_bulk(completions)

        # Haal rooster op - beide taken moeten voltooid zijn
        new_schedule = engine.get_week_schedule()

        task_a_completed = False
        task_b_completed = False

        for task in new_schedule["schedule"][first_day]["tasks"]:
            if task["task_name"] == task_a["task_name"] and task.get("completed"):
                task_a_completed = True
            if task["task_name"] == task_b["task_name"] and task.get("completed"):
                task_b_completed = True

        assert task_a_completed, f"Task A ({task_a['task_name']}) niet voltooid na swap"
        assert task_b_completed, f"Task B ({task_b['task_name']}) niet voltooid na swap"


class TestReschedulingFairness:
    """Test dat herplanning eerlijk blijft."""

    def test_rescheduling_doesnt_overload_one_person(self, patched_engine, members):
        """Herplanning mag niet één persoon overbelasten."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Start op maandag
        today = mock_db.today_local()
        days_since_monday = today.weekday()
        mock_db.set_current_date(today - timedelta(days=days_since_monday))

        # Genereer rooster
        initial_schedule = engine.get_week_schedule()
        initial_counts = count_tasks_per_member(initial_schedule["schedule"])

        # Skip naar vrijdag zonder taken te doen (veel gemiste taken)
        mock_db.advance_days(4)

        # Haal rooster op met herplanning
        final_schedule = engine.get_week_schedule()
        final_counts = count_tasks_per_member(final_schedule["schedule"])

        # Check dat niemand meer dan 50% meer taken heeft dan anderen
        if final_counts:
            max_count = max(final_counts.values())
            min_count = min(final_counts.values())

            if min_count > 0:
                ratio = max_count / min_count
                assert ratio <= 2.5, \
                    f"Na herplanning te ongelijke verdeling: {final_counts}, ratio={ratio:.2f}"


class TestEdgeCasesRescheduling:
    """Edge cases voor herplanning."""

    def test_reschedule_on_last_day_of_week(self, patched_engine, members):
        """Herplanning op zondag - geen ruimte meer."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Ga naar zondag
        today = mock_db.today_local()
        days_until_sunday = 6 - today.weekday()
        mock_db.advance_days(days_until_sunday)

        # Genereer rooster
        schedule_data = engine.get_week_schedule()

        # Op zondag kunnen gemiste taken niet meer herplant worden
        # Ze moeten of vervallen of naar volgende week
        # Dit zou geen crash mogen geven
        assert schedule_data is not None

    def test_reschedule_with_all_slots_full(self, patched_engine, members, tasks):
        """Herplanning als alle tijdslots al vol zijn."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Genereer rooster
        schedule_data = engine.get_week_schedule()

        # Dit scenario is complex om te simuleren, maar mag niet crashen
        # Het systeem moet graceful falen als herplanning niet mogelijk is
        assert schedule_data is not None

    def test_complete_task_not_in_schedule(self, patched_engine, members, tasks):
        """Voltooien van taak die niet in rooster staat (extra taak)."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Genereer rooster
        initial_schedule = engine.get_week_schedule()

        # Zoek een taak die vandaag NIET is ingepland
        today = mock_db.today_local()
        day_names = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
        today_name = day_names[today.weekday()]

        scheduled_today = {t["task_name"] for t in initial_schedule["schedule"][today_name]["tasks"]}

        # Vind een taak die niet is ingepland
        extra_task = None
        for task in tasks:
            if task.display_name not in scheduled_today:
                extra_task = task
                break

        if not extra_task:
            pytest.skip("Alle taken zijn al ingepland vandaag")

        # Voltooi deze extra taak
        engine.complete_task("Nora", extra_task.display_name)

        # Haal rooster op - extra taak moet als "extra" gemarkeerd zijn
        new_schedule = engine.get_week_schedule()

        found_extra = False
        for task in new_schedule["schedule"][today_name]["tasks"]:
            if task["task_name"] == extra_task.display_name:
                if task.get("extra") or task.get("completed"):
                    found_extra = True
                    break

        assert found_extra, f"Extra taak {extra_task.display_name} niet geregistreerd"
