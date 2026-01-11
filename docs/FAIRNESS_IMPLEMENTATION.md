# Fairness Implementation Guide

This document provides concrete code changes to address the fairness issues identified in `FAIRNESS_ANALYSIS.md`.

---

## Priority 1: Fix First-Pick Bias (Quick Win)

### Problem
When multiple members have equal scores, the first one in the list always gets picked.

### Solution
Add randomization when scores are tied.

**File: `src/task_engine.py`**

```python
# Add at top of file
import random

# Modify _select_member_for_task() around line 1520:

def _select_member_for_task(self, task: Task, available_members: list,
                               member_week_counts: dict, member_day_slots: dict,
                               member_month_task_counts: dict = None) -> Optional[Member]:
    """Selecteer het beste lid voor een taak."""
    time_slot = task.time_of_day
    task_name = task.display_name

    # Filter op wie dit tijdslot nog vrij heeft vandaag
    eligible = [
        m for m in available_members
        if time_slot not in member_day_slots.get(m.name, set())
    ]

    if not eligible:
        return None

    def sort_key(m):
        month_task_count = 0
        if member_month_task_counts and m.name in member_month_task_counts:
            month_task_count = member_month_task_counts[m.name].get(task_name, 0)
        week_count = member_week_counts.get(m.name, 0)
        return (month_task_count, week_count)

    # Group by equal scores and randomize within groups
    from collections import defaultdict
    score_groups = defaultdict(list)
    for m in eligible:
        score = sort_key(m)
        score_groups[score].append(m)

    # Get the lowest score (best candidates)
    min_score = min(score_groups.keys())
    best_candidates = score_groups[min_score]

    # RANDOM selection among equally-scored candidates
    return random.choice(best_candidates)
```

---

## Priority 2: Long-Term Fairness Balance

### Problem
Weekly and monthly resets cause fairness amnesia.

### Solution
Add a persistent fairness balance table that tracks credit/debt over time.

### Database Migration

**File: `src/database.py`** - Add new table:

```python
def migrate_add_fairness_balance_table():
    """Add fairness_balance table for long-term tracking."""
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fairness_balance (
                id SERIAL PRIMARY KEY,
                member_id INTEGER REFERENCES members(id) ON DELETE CASCADE UNIQUE,
                member_name VARCHAR(50),
                balance FLOAT DEFAULT 0.0,
                last_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Initialize with 0 balance for all members
        cur.execute("""
            INSERT INTO fairness_balance (member_id, member_name, balance)
            SELECT id, name, 0.0 FROM members
            ON CONFLICT (member_id) DO NOTHING
        """)

        conn.commit()
        print("fairness_balance table created!")

    except Exception as e:
        conn.rollback()
        raise e

    finally:
        cur.close()
        conn.close()


def get_fairness_balance(member_id: str) -> float:
    """Get the fairness balance for a member."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT balance FROM fairness_balance WHERE member_id = %s
    """, (int(member_id),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["balance"] if row else 0.0


def update_fairness_balance(member_id: str, delta: float):
    """Update the fairness balance for a member.

    Positive delta = member owes more work
    Negative delta = member is owed (did extra work)
    """
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE fairness_balance
        SET balance = balance + %s, last_updated = CURRENT_TIMESTAMP
        WHERE member_id = %s
    """, (delta, int(member_id)))
    conn.commit()
    cur.close()
    conn.close()


def get_all_fairness_balances() -> dict:
    """Get all fairness balances as {member_name: balance}."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT member_name, balance FROM fairness_balance")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["member_name"]: r["balance"] for r in rows}
```

### Updated Score Calculation

**File: `src/task_engine.py`**

