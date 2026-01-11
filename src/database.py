"""PostgreSQL database voor de Cahn Family Task Assistant (Vercel Postgres)."""
import os
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo
import psycopg2
from psycopg2.extras import RealDictCursor

from .models import Member, Task, Completion, Absence, Swap, ScheduleAssignment

# Timezone voor de familie (Nederland)
TIMEZONE = ZoneInfo("Europe/Amsterdam")


def now_local() -> datetime:
    """Geef huidige tijd in lokale timezone."""
    return datetime.now(TIMEZONE)


def today_local() -> date:
    """Geef huidige datum in lokale timezone."""
    return datetime.now(TIMEZONE).date()

# Database URL - Supabase/Vercel zetten verschillende variabelen
def get_database_url():
    """Haal de database URL op en clean eventuele ongeldige parameters."""
    url = (
        os.getenv("POSTGRES_URL") or
        os.getenv("DATABASE_URL") or
        os.getenv("SUPABASE_DB_URL") or
        os.getenv("POSTGRES_URL_NON_POOLING") or
        ""
    )
    # Verwijder ongeldige query parameters voor psycopg2
    if "?" in url:
        base_url = url.split("?")[0]
        params = url.split("?")[1] if "?" in url else ""
        # Filter alleen geldige psycopg2 parameters
        valid_params = []
        for param in params.split("&"):
            key = param.split("=")[0] if "=" in param else param
            if key in ["sslmode", "connect_timeout", "application_name"]:
                valid_params.append(param)
        if valid_params:
            url = base_url + "?" + "&".join(valid_params)
        else:
            url = base_url + "?sslmode=require"
    return url

DATABASE_URL = get_database_url()


