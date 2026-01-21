"""
Unit tests voor de member selectie logica.

De _select_member_for_task functie kiest wie een taak krijgt toegewezen.
Selectiecriteria (in volgorde van prioriteit):
1. Tijdslot moet vrij zijn (geen 2 avondtaken per persoon per dag)
2. Minste keer deze specifieke taak gedaan deze MAAND
3. Minste taken deze WEEK
"""
import pytest
from datetime import date, timedelta

from tests.conftest import count_tasks_per_member, get_tasks_per_day_per_member


class TestTimeslotConstraints:
    """Test dat tijdslot constraints worden gerespecteerd."""

    def test_no_two_evening_tasks_same_person_same_day(self, patched_engine):
        """Niemand krijgt 2 avondtaken op dezelfde dag."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        tasks_per_day = get_tasks_per_day_per_member(schedule)

        for day_name, members_tasks in tasks_per_day.items():
            for member_name, task_list in members_tasks.items():
                # Tel avondtaken
                evening_tasks = [t for t in task_list if "avond" in t.lower() or
                                t in ["inruimen", "dekken", "koken"]]

                # Strict: max 1 taak per tijdslot
                # Note: uitruimen avond, inruimen, dekken zijn allemaal "avond"
                # Per persoon per dag mag max 1 avondtaak
                assert len(evening_tasks) <= 1, \
                    f"{member_name} heeft {len(evening_tasks)} avondtaken op {day_name}: {evening_tasks}"

    def test_no_two_midday_tasks_same_person_same_day(self, patched_engine):
        """Niemand krijgt 2 middagtaken op dezelfde dag."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        tasks_per_day = get_tasks_per_day_per_member(schedule)

        for day_name, members_tasks in tasks_per_day.items():
            for member_name, task_list in members_tasks.items():
                # Middag taken: karton en glas
                midday_tasks = [t for t in task_list if "karton" in t.lower() or "glas" in t.lower()]

                assert len(midday_tasks) <= 1, \
                    f"{member_name} heeft {len(midday_tasks)} middagtaken op {day_name}: {midday_tasks}"


class TestMemberSelectionFairness:
    """Test dat member selectie eerlijk is."""

    def test_tasks_distributed_across_all_members(self, patched_engine, members):
        """Taken worden verdeeld over alle gezinsleden."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        counts = count_tasks_per_member(schedule)

        # Alle 3 moeten taken hebben
        for member in members:
            assert member.name in counts, f"{member.name} heeft geen taken!"
            assert counts[member.name] > 0, f"{member.name} heeft 0 taken!"

    def test_no_member_has_double_tasks(self, patched_engine, members):
        """Geen enkel lid krijgt meer dan 2x zoveel taken als een ander."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        counts = count_tasks_per_member(schedule)
        values = list(counts.values())

        if min(values) > 0:
            ratio = max(values) / min(values)
            assert ratio <= 2.0, \
                f"Verdeling te ongelijk: {counts}, ratio {ratio:.2f}"

    def test_member_with_most_tasks_has_reason(self, patched_engine, members):
        """De verdeling moet verklaarbaar zijn."""
        engine = patched_engine

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        counts = count_tasks_per_member(schedule)

        # Verschil tussen max en min mag niet groter zijn dan aantal dagen
        # (want per dag kunnen verschillen ontstaan door afwezigheid/tijdslots)
        max_diff = max(counts.values()) - min(counts.values())
        assert max_diff <= 7, \
            f"Verschil te groot: {counts}, diff={max_diff}"


class TestAbsenceHandling:
    """Test dat afwezigheid correct wordt afgehandeld."""

    def test_absent_member_gets_no_tasks_on_absent_day(self, patched_engine, members):
        """Afwezig lid krijgt geen taken op die dag."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Nora is woensdag afwezig
        today = mock_db.today_local()
        week_start = today - timedelta(days=today.weekday())  # Maandag
        wednesday = week_start + timedelta(days=2)

        mock_db.add_absence({
            "member_id": members[0].id,
            "member_name": members[0].name,
            "start_date": wednesday,
            "end_date": wednesday,
            "reason": "Test afwezigheid"
        })

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Check woensdag
        wednesday_tasks = schedule["woensdag"]["tasks"]
        nora_tasks = [t for t in wednesday_tasks if t.get("assigned_to") == "Nora"]

        assert len(nora_tasks) == 0, \
            f"Nora is afwezig op woensdag maar heeft taken: {nora_tasks}"

    def test_absent_member_tasks_go_to_others(self, patched_engine, members):
        """Taken van afwezig lid gaan naar anderen."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Fenna is hele week afwezig
        today = mock_db.today_local()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        mock_db.add_absence({
            "member_id": members[2].id,
            "member_name": members[2].name,
            "start_date": week_start,
            "end_date": week_end,
            "reason": "Vakantie"
        })

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        counts = count_tasks_per_member(schedule)

        # Fenna mag geen taken hebben
        assert counts.get("Fenna", 0) == 0, \
            f"Fenna is hele week afwezig maar heeft {counts.get('Fenna', 0)} taken"

        # Nora en Linde moeten alle taken hebben
        assert counts.get("Nora", 0) > 0
        assert counts.get("Linde", 0) > 0


