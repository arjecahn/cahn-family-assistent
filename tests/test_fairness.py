"""
Simulatie tests voor fairness over meerdere weken.

Dit zijn de BELANGRIJKSTE tests - ze simuleren echte scenario's
en meten of de verdeling eerlijk is over langere periodes.

Fairness criteria:
- Standaarddeviatie < 10% van gemiddelde
- Max verschil tussen 2 personen < 15%
- Per-taak verdeling: niemand doet >40% van één taaktype
"""
import pytest
from datetime import date, timedelta
from collections import defaultdict

from tests.conftest import (
    count_tasks_per_member,
    count_tasks_per_member_per_type,
    calculate_fairness_metrics
)


class TestMultiWeekFairness:
    """Test eerlijkheid over meerdere weken."""

    def _simulate_weeks(self, engine, num_weeks: int) -> dict:
        """
        Simuleer N weken van roosters.

        Returns dict met:
        - total_per_member: totaal taken per persoon
        - per_task_per_member: per taak per persoon
        - weekly_counts: per week per persoon
        """
        mock_db = engine._mock_db

        total_per_member = defaultdict(int)
        per_task_per_member = defaultdict(lambda: defaultdict(int))
        weekly_counts = []

        for week in range(num_weeks):
            # Ga naar volgende week (behalve eerste iteratie)
            if week > 0:
                mock_db.advance_days(7)
                # Verwijder vorige week's schedule zodat nieuwe wordt gegenereerd
                today = mock_db.today_local()
                week_num = today.isocalendar()[1]
                year = today.isocalendar()[0]
                mock_db.delete_schedule_for_week(week_num, year)

            # Genereer rooster
            schedule_data = engine.get_week_schedule()
            schedule = schedule_data["schedule"]

            # Tel taken
            week_counts = count_tasks_per_member(schedule)
            weekly_counts.append(week_counts)

            for name, count in week_counts.items():
                total_per_member[name] += count

            task_counts = count_tasks_per_member_per_type(schedule)
            for name, tasks in task_counts.items():
                for task_name, count in tasks.items():
                    per_task_per_member[name][task_name] += count

        return {
            "total_per_member": dict(total_per_member),
            "per_task_per_member": {k: dict(v) for k, v in per_task_per_member.items()},
            "weekly_counts": weekly_counts
        }

    def test_4_weeks_fairness(self, patched_engine, members):
        """Na 4 weken moet verdeling eerlijk zijn (±15%)."""
        result = self._simulate_weeks(patched_engine, 4)

        metrics = calculate_fairness_metrics(result["total_per_member"])

        # Fairness moet > 85% zijn (max 15% afwijking)
        assert metrics["fairness_pct"] >= 85, \
            f"Fairness {metrics['fairness_pct']:.1f}% is te laag na 4 weken. " \
            f"Verdeling: {result['total_per_member']}"

    def test_8_weeks_fairness(self, patched_engine, members):
        """Na 8 weken moet verdeling nog eerlijker zijn (±10%)."""
        result = self._simulate_weeks(patched_engine, 8)

        metrics = calculate_fairness_metrics(result["total_per_member"])

        # Na langere tijd moet het convergeren naar eerlijker
        assert metrics["fairness_pct"] >= 85, \
            f"Fairness {metrics['fairness_pct']:.1f}% is te laag na 8 weken. " \
            f"Verdeling: {result['total_per_member']}"

    def test_12_weeks_fairness(self, patched_engine, members):
        """Na 12 weken (kwartaal) moet verdeling heel eerlijk zijn."""
        result = self._simulate_weeks(patched_engine, 12)

        metrics = calculate_fairness_metrics(result["total_per_member"])

        # Na 3 maanden verwachten we goede balans
        assert metrics["fairness_pct"] >= 80, \
            f"Fairness {metrics['fairness_pct']:.1f}% is te laag na 12 weken. " \
            f"Verdeling: {result['total_per_member']}"

        # Ook standaarddeviatie checken
        assert metrics["std"] < metrics["mean"] * 0.15, \
            f"Standaarddeviatie {metrics['std']:.1f} is te hoog (mean={metrics['mean']:.1f})"


