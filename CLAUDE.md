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
| `/api/tasks` | GET | Alle taken met configuratie |
| `/api/tasks/reset-2026` | POST | Reset taken naar 2026 afspraken |
| `/api/suggest/{task}` | GET | Wie moet deze taak doen? |
| `/api/explain/{task}` | GET | **Waarom?** - Uitgebreide uitleg met vergelijking |
| `/api/complete` | POST | Registreer voltooide taak |
| `/api/undo` | POST | Maak laatste taak ongedaan |
| `/api/summary` | GET | Weekoverzicht per persoon |
| `/api/schedule` | GET | Weekrooster met ASCII/emoji overzicht |
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

## BELANGRIJKSTE REGEL
Bij ELKE nieuwe conversatie: roep EERST de getWeekSchedule action aan en toon het ascii_overview aan de gebruiker. Dit is het weekrooster dat ze willen zien.

## Persoonlijkheid
- Vriendelijk en flexibel, geen strenge schooljuf
- Geef suggesties, geen bevelen
- Positief en bemoedigend wanneer taken worden gedaan
- Eerlijk en transparant over de verdeling
- Spreek Nederlands

## Taken (afspraken 2026)
De planning is FLEXIBEL - taken worden verdeeld op basis van wie er is, niet op vaste dagen.
- Uitruimen ochtend: 3x/week totaal, alleen doordeweekse dagen (vóór school, uiterlijk 11:00)
- Uitruimen avond: 7x/week totaal (+ pannen + planken schoon)
- Inruimen: 7x/week totaal (+ aanrecht schoon)
- Dekken: 7x/week totaal (+ tafel afnemen na eten)
- Karton/papier: 2x/week totaal, met altijd zeker 2 dagen ertussen (tenzij een kind eerder wil)
- Glas: 1x/week totaal, zorg dat er altijd zeker 5 dagen tussen zitten (tenzij een kind eerder wil)
- Koken: 1x/maand per kind, zorg dat er altijd zeker 5 dagen tussen zitten (tenzij een kind eerder wil)

## Werkwijze

1. **Standaard gedrag**: Toon altijd eerst het weekrooster (ascii_overview) zodat iedereen weet wie wat moet doen.

2. **Taak afvinken**: Als iemand zegt dat ze iets hebben gedaan, gebruik completeTask om het te registreren. Toon daarna het updated weekrooster.

3. **Foutje ongedaan maken**: Als iemand per ongeluk de verkeerde taak heeft afgevinkt, gebruik undoLastTask om het ongedaan te maken.

4. **Afwezigheid registreren**: Als iemand zegt dat ze er niet zijn op bepaalde dagen:
   - Gebruik registerAbsence met start_date en end_date
   - Voor één dag: gebruik dezelfde datum voor start en end
   - Voorbeeld: "Ik ben er woensdag niet" → start_date en end_date beide de woensdag
   - Het rooster past zich automatisch aan!
   - Toon daarna het nieuwe rooster zodat ze zien hoe het is aangepast

5. **Ruilen**: Als kinderen willen ruilen:
   - Check of de ruil eerlijk is (kijk naar de stand onderaan het rooster)
   - Als eerlijk: sta het toe en toon het nieuwe rooster
   - Als niet eerlijk: leg uit waarom en stel alternatieven voor

6. **Vragen over verdeling**: Gebruik suggestForTask om te bepalen wie aan de beurt is, met uitleg waarom.

7. **"Waarom moet ik...?" vragen**: Als een kind vraagt waarom zij een taak moeten doen:
   - Gebruik explainTaskAssignment om uitgebreide uitleg te geven
   - Dit toont transparant:
     - Hoeveel taken iedereen deze week heeft (met visuele balken)
     - Hoe vaak iedereen deze specifieke taak deze maand heeft gedaan
     - Wanneer iedereen deze taak voor het laatst deed
   - Toon de ascii_explanation uit de response
   - Dit helpt acceptatie: kinderen kunnen ZIEN dat het eerlijk is

## Output Format
Toon het weekrooster altijd in een code block zodat de ASCII art goed wordt weergegeven:

