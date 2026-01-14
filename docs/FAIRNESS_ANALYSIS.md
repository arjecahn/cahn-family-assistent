# Deep Fairness Analysis: Cahn Family Task Distribution Algorithm

**Date:** January 2026
**Author:** Algorithm Analysis
**Version:** 1.0

---

## Executive Summary

This document provides a comprehensive analysis of the task distribution algorithm used in the Cahn Family Assistant. After thorough examination, I've identified **several fairness issues** that could cause frustration among the children (Nora, Linde, and Fenna). While the current implementation has solid foundations, there are specific scenarios where the algorithm may produce outcomes that feel unfair.

**Key Findings:**
1. The weekly reset causes "unfairness amnesia" - past extra work isn't remembered
2. Absence compensation is non-existent - whoever was absent doesn't "pay back"
3. Equal-count tie-breaking is deterministic and biased toward first-in-list
4. Month boundary transitions can create fairness spikes
5. Task variety isn't guaranteed - someone could always get the same task

---

## Table of Contents

1. [Current Algorithm Deep Dive](#1-current-algorithm-deep-dive)
2. [Scenario Analysis](#2-scenario-analysis)
3. [Identified Fairness Issues](#3-identified-fairness-issues)
4. [Recommendations for Improvement](#4-recommendations-for-improvement)
5. [Implementation Priority](#5-implementation-priority)

---

## 1. Current Algorithm Deep Dive

### 1.1 The Weighted Score Formula

The core algorithm in `task_engine.py` (lines 105-141) calculates a **weighted score** where **lower = more aan de beurt** (more likely to be assigned):

```
weighted_score = (total_tasks / max_total) × 0.50
              + (specific_tasks / max_specific) × 0.30
              + (1 - recency_score) × 0.20
```

**Components:**
| Weight | Factor | Purpose |
|--------|--------|---------|
| 50% | Total tasks this week | Balance overall workload |
| 30% | This specific task count | Prevent repetition |
| 20% | Recency | Spread out same-task assignments |

### 1.2 Member Selection Logic

The `_select_member_for_task()` method (lines 1493-1527) uses a different approach for schedule generation:

```python
def sort_key(m):
    month_task_count = member_month_task_counts[m.name].get(task_name, 0)
    week_count = member_week_counts.get(m.name, 0)
    return (month_task_count, week_count)  # Primary: monthly, Secondary: weekly
```

**Observation:** There are actually TWO different algorithms:
1. **`calculate_weighted_score()`** - Used for ad-hoc suggestions (`/api/suggest/{task}`)
2. **`_select_member_for_task()`** - Used for weekly schedule generation

This inconsistency could lead to confusion when the suggestion doesn't match the schedule.

### 1.3 Schedule Generation

The schedule is generated once per week and persisted. Key constraints:
- Time slots: ochtend, middag, avond (mutually exclusive per person per day)
- Some tasks block multiple slots (koken blocks avond + middag)
- Maximum 5 tasks per day total
- Spacing rules (karton: 2 days, glas: 5 days between occurrences)
- Weekday-only tasks (uitruimen_ochtend)

---

## 2. Scenario Analysis

### 2.1 Scenario: Day 1 of Week 1 (Fresh Start)

**Setup:**
- All members have 0 completions
- No history

**What happens:**
```
All members have:
- total_tasks = 0
- specific_tasks = 0
- recency_score = 1.0 (never done)

Weighted score for everyone:
= (0/1)*0.5 + (0/1)*0.3 + (1-1.0)*0.2 = 0.0
```

**Problem:** When all scores are equal, selection falls back to list ordering. Looking at `_select_member_for_task()`:

```python
sorted_eligible = sorted(eligible, key=sort_key)
return sorted_eligible[0]  # ALWAYS returns first when equal
```

**Result:** The first member in the database (likely Nora, if alphabetically inserted) will ALWAYS be picked first on Day 1. This creates a **consistent first-pick bias**.

**Fairness Impact:** 🔴 **HIGH** - Children will notice "Nora always goes first"

---

### 2.2 Scenario: End of Week 1

**Setup after 7 days:**
- Assuming perfect task completion
- 3 evening tasks × 7 days = 21 assignments
- Ideally: 7 tasks per child

**Expected distribution:**
```
uitruimen_avond: 7 total → 2-3 per child
inruimen: 7 total → 2-3 per child
dekken: 7 total → 2-3 per child
uitruimen_ochtend: 3 total → 1 per child
karton_papier: 2 total → distributed with spacing
glas: 1 total → 1 child
koken: 1 total → 1 child (rotates monthly)
```

**Actual behavior:**
The algorithm distributes well within a week because:
1. `member_week_counts` is tracked per assignment
2. After each assignment, counts update before next assignment

**Fairness Impact:** 🟢 **LOW** - Within a single week, distribution is generally fair

---

### 2.3 Scenario: Week 3 (Cumulative Effects)

**Setup:**
- Week 1: Nora did 8 tasks, Linde did 7, Fenna did 6 (due to first-pick bias)
- Week 2: Same pattern repeats
- Week 3: Starting fresh with weekly counts at 0

**Problem:** The algorithm **completely forgets** weeks 1 and 2!

```python
def get_task_count_this_week(self, member: Member, task: Optional[Task] = None) -> int:
    week_number = self.get_current_week()  # Only current week!
    completions = db.get_completions_for_member(member.id, week_number)
    return len(completions)
```

**Cumulative imbalance after 3 weeks:**
```
Nora: 8 + 8 + 8 = 24 tasks
Fenna: 6 + 6 + 6 = 18 tasks
Difference: 6 tasks (33% more work for Nora!)
```

**Mitigation exists but is weak:** The monthly counting in `_select_member_for_task()` does track monthly totals, but only for schedule generation, not for ad-hoc suggestions.

**Fairness Impact:** 🟡 **MEDIUM** - Monthly tracking helps, but first-pick bias accumulates

---

### 2.4 Scenario: Month Transition (e.g., January 31 → February 1)

**Setup:**
- January: Nora did 35 tasks, Linde 30, Fenna 30
- February 1: New month starts

**Problem:** Monthly completions counter resets!

```python
def _get_monthly_task_stats(self, ...):
    completions = db.get_completions_for_month(year, month)  # Only current month!
```

On February 1:
- All `month_completions` = empty
- All `member_month_task_counts` = 0
- Nora's 5-task surplus is forgotten

**Fairness Impact:** 🔴 **HIGH** - Cross-month imbalances are never corrected

---

### 2.5 Scenario: One Week Vacation

**Setup:**
- Week 5: Nora is on vacation (absence registered)
- Linde and Fenna cover all tasks

**Week 5 task distribution:**
```
Normal week: 21 evening tasks ÷ 3 = 7 per child
Vacation week: 21 evening tasks ÷ 2 = 10.5 per child
Linde: 10-11 tasks
Fenna: 10-11 tasks
Nora: 0 tasks
```

**Week 6 - Nora returns:**
```python
# What happens in _select_member_for_task():
nora_month_count = 0  # She did nothing last week
linde_month_count = 10
fenna_month_count = 10

# Sort key: (month_count, week_count)
# Nora has LOWEST month_count → Nora gets picked first!
```

**Result:** Nora gets favorable treatment in week 6 because she did "less" in week 5 - despite being absent!

**The unfairness:** Linde and Fenna worked EXTRA hard while Nora was on vacation. Now, not only is there no compensation, but Nora gets priority because her counts are lower.

**Fairness Impact:** 🔴 **CRITICAL** - This will cause significant frustration

---

### 2.6 Scenario: Missed Tasks and Rescheduling

**Setup:**
- Monday: Nora assigned "uitruimen avond" but doesn't do it
- System detects missed task and reschedules

**Current behavior (lines 1008-1265):**
```python
# Task stays assigned to same person
schedule[target_day_name]["tasks"].append({
    "task_name": task_name,
    "assigned_to": original_member,  # Still Nora!
    ...
})
```

**Problem:** If Nora missed Monday's task, she gets it again on Tuesday. But what if:
- Nora already has 2 other tasks on Tuesday
- Linde has 0 tasks on Tuesday

The system doesn't redistribute based on Tuesday's load - it just moves the task.

**Fairness Impact:** 🟡 **MEDIUM** - Missed task stays with same person regardless of new day's balance

---

### 2.7 Scenario: Task Variety

**Setup:**
- Fenna somehow always gets "glas" (glass recycling)
- Over months, this becomes "Fenna's job"

**Current algorithm checks:**
1. Monthly count per task type ✓
2. Recency ✓

**But:** If all three children have done "glas" 1x this month, and it's time for assignment #2, the algorithm picks based on weekly count - which could consistently favor/disfavor the same person.

**Fairness Impact:** 🟡 **MEDIUM** - Could create task "ownership" patterns

---

## 3. Identified Fairness Issues

### 3.1 Critical Issues (Require Immediate Attention)

| Issue | Description | Impact |
|-------|-------------|--------|
| **Absence Debt** | No compensation mechanism when someone is absent | Siblings who worked extra get no credit |
| **Cross-Month Amnesia** | Monthly stats reset, losing fairness context | Long-term imbalances never corrected |
| **First-Pick Bias** | Equal scores always favor first-in-list | Systematic bias toward one child |

### 3.2 Moderate Issues

| Issue | Description | Impact |
|-------|-------------|--------|
| **Missed Task Stickiness** | Missed tasks stay with same person | No load redistribution |
| **Two Algorithms** | Suggestions vs. schedule use different logic | Confusing behavior |
| **Task Type Fairness** | No guaranteed variety per child | Some may always get "undesirable" tasks |

### 3.3 Minor Issues

| Issue | Description | Impact |
|-------|-------------|--------|
| **Recency Weight Too Low** | 20% weight may not prevent immediate repeats | Same task could repeat quickly |
| **Koken Blocking** | Koken blocks other tasks but counts as 1 task | May feel like "easy day" |

---

## 4. Recommendations for Improvement

### 4.1 Implement Long-Term Fairness Tracking

**Create a new `fairness_balance` table:**

```sql
CREATE TABLE fairness_balance (
    id SERIAL PRIMARY KEY,
    member_id INTEGER REFERENCES members(id),
    member_name VARCHAR(50),
    balance FLOAT DEFAULT 0.0,  -- Positive = owes tasks, Negative = owed tasks
    last_updated TIMESTAMPTZ
);
```

**Algorithm change:**
```python
def calculate_fairness_adjusted_score(member, task, available_members):
    base_score = calculate_weighted_score(member, task, available_members)
    fairness_balance = get_fairness_balance(member)

    # Adjust: positive balance = they owe tasks, so LOWER their score
    # (lower score = more likely to be picked)
    adjusted_score = base_score - (fairness_balance * 0.1)
    return adjusted_score
```

**Update balance on:**
- Completion: `balance -= 1` (they did work)
- Absence: siblings get `balance -= extra_load` (they did extra)
- Week end: Normalize to prevent drift

### 4.2 Fix First-Pick Bias

**Add randomization for equal scores:**

```python
def _select_member_for_task(self, task, available_members, ...):
    # ... existing logic ...

    # Group by equal scores
    score_groups = {}
    for m in eligible:
        score = sort_key(m)
        if score not in score_groups:
            score_groups[score] = []
        score_groups[score].append(m)

    # Get lowest score group
    min_score = min(score_groups.keys())
    candidates = score_groups[min_score]

    # Random selection among equals
    import random
    return random.choice(candidates)
```

### 4.3 Implement Absence Compensation

**When absence ends:**
```python
def on_absence_end(absent_member, absence_duration_days):
    # Calculate extra load siblings carried
    siblings = [m for m in get_all_members() if m.id != absent_member.id]
    avg_daily_tasks = 7  # evening tasks per day
    extra_per_sibling = (avg_daily_tasks / 2) - (avg_daily_tasks / 3)  # 3.5 - 2.33 = 1.17

    for sibling in siblings:
        update_fairness_balance(sibling.id, -extra_per_sibling * absence_duration_days)

    # Absent member owes tasks
    update_fairness_balance(absent_member.id, +extra_per_sibling * absence_duration_days * 2)
```

### 4.4 Unify the Two Algorithms

**Use single scoring method everywhere:**

```python
def get_unified_score(member, task, context):
    """Single source of truth for task assignment scoring."""
    week_count = self.get_task_count_this_week(member)
    month_task_count = self.get_monthly_task_count(member, task)
    fairness_balance = self.get_fairness_balance(member)
    recency_days = self.get_days_since_last(member, task)

    # Weighted combination
    score = (
        week_count * 0.30 +          # Weekly balance
        month_task_count * 0.25 +    # Task-specific monthly
        fairness_balance * 0.25 +     # Long-term fairness
        (7 - recency_days) * 0.10 +  # Recency penalty
        random.random() * 0.10        # Tie-breaker
    )
    return score
```

### 4.5 Add Task Variety Guarantee

**Track task type distribution:**

```python
# Ensure each child does similar mix of task types
TASK_CATEGORIES = {
    "dishes": ["uitruimen_ochtend", "uitruimen_avond", "inruimen"],
    "table": ["dekken"],
    "recycling": ["karton_papier", "glas"],
    "cooking": ["koken"]
}

def ensure_variety(member, proposed_task, week_assignments):
    category = get_category(proposed_task)
    member_category_counts = count_categories(member, week_assignments)

    # Check if this would over-assign one category
    if member_category_counts[category] > expected_per_category:
        return find_alternative_member(proposed_task)
    return member
```

### 4.6 Smart Missed Task Redistribution

**When rescheduling missed tasks:**

```python
def reschedule_missed_task(missed_task, original_member, target_day):
    target_day_loads = get_member_loads_for_day(target_day)

    # Find member with lowest load on target day
    # (respecting time slot constraints)
    best_member = min(
        available_members,
        key=lambda m: (
            target_day_loads[m.name],
            get_fairness_balance(m)
        )
    )

    if best_member != original_member:
        # Update fairness: original_member got a "pass"
        update_fairness_balance(original_member.id, +0.5)
        update_fairness_balance(best_member.id, -0.5)

    return assign_task(best_member, missed_task, target_day)
```

---

## 5. Implementation Priority

### Phase 1: Quick Wins (1-2 days)

1. **Add randomization for equal scores** - Simple fix, big perception impact
2. **Log fairness metrics** - Add visibility before making changes

### Phase 2: Core Fairness (3-5 days)

3. **Implement fairness_balance table** - Foundation for long-term tracking
4. **Unify scoring algorithms** - Consistency
5. **Add absence compensation** - Major frustration point

### Phase 3: Polish (2-3 days)

6. **Task variety tracking** - Prevent monotony
7. **Smart missed task redistribution** - Edge case handling
8. **Add fairness dashboard** - Transparency for the family

---

## 6. Testing Recommendations

Create a simulation framework to test fairness over extended periods:

```python
def simulate_weeks(num_weeks, absence_pattern=None):
    """Simulate task distribution over multiple weeks."""
    results = {member: {"total": 0, "by_task": {}} for member in MEMBERS}

    for week in range(num_weeks):
        week_schedule = generate_schedule(absence_pattern.get(week, []))
        for day, tasks in week_schedule.items():
            for task in tasks:
                member = task["assigned_to"]
                results[member]["total"] += 1
                results[member]["by_task"][task["name"]] = \
                    results[member]["by_task"].get(task["name"], 0) + 1

    return calculate_fairness_metrics(results)
```

**Test scenarios:**
1. 12 weeks with no absences - should be perfectly balanced
2. 12 weeks with 1 week absence per child (staggered)
3. 12 weeks with varied absences (one child absent 3x more)
4. Month boundary transitions (test December → January)

---

## 7. Conclusion

The current algorithm is **functionally correct** but has **fairness blind spots** that will become apparent over time. Children are perceptive about fairness, and systematic biases (like first-pick advantage) will be noticed and resented.

**The most impactful changes are:**
1. Randomizing equal-score selections (eliminates consistent bias)
2. Implementing long-term fairness tracking (prevents cumulative drift)
3. Adding absence compensation (addresses vacation unfairness)

These changes will transform the system from "fair within a week" to "fair over months and years."

---

## Appendix A: Current Task Distribution Matrix

| Task | Frequency | Time Slot | Notes |
|------|-----------|-----------|-------|
| uitruimen_ochtend | 3x/week | ochtend | Weekdays only |
| uitruimen_avond | 7x/week | avond | Daily |
| inruimen | 7x/week | avond | Daily |
| dekken | 7x/week | avond | Daily |
| karton_papier | 2x/week | middag | 2-day spacing |
| glas | 1x/week | middag | 5-day spacing |
| koken | 1x/week | avond | Blocks middag too |

## Appendix B: Fairness Metrics to Track

```python
FAIRNESS_METRICS = {
    "gini_coefficient": "Measure of inequality (0 = perfect equality)",
    "max_min_ratio": "Ratio between highest and lowest task counts",
    "task_variety_score": "How evenly distributed task types are per child",
    "absence_compensation_balance": "How well extra work is compensated",
    "week_over_week_consistency": "Stability of assignments"
}
```