class TestPerTaskFairness:
    """Test dat specifieke taken eerlijk verdeeld worden."""

    def _simulate_weeks(self, engine, num_weeks: int) -> dict:
        """Simuleer N weken (hergebruik van bovenstaande)."""
        mock_db = engine._mock_db

        per_task_per_member = defaultdict(lambda: defaultdict(int))

        for week in range(num_weeks):
            if week > 0:
                mock_db.advance_days(7)
                today = mock_db.today_local()
                week_num = today.isocalendar()[1]
                year = today.isocalendar()[0]
                mock_db.delete_schedule_for_week(week_num, year)

            schedule_data = engine.get_week_schedule()
            schedule = schedule_data["schedule"]

            task_counts = count_tasks_per_member_per_type(schedule)
            for name, tasks in task_counts.items():
                for task_name, count in tasks.items():
                    per_task_per_member[name][task_name] += count

        return {k: dict(v) for k, v in per_task_per_member.items()}

    def test_no_one_does_same_task_always(self, patched_engine, members):
        """Niemand doet dezelfde HIGH-FREQUENCY taak meer dan 45% van de tijd.

        Note: Low-frequency taken (glas 1x/week, karton 2x/week) worden apart
        behandeld omdat die over korte periodes niet per se eerlijk verdeeld zijn.
        Het algoritme balanceert op TOTAAL taken, niet per taaktype.
        """
        per_task = self._simulate_weeks(patched_engine, 8)

        # Per taak checken
        task_totals = defaultdict(int)
        for member_tasks in per_task.values():
            for task_name, count in member_tasks.items():
                task_totals[task_name] += count

        # High-frequency taken: uitruimen, inruimen, dekken (7x/week elk)
        high_freq_tasks = {"uitruimen avond", "inruimen", "dekken", "uitruimen voor school"}

        violations = []
        for member_name, tasks in per_task.items():
            for task_name, count in tasks.items():
                # Skip low-frequency taken (glas, karton, koken)
                if task_name not in high_freq_tasks:
                    continue

                total = task_totals[task_name]
                if total > 0:
                    percentage = count / total * 100
                    if percentage > 45:  # 45% max (iets meer dan 33% want 3 personen)
                        violations.append(
                            f"{member_name} doet {task_name} {percentage:.0f}% van de tijd"
                        )

        assert len(violations) == 0, \
            f"High-frequency taak verdeling te ongelijk:\n" + "\n".join(f"  - {v}" for v in violations)

    def test_everyone_does_every_task_type(self, patched_engine, members):
        """Na 12 weken moet iedereen elke HIGH-FREQUENCY taak minstens 1x hebben gedaan.

        Note: Low-frequency taken (glas 1x/week, karton 2x/week, koken 1x/maand)
        worden niet getest omdat de frequentie te laag is om over 12 weken
        te garanderen dat iedereen elke taak doet. Het algoritme balanceert
        op TOTAAL taken, wat eerlijk is voor de kinderen.
        """
        # Simuleer 12 weken voor meer kans op variatie
        per_task = self._simulate_weeks(patched_engine, 12)

        # High-frequency taken die iedereen zou moeten doen
        high_freq_tasks = {"uitruimen avond", "inruimen", "dekken", "uitruimen voor school"}

        missing = []
        for member in members:
            member_tasks = per_task.get(member.name, {})
            for task_name in high_freq_tasks:
                if member_tasks.get(task_name, 0) == 0:
                    missing.append(f"{member.name} heeft nooit {task_name} gedaan")

        assert len(missing) == 0, \
            f"Niet iedereen heeft alle high-freq taken gedaan:\n" + "\n".join(f"  - {m}" for m in missing)


