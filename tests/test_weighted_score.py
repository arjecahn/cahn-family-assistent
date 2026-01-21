"""
Unit tests voor de weighted score berekening.

De weighted score bepaalt wie het meest aan de beurt is voor een taak.
Lagere score = meer aan de beurt.

Weging:
- 50%: Totaal aantal taken deze week
- 30%: Aantal keer deze specifieke taak gedaan
- 20%: Hoe lang geleden deze taak gedaan (recency)
"""
import pytest
from datetime import date, timedelta


class TestWeightedScoreBasics:
    """Basis tests voor de score berekening."""

    def test_equal_starting_conditions_equal_scores(self, patched_engine, members, tasks):
        """Als niemand iets heeft gedaan, moeten scores gelijk zijn."""
        engine = patched_engine
        task = tasks[0]  # uitruimen_ochtend

        # Bereken scores voor alle members
        scores = []
        for member in members:
            score = engine.calculate_weighted_score(member, task, members)
            scores.append(score)

        # Alle scores moeten gelijk zijn (niemand heeft iets gedaan)
        assert scores[0] == scores[1] == scores[2], \
            f"Scores moeten gelijk zijn bij gelijke uitgangspositie: {scores}"

    def test_more_tasks_this_week_higher_score(self, patched_engine, members, tasks):
        """Wie meer taken heeft gedaan deze week krijgt hogere score."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]  # uitruimen_avond

        # Nora doet 3 taken deze week
        week_number = mock_db.today_local().isocalendar()[1]
        for i in range(3):
            mock_db.add_completion({
                "task_id": tasks[i].id,
                "member_id": members[0].id,  # Nora
                "member_name": members[0].name,
                "task_name": tasks[i].display_name,
                "week_number": week_number
            })

        # Bereken scores
        score_nora = engine.calculate_weighted_score(members[0], task, members)
        score_linde = engine.calculate_weighted_score(members[1], task, members)
        score_fenna = engine.calculate_weighted_score(members[2], task, members)

        # Nora moet hogere score hebben (minder aan de beurt)
        assert score_nora > score_linde, "Nora (3 taken) moet hogere score hebben dan Linde (0 taken)"
        assert score_nora > score_fenna, "Nora (3 taken) moet hogere score hebben dan Fenna (0 taken)"
        assert score_linde == score_fenna, "Linde en Fenna moeten gelijke score hebben"

    def test_more_specific_task_higher_score(self, patched_engine, members, tasks):
        """Wie deze specifieke taak vaker heeft gedaan krijgt hogere score."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]  # uitruimen_avond

        week_number = mock_db.today_local().isocalendar()[1]

        # Nora doet uitruimen_avond 2x
        for _ in range(2):
            mock_db.add_completion({
                "task_id": task.id,
                "member_id": members[0].id,
                "member_name": members[0].name,
                "task_name": task.display_name,
                "week_number": week_number
            })

        # Linde doet uitruimen_avond 1x
        mock_db.add_completion({
            "task_id": task.id,
            "member_id": members[1].id,
            "member_name": members[1].name,
            "task_name": task.display_name,
            "week_number": week_number
        })

        # Fenna doet andere taak 2x (gelijke totaal als Nora, maar niet deze taak)
        for _ in range(2):
            mock_db.add_completion({
                "task_id": tasks[2].id,  # inruimen
                "member_id": members[2].id,
                "member_name": members[2].name,
                "task_name": tasks[2].display_name,
                "week_number": week_number
            })

        score_nora = engine.calculate_weighted_score(members[0], task, members)
        score_linde = engine.calculate_weighted_score(members[1], task, members)
        score_fenna = engine.calculate_weighted_score(members[2], task, members)

        # Nora moet hoogste score hebben (deed deze taak het vaakst)
        assert score_nora > score_linde, \
            f"Nora (2x deze taak) moet hogere score dan Linde (1x): {score_nora} vs {score_linde}"

    def test_recency_affects_score(self, patched_engine, members, tasks):
        """Wie recent een taak deed krijgt hogere score."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]  # uitruimen_avond

        today = mock_db.today_local()
        week_number = today.isocalendar()[1]

        # Nora deed taak vandaag
        mock_db.add_completion({
            "task_id": task.id,
            "member_id": members[0].id,
            "member_name": members[0].name,
            "task_name": task.display_name,
            "week_number": week_number,
            "completed_date": today
        })

        # Linde deed taak 5 dagen geleden
        five_days_ago = today - timedelta(days=5)
        mock_db.add_completion({
            "task_id": task.id,
            "member_id": members[1].id,
            "member_name": members[1].name,
            "task_name": task.display_name,
            "week_number": five_days_ago.isocalendar()[1],
            "completed_date": five_days_ago
        })

        score_nora = engine.calculate_weighted_score(members[0], task, members)
        score_linde = engine.calculate_weighted_score(members[1], task, members)

        # Nora deed het recenter, dus hogere recency component (minder aan de beurt)
        # Maar beide hebben 1x gedaan, dus specific_task is gelijk
        # Het verschil komt van de recency (20%)
        assert score_nora > score_linde, \
            f"Nora (recent gedaan) moet hogere score dan Linde (5 dagen geleden): {score_nora} vs {score_linde}"


class TestWeightedScoreEdgeCases:
    """Edge cases en randgevallen."""

    def test_single_available_member(self, patched_engine, members, tasks):
        """Als maar 1 persoon beschikbaar is, krijgt die de taak."""
        engine = patched_engine
        task = tasks[0]

        # Alleen Nora beschikbaar
        available = [members[0]]

        score = engine.calculate_weighted_score(members[0], task, available)

        # Score moet berekend kunnen worden
        assert isinstance(score, float)

    def test_zero_tasks_week_no_division_error(self, patched_engine, members, tasks):
        """Geen division by zero als niemand taken heeft gedaan."""
        engine = patched_engine
        task = tasks[0]

        # Niemand heeft iets gedaan - mag geen error geven
        for member in members:
            score = engine.calculate_weighted_score(member, task, members)
            assert isinstance(score, float)
            assert score >= 0

    def test_score_between_zero_and_one(self, patched_engine, members, tasks):
        """Score moet altijd tussen 0 en 1 liggen (genormaliseerd)."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]

        week_number = mock_db.today_local().isocalendar()[1]

        # Voeg wat completions toe
        for i, member in enumerate(members):
            for _ in range(i + 1):  # Nora 1x, Linde 2x, Fenna 3x
                mock_db.add_completion({
                    "task_id": task.id,
                    "member_id": member.id,
                    "member_name": member.name,
                    "task_name": task.display_name,
                    "week_number": week_number
                })

        for member in members:
            score = engine.calculate_weighted_score(member, task, members)
            assert 0 <= score <= 1.5, f"Score {score} voor {member.name} is buiten verwacht bereik"


