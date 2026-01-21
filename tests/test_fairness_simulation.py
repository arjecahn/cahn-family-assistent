"""
3-Maanden Fairness Simulatie Test

Dit script simuleert 12 weken (3 maanden) van taaktoewijzingen
en rapporteert over de eerlijkheid van de verdeling.

Gebruiken:
    python tests/test_fairness_simulation.py

Vereist:
    - DATABASE_URL environment variable
    - Of: .env bestand met DATABASE_URL
"""
import os
import sys
from datetime import date, timedelta
from collections import defaultdict

# Voeg project root toe aan path EERST
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Laad .env VOORDAT we database importeren
from dotenv import load_dotenv
env_path = os.path.join(project_root, '.env')
load_dotenv(env_path, override=True)

# Debug: print DATABASE_URL (verberg wachtwoord)
db_url = os.getenv("DATABASE_URL", "")
if db_url:
    # Mask password in URL for display
    import re
    masked = re.sub(r':([^:@]+)@', ':****@', db_url)
    print(f"✓ DATABASE_URL geladen: {masked}")

# Nu pas importeren - maar we moeten DATABASE_URL direct in os.environ zetten
# zodat database.py het oppikt
from src import database as db
# Force refresh van DATABASE_URL in de database module
db.DATABASE_URL = db.get_database_url()

from src.task_engine import TaskEngine


def print_table(headers: list, rows: list, title: str = None):
    """Print een nette ASCII tabel."""
    if title:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
    
    # Bereken kolom breedtes
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    
    # Header
    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    separator = "-+-".join("-" * w for w in widths)
    
    print(f"\n{header_line}")
    print(separator)
    
    # Rows
    for row in rows:
        row_line = " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        print(row_line)


def run_3_month_simulation():
    """Simuleer 3 maanden van taaktoewijzingen en analyseer fairness."""
    
    print("\n" + "🔬 3-MAANDEN FAIRNESS SIMULATIE ".center(60, "="))
    print("Simuleert 12 weken van taaktoewijzingen\n")
    
    # Check database
    if not db.DATABASE_URL:
        print("❌ ERROR: Geen DATABASE_URL gevonden!")
        print("   Zet DATABASE_URL in je environment of .env bestand")
        return
    
    print(f"✓ Database verbonden")
    
    # Init
    engine = TaskEngine()
    
    # Haal members en tasks op
    members = db.get_all_members()
    tasks = db.get_all_tasks()
    
    if not members:
        print("❌ ERROR: Geen gezinsleden gevonden in database!")
        print("   Run eerst: curl -X GET http://localhost:8080/api/init")
        return
    
    print(f"✓ Gezinsleden: {', '.join(m.name for m in members)}")
    print(f"✓ Taken: {len(tasks)} taken geconfigureerd")
    
    # === SIMULATIE ===
    # We beginnen vandaag en gaan 12 weken terug kijken naar bestaande data
    # OF we simuleren 12 weken vooruit met suggesties
    
    today = db.today_local()
    simulation_weeks = 12
    
    # Tracking per week per persoon
    weekly_completions = defaultdict(lambda: defaultdict(int))  # week -> member -> count
    task_completions = defaultdict(lambda: defaultdict(int))     # task -> member -> count
    total_per_member = defaultdict(int)
    
    print(f"\n📊 Analyseren van data vanaf week {today.isocalendar()[1] - simulation_weeks + 1}...")
    
    # Haal alle completions op voor de simulatieperiode
    for week_offset in range(simulation_weeks):
        # Bereken de week
        check_date = today - timedelta(weeks=simulation_weeks - week_offset - 1)
        week_num = check_date.isocalendar()[1]
        year = check_date.isocalendar()[0]
        
        # Haal completions voor deze week
        week_completions = db.get_completions_for_week(week_num)
        
        for completion in week_completions:
            member_name = completion.member_name
            task_name = completion.task_name
            
            weekly_completions[week_num][member_name] += 1
            task_completions[task_name][member_name] += 1
            total_per_member[member_name] += 1
    
    # === RAPPORTAGE ===
    
    # 1. Totaal overzicht per persoon
    print_table(
        headers=["Naam", "Totaal", "Per Week (gem)", "% van Totaal"],
        rows=[
            [
                name,
                count,
                f"{count / simulation_weeks:.1f}",
                f"{count / max(sum(total_per_member.values()), 1) * 100:.1f}%"
            ]
            for name, count in sorted(total_per_member.items())
        ],
        title="TOTAAL OVERZICHT (12 WEKEN)"
    )
    
    # Bereken verwachte verdeling
    total_tasks = sum(total_per_member.values())
    expected_per_member = total_tasks / len(members) if members else 0
    
    print(f"\n📈 Statistieken:")
    print(f"   Totaal taken: {total_tasks}")
    print(f"   Verwacht per kind (eerlijk): {expected_per_member:.1f}")
    
    # Check fairness
    if total_tasks > 0:
        max_diff = max(abs(count - expected_per_member) for count in total_per_member.values())
        fairness_score = 100 - (max_diff / expected_per_member * 100) if expected_per_member else 100
        
        print(f"   Fairness score: {fairness_score:.1f}%", end="")
        if fairness_score >= 90:
            print(" ✅ Uitstekend!")
        elif fairness_score >= 75:
            print(" ⚠️ Redelijk")
        else:
            print(" ❌ Ongelijk verdeeld!")
    
    # 2. Per-week breakdown
    if weekly_completions:
        week_rows = []
        for week_num in sorted(weekly_completions.keys()):
            week_data = weekly_completions[week_num]
            row = [f"Week {week_num}"]
            for member in members:
                row.append(str(week_data.get(member.name, 0)))
            week_rows.append(row)
        
        print_table(
            headers=["Week"] + [m.name for m in members],
            rows=week_rows,
            title="TAKEN PER WEEK PER PERSOON"
        )
    
    # 3. Per-taak verdeling
    if task_completions:
        task_rows = []
        for task_name in sorted(task_completions.keys()):
            task_data = task_completions[task_name]
            row = [task_name[:25]]  # Truncate long names
            for member in members:
                row.append(str(task_data.get(member.name, 0)))
            task_rows.append(row)
        
        print_table(
            headers=["Taak"] + [m.name for m in members],
            rows=task_rows,
            title="TAKEN VERDELING PER TYPE"
        )
    
    # 4. Fairness analyse per taak
    print("\n" + "="*60)
    print("  FAIRNESS ANALYSE PER TAAK")
    print("="*60)
    
    for task in tasks:
        task_data = task_completions.get(task.display_name, {})
        total = sum(task_data.values())
        if total == 0:
            continue
        
        expected = total / len(members)
        max_deviation = max(abs(task_data.get(m.name, 0) - expected) for m in members) if members else 0
        fairness = 100 - (max_deviation / max(expected, 1) * 100)
        
        status = "✅" if fairness >= 80 else "⚠️" if fairness >= 60 else "❌"
        print(f"   {task.display_name}: {fairness:.0f}% {status}")
        
        # Toon details als er onbalans is
        if fairness < 80:
            for member in members:
                count = task_data.get(member.name, 0)
                diff = count - expected
                diff_str = f"+{diff:.0f}" if diff > 0 else f"{diff:.0f}"
                print(f"      {member.name}: {count} ({diff_str})")
    
    print("\n" + "="*60)
    print("  SIMULATIE VOLTOOID")
    print("="*60 + "\n")


if __name__ == "__main__":
    run_3_month_simulation()
