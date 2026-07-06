# Archery Tracker — prosjektinstruksjoner

Denne filen gir kontekst for AI-assistenter og nye utviklere som skal jobbe videre med prosjektet.

---

## Hva er dette?

Flask-basert webapplikasjon for å importere, lagre og visualisere resultater fra bueskytingskonkurranser. Backend og frontend kjører i samme Python-prosess (Jinja2-templates + REST API). Ingen JavaScript-byggesteg.

---

## Kjøre prosjektet

```bash
# Lokal utvikling
source venv/bin/activate
python -m src.app          # http://localhost:5001

# Docker
docker compose up -d       # http://localhost
# (med Colima: sett DOCKER_HOST=unix:///Users/<navn>/.colima/default/docker.sock)
```

## Kjøre tester

```bash
python -m pytest tests/ -q
```

155 tester. Alltid kjør disse etter endringer. Forventet output: `N passed`.

---

## Arkitektur

```
src/app.py          → Flask-app, alle ruter, rate limiting
src/database.py     → SQLite CRUD (archers, events, results, sync)
src/auth_db.py      → Brukere, grupper, token-håndtering
src/auth.py         → Flask-Login User-klasse, e-postutsending
src/scraper.py      → HTML-parser for importering av resultater
src/backup.py       → sqlite3.Connection.backup() + restore
src/db_postgres.py  → PostgreSQL-versjon av database.py (aktiveres via DATABASE_URL)
```

**Viktig:** `db_postgres.py`-importen ligger nederst i `database.py`. Dette er med vilje — PostgreSQL-funksjonene må overskrive SQLite-funksjonene, ikke omvendt.

---

## Database

- **SQLite** som standard, fil: `data/archery.db`
- **PostgreSQL** aktiveres ved å sette `DATABASE_URL=postgresql://...`
- Skjemamigrasjoner gjøres med `ALTER TABLE` i `init_database()` og `create_auth_tables()` — ingen Alembic
- WAL-modus og indekser settes opp automatisk i `init_database()`

### Tabeller
| Tabell | Beskrivelse |
|---|---|
| `archers` | Skyttere (navn, ekstern ID, klubb) |
| `events` | Stevner (ekstern ID, navn, URL) |
| `results` | Resultater (archer_id, event_id, dato, distanse, klasse, score, plassering) |
| `sync_status` | Synkroniseringsstatus per ekstern skytter-ID |
| `sync_errors` | Synkroniseringsfeil (outbox-pattern) |
| `sync_log` | Logg over synkroniseringsjobber |
| `users` | Brukere (e-post, brukernavn, bcrypt-hash, admin-flagg, tokens) |
| `groups` | Favorittgrupper (eies av en bruker) |
| `group_archers` | Kobling gruppe ↔ skytter |

---

## Autentisering

- Flask-Login med `@login_required` på alle data-endepunkter
- `LOGIN_DISABLED = True` i test-fixtures (se `tests/conftest.py`) — **ikke** sett dette i produksjon
- Standard admin: `admin@archery.local` / `Admin123!` — opprettes av `ensure_admin_user()` ved oppstart
- Verifiseringstoken utløper etter 24t, passord-reset-token etter 1t
- Rate limiting via flask-limiter (memory backend — ikke persistent ved restart)

---

## Testing

```
tests/conftest.py       → flask_client fixture med LOGIN_DISABLED=True og temp-DB
tests/test_database.py  → Enhetstester for database-logikk og score-normalisering
tests/test_app.py       → Integrasjonstester for API-endepunkter
tests/test_auth.py      → Tester for auth, grupper og admin (bruker authed_client uten LOGIN_DISABLED)
```

Nye API-endepunkter skal alltid ha tilhørende tester.

---

## Frontend

- Enkelt-side SPA i `templates/index.html` (~1800 linjer)
- Vanilla JavaScript, ingen rammeverk
- Chart.js for grafer (tidsserie og årsgjennomsnitt)
- Auth-overlay vises ved oppstart hvis ikke innlogget
- `?reset=TOKEN` i URL åpner passord-reset-skjema
- `?verified=1` viser bekreftelsesmelding

### Viktige globale JS-variabler
| Variabel | Beskrivelse |
|---|---|
| `currentUser` | Innlogget bruker (null hvis ikke innlogget) |
| `allArchers` | Cache av alle skyttere fra `/api/archers` |
| `selectedArcherIds` | Set med valgte skytter-IDer |
| `chartMode` | `'time'` eller `'yearly'` |
| `_editingGroupId` | Gruppe-ID som er åpen i gruppe-editoren |

---

## Docker

```
Dockerfile              → Python 3.11-slim + gunicorn (2 workers, port 8000)
nginx/nginx.conf        → Serverer /static/ direkte, proxy til web:8000
docker-compose.yml      → web + nginx alltid, postgres kun med --profile postgres
.dockerignore           → Ekskluderer venv/, data/, tests/
```

Persistente data lagres i Docker-volumet `archery_data` (SQLite + backups).

---

## Miljøvariabler (se .env.example for alle)

| Variabel | Viktig? | Beskrivelse |
|---|---|---|
| `SECRET_KEY` | **Kritisk** | Bytt før produksjon |
| `DATABASE_URL` | Valgfri | Tom = SQLite |
| `MAIL_SERVER` | Valgfri | Tom = console-output |
| `HTTPS` | Produksjon | Aktiverer Secure-cookies |
| `DEBUG` | Aldri i prod | Flask debug-modus |

---

## Konvensjoner

- All feilmelding til bruker på norsk i API-svar
- `score_per_60`: score normalisert til 60-piler, definert i `ARROWS_BY_DISTANCE` i `database.py`
- Nye database-kolonner legges til med `ALTER TABLE ... ADD COLUMN` i `_migrations`-lista i `init_database()` eller `create_auth_tables()`
- Aldri bruk `git add -A` — ekskluder `.env` og `data/`

---

## Utestående / mulige forbedringer

- [ ] Alembic for ordentlig migrasjonshistorikk
- [ ] Redis for rate-limiting (nå: in-memory, tapes ved restart)
- [ ] Eksport til CSV/Excel
- [ ] Synkroniseringsmodulen (`src/sync/`) er implementert men ikke dokumentert
- [ ] HTTPS/TLS-terminering i nginx for produksjon
- [ ] Paginering på store resultatlister