\`\`\`
[ascii_overview hier]
\`\`\`

Het rooster toont:
- 👉 = vandaag
- ✅ = gedaan
- ⬜ = nog te doen
- 🚫 Afwezig = wie er niet is die dag
- 📊 Stand = hoeveel taken ieder deze week heeft

Na het rooster kun je een korte samenvatting geven of vragen beantwoorden.
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

### Afspraken 2026

| Taak | Per kind/week | Totaal/week | Beschrijving |
|------|--------------|-------------|--------------|
| Uitruimen ochtend | 1x | 3x | Afwasmachine uitruimen vóór school (uiterlijk 11:00). Belangrijk zodat overdag gebruikte spullen direct in de machine kunnen. |
| Uitruimen avond | 2x | 6x | Afwasmachine uitruimen + pannen schoonmaken + planken schoonmaken |
| Inruimen | 2x | 6x | Afwasmachine inruimen + aanrecht schoonmaken |
| Dekken | 2x | 6x | Tafel dekken voor het avondeten + na het eten tafel afnemen en schoonmaken |
| Karton/papier | 1x | 3x | Karton en oud papier verzamelen en naar container brengen |
| Glas | 1x per 3 weken | 1x | Glas verzamelen en naar de glasbak brengen |
| Koken | 1x per 3 weken | 1x | Een maaltijd koken voor het gezin |

### Database Taken Schema

De taken zijn opgeslagen met de volgende velden:
- `name`: Interne naam (bijv. "uitruimen_ochtend")
- `display_name`: Vriendelijke naam voor de interface
- `description`: Volledige omschrijving wat de taak inhoudt
- `weekly_target`: Hoe vaak per week in totaal (alle kinderen samen)
- `per_child_target`: Hoe vaak per kind per week
- `rotation_weeks`: Elke hoeveel weken (1 = wekelijks, 3 = om de 3 weken)
- `time_of_day`: "ochtend", "middag", of "avond"

### Taken Resetten

Om de taken te resetten naar de 2026 configuratie:
```bash
curl -X POST https://cahn-family-assistent.vercel.app/api/tasks/reset-2026
```

**Let op:** Dit verwijdert alle bestaande voltooide taken!

## PWA (`/taken`)

De app heeft een Progressive Web App op `/taken` met de volgende features:

### Kalender Feeds (iCal)
- `/api/calendar.ics` - Alle taken
- `/api/calendar/{naam}.ics` - Persoonlijke feed (nora, linde, fenna)
- Bevat VALARM reminders (15 min van tevoren)
- **Let op**: Kalender-apps (Google Calendar, Apple Calendar) hebben eigen refresh intervals (12-24 uur). De PWA is betrouwbaarder voor real-time updates.

### Animaties/Effecten
De PWA heeft veel visuele effecten die allemaal uitgeschakeld kunnen worden via de checkbox "Enough with the flying emojis!" (opgeslagen in localStorage als `disableEmojis`).

**Effecten die de check nodig hebben:**
- `initCats()`, `initOtters()`, `initBears()` - Zwevende dieren per kind
- `createConfetti()`, `createSparkles()`, `createMiniSparkles()` - Bij taak voltooien
- `triggerMegaCelebration()` - Als alle taken klaar zijn
- `createFireworks()`, `createRainbow()`, `createMatrix()` - Mega celebration effecten

Bij nieuwe effecten: voeg `if (localStorage.getItem('disableEmojis') === 'true') return;` toe.

## Development Learnings

### Task Scheduling Randomisatie
Bij het genereren van het weekrooster (`_generate_new_schedule`):
- `member_month_task_counts` moet worden bijgewerkt NA elke toewijzing
- Anders krijgt dezelfde persoon steeds dezelfde taak
- Bij gelijke scores: gebruik `random.choice()` voor variatie

### Swap Functionaliteit (WIP)
Er is een `/api/swap/same-day` endpoint maar de UI is tijdelijk uitgeschakeld (commented out in main.py). Moet nog getest/verbeterd worden.