```python
def calculate_weighted_score_v2(
    self,
    member: Member,
    task: Task,
    available_members: list[Member]
) -> float:
    """
    Improved weighted score with long-term fairness consideration.
    Lower score = more aan de beurt.

    Weging:
    - 35%: Totaal aantal taken deze week (weekly balance)
    - 25%: Aantal keer deze specifieke taak gedaan (task variety)
    - 20%: Fairness balance (long-term debt/credit)
    - 10%: Recency (how recently they did this task)
    - 10%: Random factor (tie-breaker, prevents predictability)
    """
    total_tasks = self.get_task_count_this_week(member)
    specific_tasks = self.get_task_count_this_week(member, task)
    last_did = self.get_last_completion(member, task)
    fairness_balance = db.get_fairness_balance(member.id)

    # Normalize week tasks
    max_total = max(self.get_task_count_this_week(m) for m in available_members) or 1
    max_specific = max(self.get_task_count_this_week(m, task) for m in available_members) or 1

    # Recency score
    if last_did:
        days_ago = (now_local() - last_did).days
        recency_score = min(days_ago / 7, 1.0)
    else:
        recency_score = 1.0

    # Fairness balance normalization (-10 to +10 typical range)
    # Positive balance = owes tasks = should get lower score (more likely picked)
    fairness_score = max(-1.0, min(1.0, fairness_balance / 10.0))

    weighted = (
        (total_tasks / max_total) * 0.35 +
        (specific_tasks / max_specific) * 0.25 +
        (-fairness_score) * 0.20 +  # Negative because higher debt = lower score
        (1 - recency_score) * 0.10 +
        random.random() * 0.10  # Random tie-breaker
    )

    return weighted


def on_task_completed(self, member: Member):
    """Called when a member completes a task - updates fairness balance."""
    # Member did work, reduce their balance (they're owed less / owe less)
    db.update_fairness_balance(member.id, -0.1)


def weekly_fairness_normalization():
    """Run weekly to prevent extreme drift in balances."""
    balances = db.get_all_fairness_balances()

    # Calculate the mean
    mean_balance = sum(balances.values()) / len(balances)

    # Normalize: shift everyone so mean is 0
    for member_name, balance in balances.items():
        member = db.get_member_by_name(member_name)
        adjustment = -mean_balance
        db.update_fairness_balance(member.id, adjustment)
```

---

## Priority 3: Absence Compensation

### Problem
When someone is absent, siblings work extra but get no compensation.

### Solution
Track absence periods and credit siblings who covered.

**File: `src/task_engine.py`**

```python
def register_absence(
    self,
    member_name: str,
    start: date,
    end: date,
    reason: Optional[str] = None
):
    """Register absence and calculate compensation for siblings."""
    member = db.get_member_by_name(member_name)
    if not member:
        raise ValueError(f"Gezinslid '{member_name}' niet gevonden")

    absence = db.add_absence({
        "member_id": member.id,
        "member_name": member.name,
        "start_date": start,
        "end_date": end,
        "reason": reason
    })

    # Calculate fairness compensation
    self._calculate_absence_compensation(member, start, end)

    return absence


def _calculate_absence_compensation(self, absent_member: Member, start: date, end: date):
    """Credit siblings who will cover for the absent member."""
    all_members = db.get_all_members()
    siblings = [m for m in all_members if m.id != absent_member.id]

    if not siblings:
        return

    # Calculate how many days of absence
    absence_days = (end - start).days + 1

    # Average daily tasks per person (roughly)
    # Total weekly tasks: 21 evening + 3 morning + 3 recycling = ~27
    # Per person per day: 27 / 7 / 3 = ~1.3 tasks
    avg_tasks_per_person_per_day = 1.3

    # Extra load per sibling per day when one is absent:
    # Normal: 27/3 = 9 per person per week = 1.3/day
    # With absence: 27/2 = 13.5 per person per week = 1.9/day
    # Extra per sibling: 0.6 tasks/day
    extra_per_sibling_per_day = avg_tasks_per_person_per_day * (3 / 2 - 1)

    total_extra_per_sibling = extra_per_sibling_per_day * absence_days

    # Update balances
    for sibling in siblings:
        # Siblings are OWED (negative balance = they're owed tasks)
        db.update_fairness_balance(sibling.id, -total_extra_per_sibling)

    # Absent member OWES (positive balance = they owe tasks)
    db.update_fairness_balance(absent_member.id, total_extra_per_sibling * len(siblings))
```

