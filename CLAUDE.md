# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Cahn Family Task Assistant** - Een voice/chat-gestuurde huishoudcoach voor het gezin Cahn. De assistent helpt met eerlijke takenverdeling tussen Nora, Linde en Fenna via een ChatGPT Custom GPT.

## Tech Stack

- **Backend**: Python 3.11 + FastAPI
- **Database**: PostgreSQL via Supabase
- **Hosting**: Vercel (serverless)
- **Interface**: ChatGPT Custom GPT
- **CI/CD**: GitHub → Vercel auto-deploy

## Commands

```bash
# Lokaal draaien (vereist DATABASE_URL env var)
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8080

# Deploy (automatisch via GitHub push)
git push origin main
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  ChatGPT GPT    │────▶│  Vercel API     │────▶│   Supabase      │
│  (Interface)    │◀────│  (FastAPI)      │◀────│   (PostgreSQL)  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

### File Structure

```
cahn-family-assistent/
├── api/
│   └── index.py          # Vercel serverless entry point
├── src/
│   ├── __init__.py
│   ├── main.py           # FastAPI app + API endpoints
│   ├── models.py         # Pydantic data models
│   ├── database.py       # PostgreSQL/Supabase operations
│   ├── task_engine.py    # Core business logic (fair distribution)
│   └── voice_handlers.py # (Legacy) Google Actions handlers
├── vercel.json           # Vercel deployment config
├── requirements.txt      # Python dependencies
└── CLAUDE.md
```

## Core Components

### TaskEngine (`src/task_engine.py`)

Het hart van de applicatie - berekent eerlijke takenverdeling.

**Algoritme (weighted score, lager = meer aan de beurt):**
- 50%: Totaal aantal taken deze week
- 30%: Aantal keer deze specifieke taak gedaan
- 20%: Recency (hoe lang geleden laatst gedaan)

**Belangrijke methods:**
- `suggest_member_for_task(task_name)` - Suggereert wie aan de beurt is
- `complete_task(member, task)` - Registreert voltooide taak
- `register_absence(member, start, end)` - Registreert afwezigheid
- `get_weekly_summary()` - Weekoverzicht per persoon

### Database (`src/database.py`)

PostgreSQL via Supabase met automatische URL parsing voor Vercel compatibility.

**Tabellen:**
- `members` - Nora, Linde, Fenna
- `tasks` - dekken, inruimen, uitruimen, karton, glas
- `completions` - Voltooide taken met week_number
- `absences` - Afwezigheidsperiodes
- `swaps` - Ruil verzoeken

### API Endpoints (`src/main.py`)

| Endpoint | Method | Beschrijving |
|----------|--------|--------------|
| `/health` | GET | Health check |
| `/api/init` | GET | Database initialisatie |
| `/api/suggest/{task}` | GET | Wie moet deze taak doen? |
| `/api/complete` | POST | Registreer voltooide taak |
| `/api/summary` | GET | Weekoverzicht |
| `/api/absence` | POST | Registreer afwezigheid |
| `/api/swap/request` | POST | Vraag ruil aan |
| `/api/swap/respond` | POST | Accepteer/weiger ruil |

## Environment Variables

| Variable | Beschrijving |
|----------|--------------|
| `DATABASE_URL` | PostgreSQL connection string (Supabase) |
| `POSTGRES_URL` | Alternatieve naam voor database URL |

## ChatGPT Custom GPT Setup

### Instructions (System Prompt)
```
Je bent de huishoudcoach van het gezin Cahn. Je helpt met het eerlijk verdelen van huishoudelijke taken tussen Nora, Linde en Fenna.

Persoonlijkheid:
- Vriendelijk en flexibel, geen strenge schooljuf
- Geef suggesties, geen bevelen
- Positief en bemoedigend wanneer taken worden gedaan
- Eerlijk en transparant over de verdeling

Taken: uitruimen, inruimen, dekken, karton/papier wegbrengen, glas wegbrengen

Spreek Nederlands.
```

### Actions Schema
Zie `docs/openapi-schema.json` of de Actions configuratie in de GPT.

## Deployment

### Vercel (Production)
- Auto-deploy bij push naar `main` branch
- Serverless Python runtime
- URL: https://cahn-family-assistent.vercel.app

### Database
- Supabase PostgreSQL
- Project: cahn-family
- Region: eu-central-1 (Frankfurt)

## Development Notes

### Vercel Python Specifics
- Entry point moet in `api/` folder
- `sys.path` aanpassing nodig voor imports
- Supabase URL parameters moeten gefilterd worden voor psycopg2

### Geen authenticatie
API endpoints hebben geen auth - acceptabel voor familie-app met obscure URLs.

## Gezinsleden & Taken

**Gezinsleden:** Nora, Linde, Fenna

**Taken met weekly targets:**
| Taak | Target/week |
|------|-------------|
| Uitruimen (ochtend) | 3x |
| Uitruimen (avond) | 6x |
| Inruimen | 6x |
| Dekken | 6x |
| Karton/papier wegbrengen | 3x |
| Glas wegbrengen | 1x |
