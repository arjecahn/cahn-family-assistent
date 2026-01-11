"""PostgreSQL database voor de Cahn Family Task Assistant (Vercel Postgres)."""
import os
from datetime import date, datetime
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor

from .models import Member, Task, Completion, Absence, Swap

# Database URL van Vercel (automatisch beschikbaar als POSTGRES_URL)
DATABASE_URL = os.getenv("POSTGRES_URL", os.getenv("DATABASE_URL", ""))


def get_db():
    """Maak een database connectie."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn


def init_db():
    """Maak de database tabellen aan."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50) UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            display_name VARCHAR(100),
            weekly_target INTEGER DEFAULT 0,
            time_of_day VARCHAR(20)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS completions (
            id SERIAL PRIMARY KEY,
            task_id INTEGER REFERENCES tasks(id),
            member_id INTEGER REFERENCES members(id),
            member_name VARCHAR(50),
            task_name VARCHAR(100),
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            week_number INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS absences (
            id SERIAL PRIMARY KEY,
            member_id INTEGER REFERENCES members(id),
            member_name VARCHAR(50),
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            reason TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS swaps (
            id SERIAL PRIMARY KEY,
            requester_id INTEGER REFERENCES members(id),
            requester_name VARCHAR(50),
            target_id INTEGER REFERENCES members(id),
            target_name VARCHAR(50),
            task_id INTEGER REFERENCES tasks(id),
            task_name VARCHAR(100),
            swap_date DATE,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


def seed_initial_data():
    """Vul de database met initiele data als die leeg is."""
    if not DATABASE_URL:
        print("Geen DATABASE_URL gevonden, skip seeding")
        return

    init_db()
    conn = get_db()
    cur = conn.cursor()

    # Check of er al members zijn
    cur.execute("SELECT COUNT(*) as count FROM members")
    if cur.fetchone()["count"] > 0:
        cur.close()
        conn.close()
        return

    # Gezinsleden toevoegen
    members = ["Nora", "Linde", "Fenna"]
    for name in members:
        cur.execute("INSERT INTO members (name) VALUES (%s)", (name,))

    # Taken toevoegen
    tasks = [
        ("uitruimen_ochtend", "uitruimen voor school", 3, "ochtend"),
        ("uitruimen_avond", "uitruimen", 6, "avond"),
        ("inruimen", "inruimen", 6, "avond"),
        ("dekken", "dekken", 6, "avond"),
        ("karton_papier", "karton en papier wegbrengen", 3, "middag"),
        ("glas", "glas wegbrengen", 1, "middag"),
    ]
    for name, display_name, target, time in tasks:
        cur.execute(
            "INSERT INTO tasks (name, display_name, weekly_target, time_of_day) VALUES (%s, %s, %s, %s)",
            (name, display_name, target, time)
        )

    conn.commit()
    cur.close()
    conn.close()
    print("Database gevuld met gezinsleden en taken!")


# CRUD operaties voor Members
def get_all_members() -> list[Member]:
    """Haal alle gezinsleden op."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM members")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [Member(id=str(r["id"]), name=r["name"]) for r in rows]


def get_member_by_name(name: str) -> Optional[Member]:
    """Zoek een gezinslid op naam."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM members WHERE LOWER(name) = LOWER(%s)", (name,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return Member(id=str(row["id"]), name=row["name"])
    return None


# CRUD operaties voor Tasks
def get_all_tasks() -> list[Task]:
    """Haal alle taken op."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, display_name, weekly_target, time_of_day FROM tasks")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [Task(id=str(r["id"]), name=r["name"], display_name=r["display_name"] or r["name"],
                 weekly_target=r["weekly_target"] or 0, time_of_day=r["time_of_day"] or "") for r in rows]


def get_task_by_name(name: str) -> Optional[Task]:
    """Zoek een taak op naam of display_name."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, display_name, weekly_target, time_of_day FROM tasks
        WHERE LOWER(name) = LOWER(%s) OR LOWER(display_name) LIKE LOWER(%s)
    """, (name, f"%{name}%"))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return Task(id=str(row["id"]), name=row["name"], display_name=row["display_name"] or row["name"],
                   weekly_target=row["weekly_target"] or 0, time_of_day=row["time_of_day"] or "")
    return None


# CRUD operaties voor Completions
def get_completions_for_member(member_id: str, week_number: int) -> list[Completion]:
    """Haal voltooide taken op voor een lid in een specifieke week."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, task_id, member_id, member_name, task_name, completed_at, week_number
        FROM completions WHERE member_id = %s AND week_number = %s
    """, (int(member_id), week_number))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [Completion(id=str(r["id"]), task_id=str(r["task_id"]), member_id=str(r["member_id"]),
                       member_name=r["member_name"], task_name=r["task_name"],
                       completed_at=r["completed_at"], week_number=r["week_number"]) for r in rows]


def add_completion(completion_data: dict) -> Completion:
    """Voeg een voltooide taak toe."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO completions (task_id, member_id, member_name, task_name, week_number, completed_at)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (
        int(completion_data["task_id"]),
        int(completion_data["member_id"]),
        completion_data["member_name"],
        completion_data["task_name"],
        completion_data["week_number"],
        datetime.utcnow()
    ))
    new_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return Completion(id=str(new_id), completed_at=datetime.utcnow(), **completion_data)


def get_last_completion_for_task(member_id: str, task_id: str) -> Optional[Completion]:
    """Wanneer deed dit lid deze taak voor het laatst?"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, task_id, member_id, member_name, task_name, completed_at, week_number
        FROM completions WHERE member_id = %s AND task_id = %s
        ORDER BY completed_at DESC LIMIT 1
    """, (int(member_id), int(task_id)))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return Completion(id=str(row["id"]), task_id=str(row["task_id"]), member_id=str(row["member_id"]),
                         member_name=row["member_name"], task_name=row["task_name"],
                         completed_at=row["completed_at"], week_number=row["week_number"])
    return None


# CRUD operaties voor Absences
def get_absence_for_date(member_id: str, check_date: date) -> Optional[Absence]:
    """Check of een lid afwezig is op een bepaalde datum."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, member_id, member_name, start_date, end_date, reason
        FROM absences WHERE member_id = %s AND start_date <= %s AND end_date >= %s
    """, (int(member_id), check_date, check_date))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return Absence(id=str(row["id"]), member_id=str(row["member_id"]), member_name=row["member_name"],
                      start_date=row["start_date"], end_date=row["end_date"], reason=row["reason"])
    return None