---

## Priority 4: Unified Scoring Algorithm

### Problem
`calculate_weighted_score()` and `_select_member_for_task()` use different logic.

### Solution
Create a single scoring function used everywhere.

**File: `src/task_engine.py`**

```python
def get_unified_score(
    self,
    member: Member,
    task: Task,
    week_counts: dict,
    month_counts: dict,
    day_slots: dict,
    fairness_balances: dict
) -> tuple:
    """
    Unified scoring for all task assignments.

    Returns a tuple for stable sorting:
    (can_do_slot, month_task_count, week_count, fairness_balance, random)

    Lower tuple = more likely to be assigned.
    """
    task_name = task.display_name
    time_slot = task.time_of_day

    # Check if time slot is available
    can_do = 0 if time_slot not in day_slots.get(member.name, set()) else 999

    # Monthly count for this specific task
    month_task_count = month_counts.get(member.name, {}).get(task_name, 0)

    # Weekly total count
    week_count = week_counts.get(member.name, 0)

    # Fairness balance (positive = owes, should be picked more)
    # Invert so positive balance = lower score
    fairness = -fairness_balances.get(member.name, 0)

    # Random tie-breaker
    rand = random.random()

    return (can_do, month_task_count, week_count, fairness, rand)


# Use everywhere:
def suggest_member_for_task(self, task_name: str) -> TaskSuggestion:
    """Suggereer wie een taak moet doen."""
    task = db.get_task_by_name(task_name)
    available = self.get_available_members()

    # Build scoring context
    week_counts = {m.name: self.get_task_count_this_week(m) for m in available}
    month_counts = self._get_month_task_counts(available, task)
    day_slots = {}  # Not relevant for suggestions
    fairness_balances = db.get_all_fairness_balances()

    # Score all members
    scored = [
        (self.get_unified_score(m, task, week_counts, month_counts, day_slots, fairness_balances), m)
        for m in available
    ]
    scored.sort(key=lambda x: x[0])

    suggested = scored[0][1]
    # ... rest of method
```

---

## Priority 5: Smart Missed Task Redistribution

### Problem
Missed tasks stay with the original person regardless of the new day's load.

### Solution
Redistribute based on target day's balance.

**File: `src/task_engine.py`**

```python
def _reschedule_missed_task_smart(
    self,
    missed_task: dict,
    week_number: int,
    year: int,
    week_start: date,
    day_availability: dict,
    tasks_lookup: dict
) -> Optional[dict]:
    """
    Smart rescheduling that considers target day load balance.

    Returns dict with new assignment info, or None if can't reschedule.
    """
    task_name = missed_task["task_name"]
    original_member_name = missed_task["assigned_to"]
    time_slot = missed_task.get("time_of_day", "avond")
    task = tasks_lookup.get(task_name)

    if not task:
        return None

    today = today_local()
    today_idx = (today - week_start).days

    # Get all assignments for remaining days
    all_assignments = db.get_schedule_for_week(week_number, year)

    # Build load per member per remaining day
    day_member_loads = {}
    for day_idx in range(max(0, today_idx), 7):
        day_member_loads[day_idx] = {}
        day_assignments = [a for a in all_assignments if a.day_of_week == day_idx]
        for a in day_assignments:
            day_member_loads[day_idx][a.member_name] = \
                day_member_loads[day_idx].get(a.member_name, 0) + 1

    # Find best (day, member) combination
    best_option = None
    best_score = float('inf')

    for target_day_idx in range(max(0, today_idx), 7):
        day_name = DAY_NAMES[target_day_idx]
        available = day_availability.get(day_name, [])

        for member in available:
            # Check time slot availability
            # ... (existing slot check logic)

            # Score: lower is better
            current_load = day_member_loads[target_day_idx].get(member.name, 0)
            fairness = db.get_fairness_balance(member.id)

            # Prefer members with lower load and higher fairness debt (positive balance)
            score = current_load - (fairness * 0.5)

            if score < best_score:
                best_score = score
                best_option = {"day_idx": target_day_idx, "member": member}

    if not best_option:
        return None

    # If assigning to different person than original, update fairness
    if best_option["member"].name != original_member_name:
        # Original person "got out" of the task
        original_member = db.get_member_by_name(original_member_name)
        if original_member:
            db.update_fairness_balance(original_member.id, +0.5)  # They owe more

        # New person takes on extra
        db.update_fairness_balance(best_option["member"].id, -0.5)  # They're owed

    return {
        "day_of_week": best_option["day_idx"],
        "member_id": best_option["member"].id,
        "member_name": best_option["member"].name,
        "task_id": task.id,
        "task_name": task_name
    }
```