def get_db():
    """Maak een database connectie."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor, sslmode='require')
    return conn


def init_db():
    """Maak de database tabellen aan met CASCADE DELETE constraints."""
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
            description TEXT,
            weekly_target INTEGER DEFAULT 0,
            per_child_target INTEGER DEFAULT 0,
            rotation_weeks INTEGER DEFAULT 1,
            time_of_day VARCHAR(20)
        )
    """)

    # Completions met CASCADE DELETE (als task of member wordt verwijderd, ook completions verwijderen)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS completions (
            id SERIAL PRIMARY KEY,
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
            member_id INTEGER REFERENCES members(id) ON DELETE CASCADE,
            member_name VARCHAR(50),
            task_name VARCHAR(100),
            completed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            week_number INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS absences (
            id SERIAL PRIMARY KEY,
            member_id INTEGER REFERENCES members(id) ON DELETE CASCADE,
            member_name VARCHAR(50),
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            reason TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS swaps (
            id SERIAL PRIMARY KEY,
            requester_id INTEGER REFERENCES members(id) ON DELETE CASCADE,
            requester_name VARCHAR(50),
            target_id INTEGER REFERENCES members(id) ON DELETE CASCADE,
            target_name VARCHAR(50),
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
            task_name VARCHAR(100),
            swap_date DATE,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Schedule assignments - persistent weekrooster
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schedule_assignments (
            id SERIAL PRIMARY KEY,
            week_number INTEGER NOT NULL,
            year INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
            task_name VARCHAR(100),
            member_id INTEGER REFERENCES members(id) ON DELETE CASCADE,
            member_name VARCHAR(50),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(week_number, year, day_of_week, task_id)
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


def migrate_add_cascade_delete():
    """Migratie: voeg CASCADE DELETE toe aan bestaande foreign keys.

    Dit is nodig voor databases die zijn aangemaakt voordat CASCADE werd toegevoegd.
    Veilig om meerdere keren uit te voeren.
    """
    conn = get_db()
    cur = conn.cursor()

    try:
        # Completions table
        # Drop oude constraints en maak nieuwe met CASCADE
        cur.execute("""
            DO $$
            BEGIN
                -- completions.task_id
                IF EXISTS (SELECT 1 FROM information_schema.table_constraints
                           WHERE constraint_name = 'completions_task_id_fkey') THEN
                    ALTER TABLE completions DROP CONSTRAINT completions_task_id_fkey;
                END IF;
                ALTER TABLE completions
                    ADD CONSTRAINT completions_task_id_fkey
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE;

                -- completions.member_id
                IF EXISTS (SELECT 1 FROM information_schema.table_constraints
                           WHERE constraint_name = 'completions_member_id_fkey') THEN
                    ALTER TABLE completions DROP CONSTRAINT completions_member_id_fkey;
                END IF;
                ALTER TABLE completions
                    ADD CONSTRAINT completions_member_id_fkey
                    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE;

                -- absences.member_id
                IF EXISTS (SELECT 1 FROM information_schema.table_constraints
                           WHERE constraint_name = 'absences_member_id_fkey') THEN
                    ALTER TABLE absences DROP CONSTRAINT absences_member_id_fkey;
                END IF;
                ALTER TABLE absences
                    ADD CONSTRAINT absences_member_id_fkey
                    FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE;

                -- swaps.requester_id
                IF EXISTS (SELECT 1 FROM information_schema.table_constraints
                           WHERE constraint_name = 'swaps_requester_id_fkey') THEN
                    ALTER TABLE swaps DROP CONSTRAINT swaps_requester_id_fkey;
                END IF;
                ALTER TABLE swaps
                    ADD CONSTRAINT swaps_requester_id_fkey
                    FOREIGN KEY (requester_id) REFERENCES members(id) ON DELETE CASCADE;

                -- swaps.target_id
                IF EXISTS (SELECT 1 FROM information_schema.table_constraints
                           WHERE constraint_name = 'swaps_target_id_fkey') THEN
                    ALTER TABLE swaps DROP CONSTRAINT swaps_target_id_fkey;
                END IF;
                ALTER TABLE swaps
                    ADD CONSTRAINT swaps_target_id_fkey
                    FOREIGN KEY (target_id) REFERENCES members(id) ON DELETE CASCADE;

                -- swaps.task_id
                IF EXISTS (SELECT 1 FROM information_schema.table_constraints
                           WHERE constraint_name = 'swaps_task_id_fkey') THEN
                    ALTER TABLE swaps DROP CONSTRAINT swaps_task_id_fkey;
                END IF;
                ALTER TABLE swaps
                    ADD CONSTRAINT swaps_task_id_fkey
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE;
            END $$;
        """)

        conn.commit()
        print("CASCADE DELETE constraints toegevoegd!")

    except Exception as e:
        conn.rollback()
        print(f"Migratie fout: {e}")
        raise e

    finally:
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

    # Taken toevoegen (afspraken 2026)
    # Format: (name, display_name, description, weekly_target, per_child_target, rotation_weeks, time_of_day)
    tasks = [
        (
            "uitruimen_ochtend",
            "uitruimen voor school",
            "Afwasmachine uitruimen vóór school (uiterlijk 11:00). Dit is belangrijk zodat de dingen die overdag worden gebruikt direct in de machine kunnen.",
            3, 1, 1, "ochtend"
        ),
        (
            "uitruimen_avond",
            "uitruimen avond",
            "Afwasmachine uitruimen in de avond + pannen schoonmaken + planken schoonmaken.",
            7, 2, 1, "avond"
        ),
        (
            "inruimen",
            "inruimen",
            "Afwasmachine inruimen in de avond + aanrecht schoonmaken.",
            7, 2, 1, "avond"
        ),
        (
            "dekken",
            "dekken",
            "Tafel dekken voor het avondeten + na het eten de tafel afnemen en zorgen dat die schoon is.",
            7, 2, 1, "avond"
        ),
        (
            "karton_papier",
            "karton en papier wegbrengen",
            "Karton en oud papier verzamelen en naar de container brengen.",
            2, 1, 1, "middag"
        ),
        (
            "glas",
            "glas wegbrengen",
            "Glas verzamelen en naar de glasbak brengen. 1x per week totaal.",
            1, 1, 1, "middag"
        ),
        (
            "koken",
            "koken",
            "Een maaltijd koken voor het gezin. Elk kind 1x per maand.",
            1, 1, 4, "avond"
        ),
    ]
    for name, display_name, description, weekly_target, per_child, rotation, time in tasks:
        cur.execute(
            """INSERT INTO tasks (name, display_name, description, weekly_target, per_child_target, rotation_weeks, time_of_day)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (name, display_name, description, weekly_target, per_child, rotation, time)
        )

    conn.commit()
    cur.close()
    conn.close()
    print("Database gevuld met gezinsleden en taken!")


def reset_tasks_2026():
    """Reset de taken naar de 2026 configuratie.

    Dit verwijdert alle bestaande taken en voegt de nieuwe taken toe.
    LET OP: Dit verwijdert ook alle voltooide taken (completions)!
    """
    if not DATABASE_URL:
        print("Geen DATABASE_URL gevonden, skip reset")
        return

    conn = get_db()
    cur = conn.cursor()

    # Verwijder bestaande completions en taken
    cur.execute("DELETE FROM completions")
    cur.execute("DELETE FROM swaps")
    cur.execute("DELETE FROM tasks")

    # Voeg nieuwe kolommen toe als ze nog niet bestaan
    try:
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS description TEXT")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS per_child_target INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS rotation_weeks INTEGER DEFAULT 1")
    except Exception as e:
        print(f"Kolommen bestaan mogelijk al: {e}")

    # Taken toevoegen (afspraken 2026)
    tasks = [
        (
            "uitruimen_ochtend",
            "uitruimen voor school",
            "Afwasmachine uitruimen vóór school (uiterlijk 11:00). Dit is belangrijk zodat de dingen die overdag worden gebruikt direct in de machine kunnen.",
            3, 1, 1, "ochtend"
        ),
        (
            "uitruimen_avond",
            "uitruimen avond",
            "Afwasmachine uitruimen in de avond + pannen schoonmaken + planken schoonmaken.",
            7, 2, 1, "avond"
        ),
        (
            "inruimen",
            "inruimen",
            "Afwasmachine inruimen in de avond + aanrecht schoonmaken.",
            7, 2, 1, "avond"
        ),
        (
            "dekken",
            "dekken",
            "Tafel dekken voor het avondeten + na het eten de tafel afnemen en zorgen dat die schoon is.",
            7, 2, 1, "avond"
        ),
        (
            "karton_papier",
            "karton en papier wegbrengen",
            "Karton en oud papier verzamelen en naar de container brengen.",
            2, 1, 1, "middag"
        ),
        (
            "glas",
            "glas wegbrengen",
            "Glas verzamelen en naar de glasbak brengen. 1x per week totaal.",
            1, 1, 1, "middag"
        ),
        (
            "koken",
            "koken",
            "Een maaltijd koken voor het gezin. Elk kind 1x per maand.",
            1, 1, 4, "avond"
        ),
    ]
    for name, display_name, description, weekly_target, per_child, rotation, time in tasks:
        cur.execute(
            """INSERT INTO tasks (name, display_name, description, weekly_target, per_child_target, rotation_weeks, time_of_day)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (name, display_name, description, weekly_target, per_child, rotation, time)
        )

    conn.commit()
    cur.close()
    conn.close()
    print("Taken gereset naar 2026 configuratie!")


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
    cur.execute("SELECT id, name, display_name, description, weekly_target, per_child_target, rotation_weeks, time_of_day FROM tasks")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [Task(
        id=str(r["id"]),
        name=r["name"],
        display_name=r["display_name"] or r["name"],
        description=r["description"] or "",
        weekly_target=r["weekly_target"] or 0,
        per_child_target=r["per_child_target"] or 0,
        rotation_weeks=r["rotation_weeks"] or 1,
        time_of_day=r["time_of_day"] or ""
    ) for r in rows]


def get_task_by_name(name: str) -> Optional[Task]:
    """Zoek een taak op naam of display_name.

    Matching prioriteit:
    1. Exacte match op name of display_name
    2. "uitruimen" zonder specificatie → "uitruimen avond" (meest voorkomend)
    3. LIKE match op display_name
    """
    conn = get_db()
    cur = conn.cursor()

    # Normaliseer input
    name_lower = name.lower().strip()

    # Speciale case: "uitruimen" zonder "ochtend"/"avond" → default naar avond
    if name_lower in ("uitruimen", "uitgeruimd"):
        name_lower = "uitruimen_avond"

    # Probeer eerst exacte match
    cur.execute("""
        SELECT id, name, display_name, description, weekly_target, per_child_target, rotation_weeks, time_of_day
        FROM tasks
        WHERE LOWER(name) = %s OR LOWER(display_name) = %s
    """, (name_lower, name_lower))
    row = cur.fetchone()

    # Als geen exacte match, probeer LIKE (maar prefereer kortere matches)
    if not row:
        cur.execute("""
            SELECT id, name, display_name, description, weekly_target, per_child_target, rotation_weeks, time_of_day
            FROM tasks
            WHERE LOWER(display_name) LIKE %s
            ORDER BY LENGTH(display_name) ASC
            LIMIT 1
        """, (f"%{name_lower}%",))
        row = cur.fetchone()

    cur.close()
    conn.close()

    if row:
        return Task(
            id=str(row["id"]),
            name=row["name"],
            display_name=row["display_name"] or row["name"],
            description=row["description"] or "",
            weekly_target=row["weekly_target"] or 0,
            per_child_target=row["per_child_target"] or 0,
            rotation_weeks=row["rotation_weeks"] or 1,
            time_of_day=row["time_of_day"] or ""
        )
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


def get_completions_for_month(year: int, month: int) -> list[Completion]:
    """Haal alle voltooide taken op voor een specifieke maand."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, task_id, member_id, member_name, task_name, completed_at, week_number
        FROM completions
        WHERE EXTRACT(YEAR FROM completed_at) = %s
          AND EXTRACT(MONTH FROM completed_at) = %s
    """, (year, month))
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

    # Gebruik opgegeven datum of huidige tijd (in lokale timezone)
    completed_date = completion_data.get("completed_date")
    if completed_date:
        # Zet date om naar datetime (middag van die dag, lokale tijd)
        completed_at = datetime.combine(completed_date, datetime.min.time().replace(hour=12), tzinfo=TIMEZONE)
    else:
        completed_at = now_local()

    cur.execute("""
        INSERT INTO completions (task_id, member_id, member_name, task_name, week_number, completed_at)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (
        int(completion_data["task_id"]),
        int(completion_data["member_id"]),
        completion_data["member_name"],
        completion_data["task_name"],
        completion_data["week_number"],
        completed_at
    ))
    new_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()

    # Maak return object (zonder completed_date veld dat niet in model zit)
    return_data = {k: v for k, v in completion_data.items() if k != "completed_date"}
    return Completion(id=str(new_id), completed_at=completed_at, **return_data)


def add_completions_bulk(completions_data: list[dict]) -> list[Completion]:
    """Voeg meerdere voltooide taken toe in één transactie.

    Als één insert faalt, worden ALLE inserts teruggedraaid (rollback).
    Dit voorkomt partial failures.
    """
    if not completions_data:
        return []

    conn = get_db()
    cur = conn.cursor()
    results = []

    try:
        for completion_data in completions_data:
            # Gebruik opgegeven datum of huidige tijd (in lokale timezone)
            completed_date = completion_data.get("completed_date")
            if completed_date:
                completed_at = datetime.combine(completed_date, datetime.min.time().replace(hour=12), tzinfo=TIMEZONE)
            else:
                completed_at = now_local()

            cur.execute("""
                INSERT INTO completions (task_id, member_id, member_name, task_name, week_number, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                int(completion_data["task_id"]),
                int(completion_data["member_id"]),
                completion_data["member_name"],
                completion_data["task_name"],
                completion_data["week_number"],
                completed_at
            ))
            new_id = cur.fetchone()["id"]

            return_data = {k: v for k, v in completion_data.items() if k != "completed_date"}
            results.append(Completion(id=str(new_id), completed_at=completed_at, **return_data))

        # Commit alleen als ALLES is gelukt
        conn.commit()
        return results

    except Exception as e:
        # Rollback bij elke fout
        conn.rollback()
        raise e

    finally:
        cur.close()
        conn.close()


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


def get_last_completion_for_member(member_id: str) -> Optional[Completion]:
    """Haal de laatst voltooide taak op voor een lid (voor undo)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, task_id, member_id, member_name, task_name, completed_at, week_number
        FROM completions WHERE member_id = %s
        ORDER BY completed_at DESC LIMIT 1
    """, (int(member_id),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return Completion(id=str(row["id"]), task_id=str(row["task_id"]), member_id=str(row["member_id"]),
                         member_name=row["member_name"], task_name=row["task_name"],
                         completed_at=row["completed_at"], week_number=row["week_number"])
    return None


def delete_completion(completion_id: str) -> bool:
    """Verwijder een voltooide taak (undo)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM completions WHERE id = %s", (int(completion_id),))
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return deleted


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


def get_absences_for_week(week_start: date, week_end: date) -> list[Absence]:
    """Haal alle afwezigheden op die overlappen met een week (batch query)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, member_id, member_name, start_date, end_date, reason
        FROM absences
        WHERE start_date <= %s AND end_date >= %s
    """, (week_end, week_start))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [Absence(id=str(r["id"]), member_id=str(r["member_id"]), member_name=r["member_name"],
                   start_date=r["start_date"], end_date=r["end_date"], reason=r["reason"]) for r in rows]


def get_completions_for_week(week_number: int) -> list[Completion]:
    """Haal alle voltooide taken op voor een week (batch query)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, task_id, member_id, member_name, task_name, completed_at, week_number
        FROM completions WHERE week_number = %s
    """, (week_number,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [Completion(id=str(r["id"]), task_id=str(r["task_id"]), member_id=str(r["member_id"]),
                       member_name=r["member_name"], task_name=r["task_name"],
                       completed_at=r["completed_at"], week_number=r["week_number"]) for r in rows]


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


# CRUD operaties voor Schedule Assignments
def schedule_exists_for_week(week_number: int, year: int) -> bool:
    """Check of er al een rooster bestaat voor deze week."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) as count FROM schedule_assignments
        WHERE week_number = %s AND year = %s
    """, (week_number, year))
    count = cur.fetchone()["count"]
    cur.close()
    conn.close()
    return count > 0


def get_schedule_for_week(week_number: int, year: int) -> list[ScheduleAssignment]:
    """Haal het opgeslagen rooster op voor een week."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, week_number, year, day_of_week, task_id, task_name, member_id, member_name, created_at
        FROM schedule_assignments
        WHERE week_number = %s AND year = %s
        ORDER BY day_of_week, task_name
    """, (week_number, year))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [ScheduleAssignment(
        id=str(r["id"]),
        week_number=r["week_number"],
        year=r["year"],
        day_of_week=r["day_of_week"],
        task_id=str(r["task_id"]),
        task_name=r["task_name"],
        member_id=str(r["member_id"]),
        member_name=r["member_name"],
        created_at=r["created_at"]
    ) for r in rows]


def save_schedule_for_week(week_number: int, year: int, assignments: list[dict]) -> list[ScheduleAssignment]:
    """Sla een nieuw weekrooster op.

    assignments: lijst van dicts met keys:
        day_of_week, task_id, task_name, member_id, member_name
    """
    conn = get_db()
    cur = conn.cursor()
    results = []

    try:
        for assignment in assignments:
            cur.execute("""
                INSERT INTO schedule_assignments
                    (week_number, year, day_of_week, task_id, task_name, member_id, member_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (
                week_number,
                year,
                assignment["day_of_week"],
                int(assignment["task_id"]),
                assignment["task_name"],
                int(assignment["member_id"]),
                assignment["member_name"]
            ))
            row = cur.fetchone()
            results.append(ScheduleAssignment(
                id=str(row["id"]),
                week_number=week_number,
                year=year,
                day_of_week=assignment["day_of_week"],
                task_id=str(assignment["task_id"]),
                task_name=assignment["task_name"],
                member_id=str(assignment["member_id"]),
                member_name=assignment["member_name"],
                created_at=row["created_at"]
            ))

        conn.commit()
        return results

    except Exception as e:
        conn.rollback()
        raise e

    finally:
        cur.close()
        conn.close()


def delete_schedule_for_week(week_number: int, year: int) -> int:
    """Verwijder het rooster voor een week (voor regeneratie)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM schedule_assignments
        WHERE week_number = %s AND year = %s
    """, (week_number, year))
    deleted_count = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return deleted_count


def update_assignment(assignment_id: str, member_id: str, member_name: str) -> bool:
    """Update een assignment naar een andere persoon (herplanning)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE schedule_assignments
        SET member_id = %s, member_name = %s
        WHERE id = %s
    """, (int(member_id), member_name, int(assignment_id)))
    updated = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return updated


def delete_assignment(assignment_id: str) -> bool:
    """Verwijder een specifieke assignment."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM schedule_assignments WHERE id = %s", (int(assignment_id),))
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return deleted


def get_assignments_for_day(week_number: int, year: int, day_of_week: int) -> list[ScheduleAssignment]:
    """Haal alle assignments op voor een specifieke dag."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, week_number, year, day_of_week, task_id, task_name, member_id, member_name, created_at
        FROM schedule_assignments
        WHERE week_number = %s AND year = %s AND day_of_week = %s
    """, (week_number, year, day_of_week))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [ScheduleAssignment(
        id=str(r["id"]),
        week_number=r["week_number"],
        year=r["year"],
        day_of_week=r["day_of_week"],
        task_id=str(r["task_id"]),
        task_name=r["task_name"],
        member_id=str(r["member_id"]),
        member_name=r["member_name"],
        created_at=r["created_at"]
    ) for r in rows]


def get_member_assignments_for_day(member_id: str, week_number: int, year: int, day_of_week: int) -> list[ScheduleAssignment]:
    """Haal assignments op voor een specifiek lid op een dag (voor tijdslot check)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT sa.id, sa.week_number, sa.year, sa.day_of_week, sa.task_id, sa.task_name,
               sa.member_id, sa.member_name, sa.created_at, t.time_of_day
        FROM schedule_assignments sa
        JOIN tasks t ON sa.task_id = t.id
        WHERE sa.member_id = %s AND sa.week_number = %s AND sa.year = %s AND sa.day_of_week = %s
    """, (int(member_id), week_number, year, day_of_week))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [ScheduleAssignment(
        id=str(r["id"]),
        week_number=r["week_number"],
        year=r["year"],
        day_of_week=r["day_of_week"],
        task_id=str(r["task_id"]),
        task_name=r["task_name"],
        member_id=str(r["member_id"]),
        member_name=r["member_name"],
        created_at=r["created_at"]
    ) for r in rows]


def add_assignment(week_number: int, year: int, day_of_week: int, task_id: str, task_name: str, member_id: str, member_name: str) -> ScheduleAssignment:
    """Voeg een nieuwe assignment toe (voor herplanning)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO schedule_assignments
            (week_number, year, day_of_week, task_id, task_name, member_id, member_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at
    """, (week_number, year, day_of_week, int(task_id), task_name, int(member_id), member_name))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return ScheduleAssignment(
        id=str(row["id"]),
        week_number=week_number,
        year=year,
        day_of_week=day_of_week,
        task_id=str(task_id),
        task_name=task_name,
        member_id=str(member_id),
        member_name=member_name,
        created_at=row["created_at"]
    )


def get_week_schedule_data(week_number: int, year: int, week_start: date, week_end: date, month: int) -> dict:
    """Haal ALLE data op voor get_week_schedule in één database connectie.

    Dit is veel sneller dan 7+ losse queries met elk een eigen connectie.
    Retourneert een dict met: members, tasks, completions, absences, schedule_exists, schedule, month_completions
    """
    conn = get_db()
    cur = conn.cursor()

    result = {}

    # 1. Members
    cur.execute("SELECT id, name FROM members")
    rows = cur.fetchall()
    result["members"] = [Member(id=str(r["id"]), name=r["name"]) for r in rows]

    # 2. Tasks
    cur.execute("SELECT id, name, display_name, description, weekly_target, per_child_target, rotation_weeks, time_of_day FROM tasks")
    rows = cur.fetchall()
    result["tasks"] = [Task(
        id=str(r["id"]),
        name=r["name"],
        display_name=r["display_name"] or r["name"],
        description=r["description"] or "",
        weekly_target=r["weekly_target"] or 0,
        per_child_target=r["per_child_target"] or 0,
        rotation_weeks=r["rotation_weeks"] or 1,
        time_of_day=r["time_of_day"] or ""
    ) for r in rows]

    # 3. Completions for week
    cur.execute("""
        SELECT id, task_id, member_id, member_name, task_name, completed_at, week_number
        FROM completions WHERE week_number = %s
    """, (week_number,))
    rows = cur.fetchall()
    result["completions"] = [Completion(id=str(r["id"]), task_id=str(r["task_id"]), member_id=str(r["member_id"]),
                       member_name=r["member_name"], task_name=r["task_name"],
                       completed_at=r["completed_at"], week_number=r["week_number"]) for r in rows]

    # 4. Absences for week
    cur.execute("""
        SELECT id, member_id, member_name, start_date, end_date, reason
        FROM absences
        WHERE start_date <= %s AND end_date >= %s
    """, (week_end, week_start))
    rows = cur.fetchall()
    result["absences"] = [Absence(id=str(r["id"]), member_id=str(r["member_id"]), member_name=r["member_name"],
                   start_date=r["start_date"], end_date=r["end_date"], reason=r["reason"]) for r in rows]

    # 5. Check if schedule exists
    cur.execute("""
        SELECT COUNT(*) as count FROM schedule_assignments
        WHERE week_number = %s AND year = %s
    """, (week_number, year))
    result["schedule_exists"] = cur.fetchone()["count"] > 0

    # 6. Get schedule if exists
    if result["schedule_exists"]:
        cur.execute("""
            SELECT id, week_number, year, day_of_week, task_id, task_name, member_id, member_name, created_at
            FROM schedule_assignments
            WHERE week_number = %s AND year = %s
            ORDER BY day_of_week, task_name
        """, (week_number, year))
        rows = cur.fetchall()
        result["schedule"] = [ScheduleAssignment(
            id=str(r["id"]),
            week_number=r["week_number"],
            year=r["year"],
            day_of_week=r["day_of_week"],
            task_id=str(r["task_id"]),
            task_name=r["task_name"],
            member_id=str(r["member_id"]),
            member_name=r["member_name"],
            created_at=r["created_at"]
        ) for r in rows]
    else:
        result["schedule"] = []

    # 7. Monthly completions (for stats)
    cur.execute("""
        SELECT id, task_id, member_id, member_name, task_name, completed_at, week_number
        FROM completions
        WHERE EXTRACT(YEAR FROM completed_at) = %s
          AND EXTRACT(MONTH FROM completed_at) = %s
    """, (year, month))
    rows = cur.fetchall()
    result["month_completions"] = [Completion(id=str(r["id"]), task_id=str(r["task_id"]), member_id=str(r["member_id"]),
                       member_name=r["member_name"], task_name=r["task_name"],
                       completed_at=r["completed_at"], week_number=r["week_number"]) for r in rows]

    cur.close()
    conn.close()

    return result


def migrate_add_schedule_table():
    """Migratie: voeg schedule_assignments tabel toe aan bestaande database."""
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schedule_assignments (
                id SERIAL PRIMARY KEY,
                week_number INTEGER NOT NULL,
                year INTEGER NOT NULL,
                day_of_week INTEGER NOT NULL,
                task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                task_name VARCHAR(100),
                member_id INTEGER REFERENCES members(id) ON DELETE CASCADE,
                member_name VARCHAR(50),
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(week_number, year, day_of_week, task_id)
            )
        """)
        conn.commit()
        print("schedule_assignments tabel aangemaakt!")

    except Exception as e:
        conn.rollback()
        print(f"Migratie fout: {e}")
        raise e

    finally:
        cur.close()
        conn.close()
