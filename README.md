# 🏹 Archery Results Tracker

Webapplikasjon for å importere, lagre og visualisere resultater fra bueskytingskonkurranser.

---

## Funksjoner

**Brukerhåndtering**
- Registrering med e-postverifisering (lenke sendes via e-post, eller skrives til konsoll i dev-modus)
- Innlogging med persistent sesjon
- Glemt passord — tilbakestillingslenke gyldig i 1 time
- Favorittgrupper: lag navngitte grupper av skyttere for rask sammenligning
- Admin-panel: se alle brukere, slett brukere
- Standard admin-bruker opprettes automatisk: `admin@archery.local` / `Admin123!`

**Resultater og visualisering**
- Importer resultater fra HTML-filer (drag & drop eller filvelger)
- Sammenlign opptil alle skyttere i samme tidsserie-graf
- Årsgjennomsnitt per skytter med beste score og antall konkurranser
- Score normalisert til 60-piler-basis (`score_per_60`) for rettferdig sammenligning på tvers av distanser
- Konkurranseoversikt: bla gjennom alle stevner, klikk for full resultatliste
- Filtrering på klasse, distanse og datoperiode

**Teknisk**
- SQLite som standard (ingen konfig nødvendig), PostgreSQL valgfritt via `DATABASE_URL`
- Database-backup og gjenoppretting via API
- Automatisk synkronisering av resultater fra ekstern kilde (bakgrunnsjobb)
- Swagger UI på `/api/docs`
- 130 automatiserte tester

---

## Hurtigstart med Docker (anbefalt)