---

## Testing the Changes

### Unit Test Examples

```python
# tests/test_fairness.py

def test_equal_score_randomization():
    """Verify that equal scores produce varied selections over time."""
    from collections import Counter

    selections = Counter()
    for _ in range(100):
        selected = engine._select_member_for_task(
            task, members_with_equal_scores, counts, slots, month_counts
        )
        selections[selected.name] += 1

    # With randomization, each member should be picked roughly equally
    for name, count in selections.items():
        assert 20 < count < 50, f"{name} was picked {count} times (expected ~33)"


def test_absence_compensation():
    """Verify siblings get credit when one is absent."""
    # Initial balance: all 0
    for m in members:
        assert db.get_fairness_balance(m.id) == 0.0

    # Register 7-day absence for Nora
    engine.register_absence("Nora", date(2026, 1, 1), date(2026, 1, 7))

    # Nora should owe tasks (positive balance)
    assert db.get_fairness_balance(nora.id) > 0

    # Linde and Fenna should be owed (negative balance)
    assert db.get_fairness_balance(linde.id) < 0
    assert db.get_fairness_balance(fenna.id) < 0


def test_month_transition_fairness():
    """Verify fairness persists across month boundaries."""
    # Simulate January with imbalance
    simulate_month(imbalanced=True)

    # Check balances before transition
    jan_balances = db.get_all_fairness_balances()

    # Transition to February
    simulate_month_transition()

    # Balances should persist (not reset)
    feb_balances = db.get_all_fairness_balances()
    assert jan_balances == feb_balances
```

---

## Rollout Plan

### Week 1: Preparation
- [ ] Add fairness_balance table migration
- [ ] Add API endpoint to view fairness balances
- [ ] Test randomization in staging

### Week 2: Gradual Rollout
- [ ] Deploy randomization fix (Priority 1)
- [ ] Initialize fairness balances at 0 for all members
- [ ] Monitor for any issues

### Week 3: Core Features
- [ ] Deploy fairness balance tracking
- [ ] Enable absence compensation
- [ ] Add fairness to ASCII schedule output

### Week 4: Polish
- [ ] Unify scoring algorithms
- [ ] Add smart missed task redistribution
- [ ] Create family dashboard showing fairness stats

---

## Monitoring

Add these metrics to track fairness over time:

```python
def log_fairness_metrics():
    """Log fairness metrics for monitoring."""
    balances = db.get_all_fairness_balances()

    metrics = {
        "max_balance": max(balances.values()),
        "min_balance": min(balances.values()),
        "range": max(balances.values()) - min(balances.values()),
        "std_dev": statistics.stdev(balances.values()) if len(balances) > 1 else 0
    }

    # Alert if imbalance is too high
    if metrics["range"] > 5.0:
        log.warning(f"High fairness imbalance detected: {metrics}")

    return metrics
```