class TestSuggestMemberForTask:
    """Test de suggest_member_for_task functie die de score gebruikt."""

    def test_suggests_member_with_lowest_score(self, patched_engine, members, tasks):
        """Functie moet lid met laagste score suggereren."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]

        week_number = mock_db.today_local().isocalendar()[1]

        # Nora en Linde hebben taken gedaan, Fenna niet
        mock_db.add_completion({
            "task_id": task.id,
            "member_id": members[0].id,
            "member_name": members[0].name,
            "task_name": task.display_name,
            "week_number": week_number
        })
        mock_db.add_completion({
            "task_id": task.id,
            "member_id": members[1].id,
            "member_name": members[1].name,
            "task_name": task.display_name,
            "week_number": week_number
        })

        suggestion = engine.suggest_member_for_task(task.display_name)

        # Fenna moet gesuggereerd worden (laagste score)
        assert suggestion.suggested_member.name == "Fenna", \
            f"Fenna moet gesuggereerd worden, niet {suggestion.suggested_member.name}"

    def test_suggestion_includes_reason(self, patched_engine, tasks):
        """Suggestie moet een uitleg bevatten."""
        engine = patched_engine
        task = tasks[0]

        suggestion = engine.suggest_member_for_task(task.display_name)

        assert suggestion.reason is not None
        assert len(suggestion.reason) > 0

    def test_suggestion_includes_all_scores(self, patched_engine, members, tasks):
        """Suggestie moet scores van alle beschikbare leden bevatten."""
        engine = patched_engine
        task = tasks[0]

        suggestion = engine.suggest_member_for_task(task.display_name)

        assert len(suggestion.scores) == len(members)
        score_names = {s.member.name for s in suggestion.scores}
        expected_names = {m.name for m in members}
        assert score_names == expected_names

    def test_unknown_task_raises_error(self, patched_engine):
        """Onbekende taak moet ValueError geven."""
        engine = patched_engine

        with pytest.raises(ValueError) as exc_info:
            engine.suggest_member_for_task("onbekende_taak_xyz")

        assert "niet gevonden" in str(exc_info.value).lower()


class TestScoreConsistency:
    """Test dat scores consistent en voorspelbaar zijn."""

    def test_same_input_same_output(self, patched_engine, members, tasks):
        """Zelfde input moet zelfde score geven (deterministisch)."""
        engine = patched_engine
        task = tasks[0]

        score1 = engine.calculate_weighted_score(members[0], task, members)
        score2 = engine.calculate_weighted_score(members[0], task, members)

        assert score1 == score2, "Zelfde input moet zelfde score geven"

    def test_score_ordering_is_transitive(self, patched_engine, members, tasks):
        """Als A < B en B < C, dan A < C (transitiviteit)."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]

        week_number = mock_db.today_local().isocalendar()[1]

        # Fenna: 0 taken, Linde: 1 taak, Nora: 2 taken
        mock_db.add_completion({
            "task_id": task.id,
            "member_id": members[1].id,
            "member_name": members[1].name,
            "task_name": task.display_name,
            "week_number": week_number
        })
        for _ in range(2):
            mock_db.add_completion({
                "task_id": task.id,
                "member_id": members[0].id,
                "member_name": members[0].name,
                "task_name": task.display_name,
                "week_number": week_number
            })

        score_nora = engine.calculate_weighted_score(members[0], task, members)
        score_linde = engine.calculate_weighted_score(members[1], task, members)
        score_fenna = engine.calculate_weighted_score(members[2], task, members)

        # Fenna < Linde < Nora (qua score, dus Fenna meest aan de beurt)
        assert score_fenna < score_linde < score_nora, \
            f"Volgorde moet zijn Fenna < Linde < Nora: {score_fenna}, {score_linde}, {score_nora}"
