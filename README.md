# 🏹 Archery Results Tracker

A web application to crawl, store, and visualize archery competition results from databases without APIs.

## Features

- **Web Scraper**: Parse HTML from archery result websites
- **SQLite Database**: Store results locally for fast access
- **Interactive Dashboard**: Visualize scores over time with Chart.js
- **Filter & Compare**: Filter by category, distance, and compare archers
- **Statistics**: View averages, best scores, and placement history

## Installation (macOS)

### Prerequisites

- Python 3.8+ (ofte forhåndsinstallert på Mac, eller installer via Homebrew)
- pip

For å sjekke om Python er installert:
```bash
python3 --version
```

Hvis Python ikke er installert, installer via Homebrew:
```bash
brew install python3
```

### Setup

1. **Klon eller naviger til prosjektmappen**:
   ```bash
   cd ~/Desktop/archery-tracker
   ```

2. **Opprett et virtuelt miljø** (anbefalt):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
   
   > 💡 Når det virtuelle miljøet er aktivert, vil du se `(venv)` i terminalen.

3. **Installer avhengigheter**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Initialiser databasen**:
   ```bash
   python -m src.database
   ```

## Bruk

### Starte webserveren

```bash
python -m src.app
```

Åpne deretter nettleseren på: **http://localhost:5000**

Eller åpne automatisk med:
```bash
open http://localhost:5000
```

### Importing Results

1. **Via Web Interface**:
   - Enter the archer's name
   - Upload an HTML file containing their results
   - Click "Import Results"

2. **Via API**:
   ```bash
   curl -X POST http://localhost:5000/api/import \
     -H "Content-Type: application/json" \
     -d '{
       "archer_name": "John Doe",
       "html_file": "/path/to/results.html"
     }'
   ```

3. **Via Python**:
   ```python
   from src.scraper import parse_html_file
   from src.database import add_archer, add_event, add_result

   results = parse_html_file('results.html')
   archer_id = add_archer('John Doe')
   
   for r in results:
       event_id = add_event(r['event_id'], r['event_name'])
       add_result(archer_id, event_id, r['date'], r['distance'], 
                  r['category'], r['score'], r['placement'])
   ```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/archers` | GET | List all archers |
| `/api/archers` | POST | Add new archer |
| `/api/archers/<id>/results` | GET | Get archer's results |
| `/api/archers/<id>/stats` | GET | Get archer's statistics |
| `/api/results` | GET | Get all results (with filters) |
| `/api/categories` | GET | List all categories |
| `/api/distances` | GET | List all distances |
| `/api/import` | POST | Import results from HTML |
| `/api/import/file` | POST | Import from uploaded file |
| `/api/chart/scores/<id>` | GET | Get chart data for archer |
| `/api/chart/compare` | GET | Compare multiple archers |

### Query Parameters

Most endpoints support filtering:
- `category`: Filter by category code (e.g., `C1`, `R1`)
- `distance`: Filter by distance (e.g., `18 m`, `720-runde`)

## Data Structure

### HTML Format (Input)

The scraper expects HTML with this structure:
```html
<div class="fRow datarows" onclick="location.href='/Event/Result/2025230'" title="Event Name">
    <div class="flexDate">
        <span>05 des. 25</span>
        <span class="hidden">2025</span>
    </div>
    <div class="flexSubRows">
        <div class="flexSubRowItem">Event Name</div>
        <div class="flexSubRowItem">
            <div class="flexItems">18 m</div>      <!-- Distance -->
            <div class="flexItems">C1</div>        <!-- Category -->
            <div class="flexItemsInfo">567</div>   <!-- Score -->
            <div class="flexItemsInfo">1</div>     <!-- Placement -->
        </div>
    </div>
</div>
```

### Database Schema

- **archers**: id, name, profile_url, timestamps
- **events**: id, event_id, name, event_url, timestamp
- **results**: id, archer_id, event_id, date, distance, category, score, placement

## Category Codes

| Code | Description |
|------|-------------|
| C1 | Compound Men |
| C2 | Compound Women |
| CH | Compound Men Senior |
| R1 | Recurve Men |
| R2 | Recurve Women |
| RH | Recurve Men Senior |
| RH5 | Recurve Men 50+ |
| R40 | Recurve Men 40+ |

## Project Structure

```
archery-tracker/
├── data/               # SQLite database
│   └── archery.db
├── src/
│   ├── __init__.py
│   ├── app.py          # Flask web server
│   ├── database.py     # Database operations
│   └── scraper.py      # HTML parser
├── static/             # Static files (CSS, JS)
├── templates/
│   └── index.html      # Main dashboard
├── requirements.txt
└── README.md
```

## Future Enhancements

- [ ] Automatic web scraping with scheduling
- [ ] Multi-archer comparison charts
- [ ] Export to CSV/Excel
- [ ] Personal records tracking
- [ ] Score progression predictions

## License

MIT License
