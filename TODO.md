# TODO - Cahn Family Task Assistant

## Afgerond deze sessie
- [x] iCal feed endpoint (`/api/calendar.ics`)
- [x] Email kolom voor members + migratie
- [x] ATTENDEE property in calendar events voor uitnodigingen
- [x] Titels verkort (geen [DONE]/[MISSED] prefixes)
- [x] Events op "niet busy" (TRANSP: TRANSPARENT)
- [x] PWA pagina voor afvinken (`/taken`)
- [x] `/api/my-tasks/{naam}` endpoint

## Open items

### Kalender verbeteringen
- [ ] Aparte kalender feeds per kind: `/api/calendar/nora.ics`, `/api/calendar/linde.ics`, `/api/calendar/fenna.ics`
- [ ] iCal feed testen in Google Calendar na laatste wijzigingen
- [ ] Eventueel: meerdere weken tonen in kalender (nu alleen huidige week)

### PWA verbeteringen
- [ ] PWA manifest toevoegen (voor echte "app" ervaring)
- [ ] Offline support met service worker
- [ ] Push notifications voor herinneringen
- [ ] "Ongedaan maken" knop in PWA

### Mogelijke uitbreidingen
- [ ] QR-codes genereren per taak (scan = afvinken)
- [ ] Siri Shortcuts integratie (Hey Siri, dekken klaar)
- [ ] WhatsApp/Telegram bot
- [ ] Statistieken pagina (wie doet het meest, streaks, etc.)

### Technisch
- [ ] Arje toevoegen als test-member (om zelf te kunnen testen)
- [ ] API authenticatie overwegen (nu open endpoints)
- [ ] Rate limiting toevoegen

## URLs
- PWA: https://cahn-family-assistent.vercel.app/taken
- iCal: https://cahn-family-assistent.vercel.app/api/calendar.ics
- API docs: https://cahn-family-assistent.vercel.app/docs