class TestMonthlyBalancing:
    """Test dat maandelijkse balancering werkt."""

    def test_member_who_did_task_often_this_month_gets_less(self, patched_engine, members, tasks):
        """Wie een taak vaak deed deze maand, krijgt hem minder."""
        engine = patched_engine
        mock_db = engine._mock_db

        today = mock_db.today_local()

        # Simuleer dat Nora deze maand al 5x inruimen heeft gedaan
        inruimen_task = next(t for t in tasks if t.name == "inruimen")

        for i in range(5):
            past_date = today - timedelta(days=i + 1)
            mock_db.add_completion({
                "task_id": inruimen_task.id,
                "member_id": members[0].id,
                "member_name": members[0].name,
                "task_name": inruimen_task.display_name,
                "week_number": past_date.isocalendar()[1],
                "completed_date": past_date
            })

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        # Tel hoeveel inruimen Nora krijgt vs anderen
        inruimen_counts = {}
        for day_data in schedule.values():
            for task in day_data.get("tasks", []):
                if task.get("task_name") == "inruimen":
                    assigned = task.get("assigned_to")
                    if assigned:
                        inruimen_counts[assigned] = inruimen_counts.get(assigned, 0) + 1

        # Nora zou minder of gelijk moeten krijgen
        nora_count = inruimen_counts.get("Nora", 0)
        linde_count = inruimen_counts.get("Linde", 0)
        fenna_count = inruimen_counts.get("Fenna", 0)

        # Nora mag niet meer krijgen dan de anderen (ze heeft al 5x gedaan)
        assert nora_count <= max(linde_count, fenna_count) + 1, \
            f"Nora (al 5x gedaan) krijgt teveel inruimen: Nora={nora_count}, Linde={linde_count}, Fenna={fenna_count}"


class TestEdgeCases:
    """Edge cases voor member selectie."""

    def test_all_members_absent_one_day(self, patched_engine, members):
        """Als iedereen afwezig is op een dag, geen taken die dag."""
        engine = patched_engine
        mock_db = engine._mock_db

        today = mock_db.today_local()
        week_start = today - timedelta(days=today.weekday())
        friday = week_start + timedelta(days=4)

        # Iedereen vrijdag afwezig
        for member in members:
            mock_db.add_absence({
                "member_id": member.id,
                "member_name": member.name,
                "start_date": friday,
                "end_date": friday,
                "reason": "Schoolreisje"
            })

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        friday_tasks = schedule["vrijdag"]["tasks"]
        assigned_tasks = [t for t in friday_tasks if t.get("assigned_to")]

        assert len(assigned_tasks) == 0, \
            f"Iedereen afwezig maar toch taken toegewezen: {assigned_tasks}"

    def test_one_member_available_gets_all_possible_tasks(self, patched_engine, members):
        """Als maar 1 lid beschikbaar is, krijgt die alle mogelijke taken."""
        engine = patched_engine
        mock_db = engine._mock_db

        today = mock_db.today_local()
        week_start = today - timedelta(days=today.weekday())
        monday = week_start

        # Linde en Fenna maandag afwezig
        for member in members[1:]:  # Niet Nora
            mock_db.add_absence({
                "member_id": member.id,
                "member_name": member.name,
                "start_date": monday,
                "end_date": monday,
                "reason": "Ziek"
            })

        schedule_data = engine.get_week_schedule()
        schedule = schedule_data["schedule"]

        monday_tasks = schedule["maandag"]["tasks"]
        for task in monday_tasks:
            if task.get("assigned_to"):
                assert task["assigned_to"] == "Nora", \
                    f"Alleen Nora beschikbaar maar {task['assigned_to']} krijgt taak"