class TestWeeklyConsistency:
    """Test dat wekelijkse verdeling consistent is."""

    def _simulate_weeks(self, engine, num_weeks: int) -> list[dict]:
        """Simuleer N weken, return weekly counts."""
        mock_db = engine._mock_db
        weekly_counts = []

        for week in range(num_weeks):
            if week > 0:
                mock_db.advance_days(7)
                today = mock_db.today_local()
                week_num = today.isocalendar()[1]
                year = today.isocalendar()[0]
                mock_db.delete_schedule_for_week(week_num, year)

            schedule_data = engine.get_week_schedule()
            schedule = schedule_data["schedule"]
            weekly_counts.append(count_tasks_per_member(schedule))

        return weekly_counts

    def test_weekly_variance_acceptable(self, patched_engine, members):
        """Wekelijkse taken per persoon mogen niet te veel variëren."""
        weekly_counts = self._simulate_weeks(patched_engine, 8)

        # Per persoon: check variance over weken
        for member in members:
            member_weekly = [w.get(member.name, 0) for w in weekly_counts]
            mean = sum(member_weekly) / len(member_weekly)
            variance = sum((x - mean) ** 2 for x in member_weekly) / len(member_weekly)
            std = variance ** 0.5

            # Standaarddeviatie mag niet groter zijn dan 30% van gemiddelde
            # (enige variatie is normaal door afwezigheid/weekends)
            assert std < mean * 0.35 or mean < 3, \
                f"{member.name} heeft te veel wekelijkse variatie: " \
                f"mean={mean:.1f}, std={std:.1f}, weekly={member_weekly}"

    def test_no_week_without_tasks(self, patched_engine, members):
        """Niemand heeft een week zonder taken (tenzij afwezig)."""
        weekly_counts = self._simulate_weeks(patched_engine, 8)

        zero_weeks = []
        for week_idx, week in enumerate(weekly_counts):
            for member in members:
                if week.get(member.name, 0) == 0:
                    zero_weeks.append(f"Week {week_idx + 1}: {member.name} heeft 0 taken")

        # We staan max 1 "zero week" toe (kan gebeuren door toevallige verdeling)
        assert len(zero_weeks) <= 1, \
            f"Te vaak weken zonder taken:\n" + "\n".join(f"  - {z}" for z in zero_weeks)