def add_absence(absence_data: dict) -> Absence:
    """Registreer afwezigheid."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO absences (member_id, member_name, start_date, end_date, reason)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
    """, (
        int(absence_data["member_id"]),
        absence_data["member_name"],
        absence_data["start_date"],
        absence_data["end_date"],
        absence_data.get("reason")
    ))
    new_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return Absence(id=str(new_id), **absence_data)


# CRUD operaties voor Swaps
def add_swap(swap_data: dict) -> Swap:
    """Maak een ruil verzoek aan."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO swaps (requester_id, requester_name, target_id, target_name, task_id, task_name, swap_date, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (
        int(swap_data["requester_id"]),
        swap_data["requester_name"],
        int(swap_data["target_id"]),
        swap_data["target_name"],
        int(swap_data["task_id"]),
        swap_data["task_name"],
        swap_data["swap_date"],
        swap_data.get("status", "pending")
    ))
    new_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return Swap(id=str(new_id), created_at=datetime.utcnow(), **swap_data)


def get_pending_swaps_for_member(member_id: str) -> list[Swap]:
    """Haal openstaande ruil verzoeken op voor een lid."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, requester_id, requester_name, target_id, target_name, task_id, task_name, swap_date, status, created_at
        FROM swaps WHERE target_id = %s AND status = 'pending'
    """, (int(member_id),))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [Swap(id=str(r["id"]), requester_id=str(r["requester_id"]), requester_name=r["requester_name"],
                 target_id=str(r["target_id"]), target_name=r["target_name"], task_id=str(r["task_id"]),
                 task_name=r["task_name"], swap_date=r["swap_date"], status=r["status"],
                 created_at=r["created_at"]) for r in rows]


def update_swap_status(swap_id: str, status: str):
    """Update de status van een ruil verzoek."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE swaps SET status = %s WHERE id = %s", (status, int(swap_id)))
    conn.commit()
    cur.close()
    conn.close()
