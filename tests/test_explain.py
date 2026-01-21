"""
Tests voor de explain_task_assignment functie.

Deze functie geeft uitgebreide uitleg waarom iemand een taak krijgt,
bedoeld om transparantie te bieden aan de kinderen.
"""
import pytest


class TestExplainTaskAssignment:
    """Test de explain functie."""

    def test_explain_returns_all_required_fields(self, patched_engine, tasks):
        """Explain moet alle vereiste velden teruggeven."""
        engine = patched_engine
        task = tasks[1]  # uitruimen_avond

        explanation = engine.explain_task_assignment(task.display_name)

        # Check alle vereiste velden
        assert explanation.task_name == task.name
        assert explanation.task_display_name == task.display_name
        assert explanation.assigned_to is not None
        assert explanation.assigned_to_reason is not None
        assert len(explanation.comparisons) == 3  # 3 gezinsleden
        assert explanation.week_explanation is not None
        assert explanation.conclusion is not None
        assert explanation.raw_scores is not None

    def test_explain_comparison_has_visual_bars(self, patched_engine, tasks):
        """Elke vergelijking moet visuele balken hebben."""
        engine = patched_engine
        task = tasks[1]

        explanation = engine.explain_task_assignment(task.display_name)

        for comp in explanation.comparisons:
            assert comp.tasks_this_week_bar is not None
            assert "█" in comp.tasks_this_week_bar or "░" in comp.tasks_this_week_bar
            assert comp.specific_task_bar is not None
            assert comp.days_since_text is not None

    def test_explain_marks_assigned_person(self, patched_engine, tasks):
        """De toegewezen persoon moet gemarkeerd zijn."""
        engine = patched_engine
        task = tasks[1]

        explanation = engine.explain_task_assignment(task.display_name)

        assigned_comps = [c for c in explanation.comparisons if c.is_assigned]
        assert len(assigned_comps) == 1
        assert assigned_comps[0].name == explanation.assigned_to

    def test_explain_with_specific_member(self, patched_engine, members, tasks):
        """Explain voor een specifiek lid moet dat lid als assigned tonen."""
        engine = patched_engine
        task = tasks[1]

        # Vraag uitleg voor Linde specifiek
        explanation = engine.explain_task_assignment(task.display_name, "Linde")

        assert explanation.assigned_to == "Linde"
        assigned_comps = [c for c in explanation.comparisons if c.is_assigned]
        assert assigned_comps[0].name == "Linde"

    def test_explain_shows_days_since_text(self, patched_engine, members, tasks):
        """Days since moet leesbare tekst zijn."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]

        # Nora deed taak gisteren
        from datetime import timedelta
        yesterday = mock_db.today_local() - timedelta(days=1)
        mock_db.add_completion({
            "task_id": task.id,
            "member_id": members[0].id,
            "member_name": members[0].name,
            "task_name": task.display_name,
            "week_number": yesterday.isocalendar()[1],
            "completed_date": yesterday
        })

        explanation = engine.explain_task_assignment(task.display_name)

        nora_comp = next(c for c in explanation.comparisons if c.name == "Nora")
        assert nora_comp.days_since_text == "gisteren"

    def test_explain_unknown_task_raises_error(self, patched_engine):
        """Onbekende taak moet ValueError geven."""
        engine = patched_engine

        with pytest.raises(ValueError) as exc_info:
            engine.explain_task_assignment("onbekende_taak_xyz")

        assert "niet gevonden" in str(exc_info.value).lower()

    def test_explain_unknown_member_raises_error(self, patched_engine, tasks):
        """Onbekend lid moet ValueError geven."""
        engine = patched_engine
        task = tasks[0]

        with pytest.raises(ValueError) as exc_info:
            engine.explain_task_assignment(task.display_name, "Onbekend")

        assert "niet gevonden" in str(exc_info.value).lower()

    def test_explain_conclusion_mentions_assigned_person(self, patched_engine, tasks):
        """Conclusie moet de toegewezen persoon noemen."""
        engine = patched_engine
        task = tasks[1]

        explanation = engine.explain_task_assignment(task.display_name)

        assert explanation.assigned_to in explanation.conclusion

    def test_explain_raw_scores_contains_all_members(self, patched_engine, members, tasks):
        """Raw scores moet alle leden bevatten."""
        engine = patched_engine
        task = tasks[1]

        explanation = engine.explain_task_assignment(task.display_name)

        for member in members:
            assert member.name in explanation.raw_scores


class TestExplainWithHistory:
    """Test explain met bestaande taakgeschiedenis."""

    def test_explain_reflects_week_totals(self, patched_engine, members, tasks):
        """Explain moet correcte week totalen tonen."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]

        week_number = mock_db.today_local().isocalendar()[1]

        # Nora doet 3 taken deze week
        for t in tasks[:3]:
            mock_db.add_completion({
                "task_id": t.id,
                "member_id": members[0].id,
                "member_name": members[0].name,
                "task_name": t.display_name,
                "week_number": week_number
            })

        explanation = engine.explain_task_assignment(task.display_name)

        nora_comp = next(c for c in explanation.comparisons if c.name == "Nora")
        assert nora_comp.tasks_this_week == 3

    def test_explain_reflects_month_specific_task(self, patched_engine, members, tasks):
        """Explain moet correcte maandelijkse taak-specifieke count tonen."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]  # uitruimen_avond

        today = mock_db.today_local()

        # Linde doet uitruimen_avond 2x deze maand
        for i in range(2):
            mock_db.add_completion({
                "task_id": task.id,
                "member_id": members[1].id,
                "member_name": members[1].name,
                "task_name": task.display_name,
                "week_number": today.isocalendar()[1],
                "completed_date": today
            })

        explanation = engine.explain_task_assignment(task.display_name)

        linde_comp = next(c for c in explanation.comparisons if c.name == "Linde")
        assert linde_comp.specific_task_this_month == 2


class TestExplainAbsence:
    """Test explain met afwezigheid."""

    def test_explain_shows_absent_member(self, patched_engine, members, tasks):
        """Afwezig lid moet als niet-beschikbaar gemarkeerd zijn."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]

        today = mock_db.today_local()

        # Fenna is vandaag afwezig
        mock_db.add_absence({
            "member_id": members[2].id,
            "member_name": members[2].name,
            "start_date": today,
            "end_date": today,
            "reason": "Ziek"
        })

        explanation = engine.explain_task_assignment(task.display_name)

        fenna_comp = next(c for c in explanation.comparisons if c.name == "Fenna")
        assert fenna_comp.is_available == False

    def test_explain_doesnt_assign_to_absent(self, patched_engine, members, tasks):
        """Explain mag niet toewijzen aan afwezig lid."""
        engine = patched_engine
        mock_db = engine._mock_db
        task = tasks[1]

        today = mock_db.today_local()

        # Maak Nora en Linde afwezig, alleen Fenna over
        for member in members[:2]:
            mock_db.add_absence({
                "member_id": member.id,
                "member_name": member.name,
                "start_date": today,
                "end_date": today,
                "reason": "Afwezig"
            })

        explanation = engine.explain_task_assignment(task.display_name)

        assert explanation.assigned_to == "Fenna"
