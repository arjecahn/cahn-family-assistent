# Cahn Family Task Assistant

Een slimme huishoudcoach die helpt met eerlijke takenverdeling voor het gezin Cahn.

## Features

- **Eerlijke verdeling**: Algoritme dat bijhoudt wie hoeveel taken heeft gedaan
- **ChatGPT interface**: Praat natuurlijk met de assistent via een Custom GPT
- **Afwezigheidsbeheer**: Houdt rekening met wie er wel/niet is
- **Weekoverzicht**: Zie wie hoeveel heeft gedaan
- **Ruilen**: Kinderen kunnen onderling taken ruilen

## Hoe het werkt

```
"Wie moet er dekken?"
→ "Linde is aan de beurt. Zij heeft deze week pas 3 taken gedaan,
   terwijl Nora er al 5 heeft."

"Ik heb uitgeruimd"
→ "Top Fenna! Ik heb je uitruim-taak genoteerd. Je staat nu op 4 taken."

"Nora is dit weekend weg"
→ "Begrepen! Nora is 2 dagen weg. Ik pas de verdeling aan."
```

## Tech Stack

| Component | Technologie |
|-----------|-------------|
| Backend | Python + FastAPI |
| Database | PostgreSQL (Supabase) |
| Hosting | Vercel |
| Interface | ChatGPT Custom GPT |

## Architectuur

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  ChatGPT GPT    │────▶│  Vercel API     │────▶│   Supabase      │
│  (Interface)    │◀────│  (FastAPI)      │◀────│   (PostgreSQL)  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## API Endpoints

| Endpoint | Beschrijving |
|----------|--------------|
| `GET /api/suggest/{task}` | Wie moet deze taak doen? |
| `POST /api/complete` | Registreer voltooide taak |
| `GET /api/summary` | Weekoverzicht |
| `POST /api/absence` | Registreer afwezigheid |
| `POST /api/swap/request` | Vraag ruil aan |

## Eerlijke Verdeling Algoritme

De assistent berekent een score voor elk gezinslid (lager = meer aan de beurt):

- **50%** - Totaal aantal taken deze week
- **30%** - Hoe vaak deze specifieke taak al gedaan
- **20%** - Hoe lang geleden de taak voor het laatst gedaan

## Lokaal draaien

```bash
# Clone repo
git clone https://github.com/arjecahn/cahn-family-assistent.git
cd cahn-family-assistent

# Install dependencies
pip install -r requirements.txt

# Set database URL
export DATABASE_URL="postgresql://..."

# Run
uvicorn src.main:app --reload
```

## Deployment

Push naar `main` branch triggert automatische deployment naar Vercel.

```bash
git push origin main
```

## Documentatie

- [CLAUDE.md](CLAUDE.md) - Technische documentatie voor Claude Code
- [docs/openapi-schema.json](docs/openapi-schema.json) - OpenAPI specificatie

## Licentie

Private project voor familie Cahn.
