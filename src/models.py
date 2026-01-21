"""Data models voor de Cahn Family Task Assistant (Firestore)."""
from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, Field


class Member(BaseModel):
    """Gezinslid (Nora, Linde, Fenna)."""
    id: str
    name: str
    email: Optional[str] = None


class Task(BaseModel):
    """Huishoudelijke taak."""
    id: str
    name: str  # Interne naam: "uitruimen_avond"
    display_name: str  # Vriendelijke naam: "uitruimen"
    description: str = ""  # Volledige omschrijving van wat de taak inhoudt
    weekly_target: int = 0  # Hoe vaak per week in totaal (3 kinderen)
    per_child_target: int = 0  # Hoe vaak per kind per week
    rotation_weeks: int = 1  # Elke hoeveel weken (1 = wekelijks, 3 = om de 3 weken)
    time_of_day: str = ""  # "ochtend", "middag", "avond"


class Completion(BaseModel):
    """Voltooide taak."""
    id: str
    task_id: str
    member_id: str
    member_name: str  # Voor makkelijke queries
    task_name: str  # Voor makkelijke queries
    completed_at: datetime = Field(default_factory=datetime.utcnow)
    week_number: int


class Swap(BaseModel):
    """Ruil verzoek tussen twee kinderen."""
    id: str
    requester_id: str
    requester_name: str
    target_id: str
    target_name: str
    task_id: str
    task_name: str
    swap_date: date
    status: str = "pending"  # pending, accepted, rejected
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Absence(BaseModel):
    """Afwezigheid van een gezinslid."""
    id: str
    member_id: str
    member_name: str
    start_date: date
    end_date: date
    reason: Optional[str] = None


class ScheduleAssignment(BaseModel):
    """Toegewezen taak in het weekrooster.

    Dit is de geplande toewijzing, niet de daadwerkelijke voltooiing.
    Completions worden apart bijgehouden.
    """
    id: str
    week_number: int
    year: int
    day_of_week: int  # 0=maandag, 6=zondag
    task_id: str
    task_name: str
    member_id: str
    member_name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MissedTask(BaseModel):
    """Verzaakte taak - voor historisch bijhouden wie wat heeft gemist."""
    id: str
    week_number: int
    year: int
    original_day: int  # 0=maandag, 6=zondag - de dag waarop de taak gepland stond
    task_id: str
    task_name: str
    member_id: str
    member_name: str
    rescheduled_to_day: Optional[int] = None  # Naar welke dag herplant (None = vervallen)
    expired: bool = False  # True als taak niet herplant kon worden
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CustomRule(BaseModel):
    """Configureerbare regel voor taakplanning.

    Voorbeelden:
    - "Nora kan op donderdag nooit het glas wegbrengen"
    - "Linde mag nooit uitruimen ochtend"

    rule_type:
    - "unavailable": lid kan deze taak niet op deze dag
    - "never": lid kan deze taak nooit (ongeacht dag)
    - "prefer": lid heeft voorkeur voor deze taak (nog niet geïmplementeerd)
    """
    id: str
    member_name: str
    task_name: Optional[str] = None  # None = alle taken
    day_of_week: Optional[int] = None  # None = alle dagen, 0=maandag, 6=zondag
    rule_type: str = "unavailable"
    description: Optional[str] = None
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