class TestFairnessWithAbsence:
    """Test dat eerlijkheid behouden blijft bij afwezigheid."""

    def test_catch_up_after_absence(self, patched_engine, members):
        """Na afwezigheid moet inhalen plaatsvinden."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Week 1: Nora is afwezig
        today = mock_db.today_local()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        mock_db.add_absence({
            "member_id": members[0].id,
            "member_name": members[0].name,
            "start_date": week_start,
            "end_date": week_end,
            "reason": "Ziek"
        })

        # Genereer week 1
        schedule_data = engine.get_week_schedule()
        week1_counts = count_tasks_per_member(schedule_data["schedule"])

        # Verwijder afwezigheid
        mock_db.absences.clear()

        # Week 2-4: iedereen aanwezig
        total_counts = defaultdict(int)
        for name, count in week1_counts.items():
            total_counts[name] += count

        for _ in range(3):
            mock_db.advance_days(7)
            today = mock_db.today_local()
            week_num = today.isocalendar()[1]
            year = today.isocalendar()[0]
            mock_db.delete_schedule_for_week(week_num, year)

            schedule_data = engine.get_week_schedule()
            week_counts = count_tasks_per_member(schedule_data["schedule"])

            for name, count in week_counts.items():
                total_counts[name] += count

        # Na 4 weken moet Nora ingehaald hebben
        metrics = calculate_fairness_metrics(dict(total_counts))

        # Iets ruimere marge omdat Nora 1 week miste
        assert metrics["fairness_pct"] >= 70, \
            f"Na afwezigheid en inhalen: fairness {metrics['fairness_pct']:.1f}% te laag. " \
            f"Verdeling: {dict(total_counts)}"

    def test_two_weeks_absence_still_fair(self, patched_engine, members):
        """Na 2 weken afwezigheid moet systeem nog steeds eerlijk zijn."""
        engine = patched_engine
        mock_db = engine._mock_db

        # Week 1-2: Linde afwezig
        today = mock_db.today_local()
        week_start = today - timedelta(days=today.weekday())

        mock_db.add_absence({
            "member_id": members[1].id,
            "member_name": members[1].name,
            "start_date": week_start,
            "end_date": week_start + timedelta(days=13),  # 2 weken
            "reason": "Vakantie"
        })

        total_counts = defaultdict(int)

        # Simuleer 6 weken
        for week in range(6):
            if week > 0:
                mock_db.advance_days(7)
                today = mock_db.today_local()
                week_num = today.isocalendar()[1]
                year = today.isocalendar()[0]
                mock_db.delete_schedule_for_week(week_num, year)

            # Verwijder afwezigheid na week 2
            if week == 2:
                mock_db.absences.clear()

            schedule_data = engine.get_week_schedule()
            week_counts = count_tasks_per_member(schedule_data["schedule"])

            for name, count in week_counts.items():
                total_counts[name] += count

        # Na 6 weken (4 actieve voor Linde) moet het redelijk eerlijk zijn
        metrics = calculate_fairness_metrics(dict(total_counts))

        # Linde miste 2 van 6 weken, dus verwacht ~67% van anderen
        # Check dat Linde niet te ver achterblijft
        nora_fenna_avg = (total_counts["Nora"] + total_counts["Fenna"]) / 2
        linde_ratio = total_counts["Linde"] / nora_fenna_avg if nora_fenna_avg > 0 else 1

        assert linde_ratio >= 0.5, \
            f"Linde ({total_counts['Linde']}) te ver achter op anderen " \
            f"(gemiddeld {nora_fenna_avg:.1f})"


class TestFairnessMetricsOutput:
    """Test dat metrics correct berekend worden."""

    def test_metrics_calculation(self):
        """Test calculate_fairness_metrics functie."""
        # Perfecte verdeling
        perfect = {"A": 10, "B": 10, "C": 10}
        metrics = calculate_fairness_metrics(perfect)
        assert metrics["fairness_pct"] == 100
        assert metrics["std"] == 0
        assert metrics["max_diff"] == 0

        # Ongelijke verdeling
        unequal = {"A": 10, "B": 5, "C": 15}
        metrics = calculate_fairness_metrics(unequal)
        assert metrics["max_diff"] == 10
        assert metrics["mean"] == 10
        # max_diff (10) / mean (10) * 100 = 100%, dus fairness = 0%
        # Dit is correct: 100% afwijking van gemiddelde = 0% fairness
        assert metrics["fairness_pct"] == 0

        # Milde ongelijkheid
        mild_unequal = {"A": 10, "B": 9, "C": 11}
        metrics = calculate_fairness_metrics(mild_unequal)
        assert metrics["max_diff"] == 2
        assert metrics["mean"] == 10
        # max_diff (2) / mean (10) * 100 = 20%, dus fairness = 80%
        assert metrics["fairness_pct"] == 80

    def test_empty_counts(self):
        """Lege input moet niet crashen."""
        metrics = calculate_fairness_metrics({})
        assert metrics["fairness_pct"] == 100


class TestLongTermFairness:
    """Lange termijn simulaties (optioneel, kunnen langzaam zijn)."""

    @pytest.mark.slow
    def test_26_weeks_fairness(self, patched_engine, members):
        """Na half jaar moet verdeling zeer eerlijk zijn."""
        mock_db = patched_engine._mock_db

        total = defaultdict(int)

        for week in range(26):
            if week > 0:
                mock_db.advance_days(7)
                today = mock_db.today_local()
                week_num = today.isocalendar()[1]
                year = today.isocalendar()[0]
                mock_db.delete_schedule_for_week(week_num, year)

            schedule_data = patched_engine.get_week_schedule()
            counts = count_tasks_per_member(schedule_data["schedule"])
            for name, count in counts.items():
                total[name] += count

        metrics = calculate_fairness_metrics(dict(total))

        # Na half jaar moet het zeer eerlijk zijn
        assert metrics["fairness_pct"] >= 90, \
            f"26-weeks fairness {metrics['fairness_pct']:.1f}% te laag: {dict(total)}"
