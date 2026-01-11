# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Cahn Family Task Assistant - een voice-gestuurde huishoudcoach voor het gezin Cahn (Nora, Linde, Fenna). De assistent helpt met eerlijke takenverdeling via Google Home in de keuken.

## Tech Stack

- **Backend**: Python 3.11 + FastAPI
- **Database**: Google Firestore (NoSQL)
- **Hosting**: Google Cloud Run
- **Voice**: Google Actions (via webhook)

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python -m uvicorn src.main:app --reload --port 8080

# Run tests
pytest tests/

# Deploy to Cloud Run
gcloud run deploy cahn-family-assistant \
  --source . \
  --region europe-west4 \
  --allow-unauthenticated
```

## Architecture

```
src/
├── main.py           # FastAPI app, API endpoints, webhook entry
├── models.py         # Pydantic models (Member, Task, Completion, etc.)
├── database.py       # Firestore CRUD operations
├── task_engine.py    # Core logic: fair distribution algorithm
└── voice_handlers.py # Google Actions intent processing
```

### Key Components

**TaskEngine** (`task_engine.py`): Core business logic
- `suggest_member_for_task(task_name)` - Suggests who should do a task based on weighted scoring
- `complete_task(member, task)` - Records task completion
- `register_absence(member, start, end)` - Handles member unavailability
- `get_weekly_summary()` - Returns task counts per member

**Fair Distribution Algorithm**: Weighted score (lower = more due)
- 50%: Total tasks this week
- 30%: Specific task count
- 20%: Recency (how long since last time)

**Voice Handlers** (`voice_handlers.py`): Maps Google Actions intents to TaskEngine methods

## Firestore Collections

- `members` - Nora, Linde, Fenna
- `tasks` - dekken, inruimen, uitruimen, etc.
- `completions` - Completed task records with week_number
- `absences` - Date ranges when members are away
- `swaps` - Task swap requests between members

## Environment Variables

- `GOOGLE_CLOUD_PROJECT` - GCP project ID (auto-set in Cloud Run)
- `PORT` - Server port (default: 8080)

## Voice Commands (Dutch)

- "Wie moet vanavond dekken?" → suggests member
- "Ik heb uitgeruimd" → records completion
- "Nora is dit weekend weg" → registers absence
- "Hoe staat de score?" → weekly summary

## Personality

The assistant is a friendly household coach:
- Suggestions, not commands
- Positive reinforcement on task completion
- Transparent about the distribution logic
- Understanding when tasks are behind