Krever [Docker](https://www.docker.com/) eller [Colima](https://github.com/abiosoft/colima).

```bash
# 1. Klon / naviger til prosjektmappen
cd archery-tracker

# 2. Kopier konfigurasjonsfil og tilpass om ønskelig
cp .env.example .env

# 3. Start (bygger image første gang, ca. 1–2 min)
docker compose up -d

# App tilgjengelig på http://localhost
```

**Med Colima** må Docker-socketen peke riktig. Legg dette i `~/.zshrc` én gang:
```bash
export DOCKER_HOST=unix:///Users/<ditt-brukernavn>/.colima/default/docker.sock
```

**Med PostgreSQL** (valgfritt):
```bash
# Legg til i .env:
# DATABASE_URL=postgresql://archery:archery@db:5432/archery
# POSTGRES_PASSWORD=ditt-passord

docker compose --profile postgres up -d
```

**Nyttige kommandoer:**
```bash
docker compose logs -f web        # følg app-logger
docker compose down               # stopp alt
docker compose down -v            # stopp + slett volumer (sletter DB!)
docker compose build --no-cache   # rebuild etter kodeendringer
```

---

## Lokal utvikling (uten Docker)

### Krav
- Python 3.8+

```bash
# Opprett virtuelt miljø
python3 -m venv venv
source venv/bin/activate

# Installer avhengigheter
pip install -r requirements.txt

# Start serveren
python -m src.app
```

Åpne **http://localhost:5001**

### Miljøvariabler

Kopier `.env.example` til `.env` og tilpass:

| Variabel | Standardverdi | Beskrivelse |
|---|---|---|
| `SECRET_KEY` | *(usikker dev-nøkkel)* | Flask session-nøkkel — **endre i produksjon** |
| `DATABASE_URL` | *(tom = SQLite)* | PostgreSQL connection string |
| `MAIL_SERVER` | *(tom = console)* | SMTP-server for e-postverifisering |
| `MAIL_PORT` | `587` | SMTP-port |
| `MAIL_USERNAME` | | SMTP brukernavn |
| `MAIL_PASSWORD` | | SMTP passord |
| `MAIL_DEFAULT_SENDER` | `noreply@archery.local` | Avsenderadresse |
| `BASE_URL` | `http://localhost:5001` | Brukes i e-postlenker |
| `HTTPS` | `false` | Sett `true` for å aktivere Secure-flagg på cookies |
| `DEBUG` | `false` | Flask debug-modus — aldri `true` i produksjon |

> Når `MAIL_SERVER` ikke er satt skrives verifiserings- og passord-reset-lenker til terminalen.

---

## Kjøre tester

```bash
python -m pytest tests/ -q
```

130 tester dekker database-logikk, API-endepunkter, autentisering, grupper og admin.

---

## Prosjektstruktur

```
archery-tracker/
├── src/
│   ├── app.py              # Flask-applikasjon, alle API-ruter
│   ├── database.py         # SQLite-operasjoner (resultater, skyttere, stevner)
│   ├── auth_db.py          # Brukere, grupper, passord-reset
│   ├── auth.py             # Flask-Login User-klasse, e-postutsending
│   ├── scraper.py          # HTML-parser for resultatsider
│   ├── backup.py           # Database-backup og gjenoppretting
│   ├── db_postgres.py      # PostgreSQL-implementasjon (aktiveres via DATABASE_URL)
│   └── migrate_to_postgres.py  # Migreringsskript SQLite → PostgreSQL
├── templates/
│   └── index.html          # Enkeltside-dashboard (Chart.js, vanilla JS)
├── static/
│   └── openapi.yaml        # OpenAPI 3.0 spec (Swagger UI på /api/docs)
├── nginx/
│   └── nginx.conf          # Nginx-konfig: serverer /static/ direkte, proxy til gunicorn
├── tests/
│   ├── conftest.py         # Pytest fixtures
│   ├── test_database.py    # Database-logikk og score-normalisering
│   ├── test_app.py         # API-endepunkter, filtrering, backup
│   └── test_auth.py        # Autentisering, grupper, admin
├── data/                   # SQLite-database og backups (ikke i git)
├── Dockerfile              # Python 3.11-slim + gunicorn
├── docker-compose.yml      # web (gunicorn) + nginx + valgfri postgres
├── .env.example            # Mal for miljøvariabler
├── requirements.txt
└── README.md
```

---

## API

Fullstendig API-dokumentasjon er tilgjengelig via Swagger UI når appen kjører:

**http://localhost/api/docs**

### Oversikt over endepunkter

| Gruppe | Endepunkt | Beskrivelse |
|---|---|---|
| Auth | `POST /api/auth/register` | Registrer bruker |
| Auth | `POST /api/auth/login` | Logg inn |
| Auth | `POST /api/auth/logout` | Logg ut |
| Auth | `GET /api/auth/me` | Innlogget bruker |
| Auth | `POST /api/auth/forgot-password` | Be om passord-reset |
| Auth | `POST /api/auth/reset-password` | Sett nytt passord |
| Auth | `GET /api/auth/verify/<token>` | Bekreft e-post |
| Archers | `GET /api/archers` | Liste alle skyttere |
| Archers | `POST /api/archers` | Legg til skytter |
| Archers | `GET /api/archers/<id>/results` | Resultater for skytter |
| Archers | `GET /api/archers/<id>/stats` | Statistikk for skytter |
| Results | `GET /api/results` | Alle resultater (med filter) |
| Results | `GET /api/categories` | Alle klasser |
| Results | `GET /api/distances` | Alle distanser |
| Events | `GET /api/events` | Stevneoversikt |
| Events | `GET /api/events/<id>/results` | Resultater for et stevne |
| Charts | `GET /api/chart/scores/<id>` | Tidsserie for én skytter |
| Charts | `GET /api/chart/compare` | Sammenlign flere skyttere |
| Charts | `GET /api/chart/yearly/<id>` | Årsgjennomsnitt |
| Charts | `GET /api/chart/yearly/compare` | Sammenlign årsgjennomsnitt |
| Groups | `GET/POST /api/groups` | Favorittgrupper |
| Groups | `DELETE /api/groups/<id>` | Slett gruppe |
| Groups | `GET/POST /api/groups/<id>/archers` | Skyttere i gruppe |
| Import | `POST /api/import` | Importer fra HTML-streng |
| Import | `POST /api/import/file` | Importer fra opplastet fil |
| Backup | `POST /api/backup` | Opprett backup |
| Backup | `GET /api/backup/list` | Liste backups |
| Backup | `POST /api/backup/restore` | Gjenopprett backup |
| Sync | `GET /api/sync/status` | Synkroniseringsstatus |
| Sync | `POST /api/sync/start` | Start synkronisering |
| Sync | `POST /api/sync/stop` | Stopp synkronisering |
| Admin | `GET /api/admin/users` | Alle brukere (kun admin) |
| Admin | `DELETE /api/admin/users/<id>` | Slett bruker (kun admin) |

Alle endepunkter unntatt auth krever innlogging.

---

## Sikkerhet

- Rate limiting på innlogging (20/min), registrering (10/time) og passord-reset (5/time)
- Verifiseringstoken utløper etter 24 timer, passord-reset-token etter 1 time
- Session-cookies med `HttpOnly` og `SameSite=Lax`; `Secure`-flagg aktiveres via `HTTPS=true`
- Passord hashes med bcrypt

---

## Kjente begrensninger / fremtidige forbedringer

- Ingen Alembic-migrasjoner — skjemaendringer håndteres med `ALTER TABLE` i oppstartskoden
- Synkroniseringsmodulen (`src/sync/`) er ikke aktivert som standard
- Eksport til CSV/Excel ikke implementert

---

## Lisens

MIT
