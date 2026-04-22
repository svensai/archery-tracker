"""
Flask web application for archery results visualization.
"""

import os
import sys
import threading
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from flask_swagger_ui import get_swaggerui_blueprint
import bcrypt
from flask_login import LoginManager, login_required, current_user, login_user, logout_user
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from src.auth import User, generate_verification_token, send_verification_email, send_password_reset_email
from src.auth_db import (
    create_auth_tables, create_user, get_user_by_id, get_user_by_email,
    get_user_by_username, verify_user_token, set_verification_token,
    delete_user, get_all_users, ensure_admin_user,
    create_group, get_user_groups, get_group, delete_group,
    add_archer_to_group, remove_archer_from_group, get_group_archers,
    set_password_reset_token, get_user_by_reset_token, update_user_password,
)

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.database import (
    init_database, add_archer, add_event, add_result,
    get_all_archers, get_archer_results, get_all_results,
    get_categories, get_distances, get_archer_stats,
    get_archer_yearly_stats,
    get_events_list, get_event_results,
    get_sync_stats, get_all_sync_status, get_recent_sync_logs,
    get_unresolved_errors, resolve_error,
    get_top_archers, get_flagged_results, get_active_archers_per_year,
)
from src.scraper import parse_result_html, parse_html_file, get_category_description
from src.backup import create_backup, list_backups, restore_backup

app = Flask(__name__,
            template_folder='../templates',
            static_folder='../static')
CORS(app)

_secret = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
if _secret == 'dev-secret-change-in-production' and not os.environ.get('TESTING'):
    import warnings
    warnings.warn(
        "SECRET_KEY is using the insecure default. Set the SECRET_KEY environment variable.",
        stacklevel=1
    )
app.config['SECRET_KEY'] = _secret
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', '')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@archery.local')
app.config['BASE_URL'] = os.environ.get('BASE_URL', 'http://localhost:5001')

# Session / cookie security
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('HTTPS', 'false').lower() == 'true'

login_manager = LoginManager(app)

@login_manager.user_loader
def load_user(user_id):
    u = get_user_by_id(int(user_id))
    return User(u) if u else None

@login_manager.unauthorized_handler
def unauthorized():
    # In testing mode, don't block requests
    if app.config.get('TESTING'):
        return jsonify({'error': 'Authentication required', 'authenticated': False}), 401
    return jsonify({'error': 'Authentication required', 'authenticated': False}), 401

mail = Mail(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# Swagger UI at /api/docs
swaggerui_blueprint = get_swaggerui_blueprint(
    '/api/docs',
    '/static/openapi.yaml',
    config={'app_name': 'Archery Results Tracker API'},
)
app.register_blueprint(swaggerui_blueprint)

# Initialize database on startup
init_database()
create_auth_tables()
ensure_admin_user()

# Global reference to sync worker (for manual sync triggers)
_sync_worker = None
_sync_thread = None
_sync_lock = threading.Lock()


@app.route('/')
def index():
    """Main page with visualization."""
    return render_template('index.html')


@app.route('/api/archers', methods=['GET'])
@login_required
def api_get_archers():
    """Get all archers."""
    archers = get_all_archers()
    return jsonify(archers)


@app.route('/api/archers', methods=['POST'])
@login_required
def api_add_archer():
    """Add a new archer."""
    data = request.json
    name = data.get('name')
    profile_url = data.get('profile_url')

    if not name:
        return jsonify({'error': 'Name is required'}), 400

    archer_id = add_archer(name, profile_url)
    return jsonify({'id': archer_id, 'name': name})


@app.route('/api/archers/<int:archer_id>/results', methods=['GET'])
@login_required
def api_get_archer_results(archer_id):
    """Get results for an archer with optional filtering."""
    category = request.args.get('category')
    distance = request.args.get('distance')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    results = get_archer_results(archer_id, category, distance, date_from, date_to)
    return jsonify(results)


@app.route('/api/archers/<int:archer_id>/stats', methods=['GET'])
@login_required
def api_get_archer_stats(archer_id):
    """Get statistics for an archer."""
    stats = get_archer_stats(archer_id)
    return jsonify(stats)


@app.route('/api/results', methods=['GET'])
@login_required
def api_get_results():
    """Get all results with optional filtering."""
    category = request.args.get('category')
    distance = request.args.get('distance')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    results = get_all_results(category, distance, date_from, date_to)
    return jsonify(results)


@app.route('/api/results/flagged', methods=['GET'])
@login_required
def api_get_flagged_results():
    """Get results whose score exceeds the theoretical maximum for their format.

    Returns a list of suspicious results with an added 'max_score' field
    indicating the threshold that was breached.  Formats without a known
    arrow count (3D, felt, etc.) are not included.
    """
    flagged = get_flagged_results()
    return jsonify(flagged)


@app.route('/api/chart/active-archers', methods=['GET'])
@login_required
def api_chart_active_archers():
    """Active distinct archers per year, optionally split by category.

    Query params:
      categories  — repeat for each desired class, e.g. ?categories=C1&categories=R1
                    Omit to get a single 'Totalt' dataset across all categories.
    """
    categories = request.args.getlist('categories') or None
    data = get_active_archers_per_year(categories)
    return jsonify(data)


@app.route('/api/categories', methods=['GET'])
@login_required
def api_get_categories():
    """Get all unique categories."""
    categories = get_categories()
    # Add descriptions
    categories_with_desc = [
        {'code': c, 'description': get_category_description(c)}
        for c in categories
    ]
    return jsonify(categories_with_desc)


@app.route('/api/distances', methods=['GET'])
@login_required
def api_get_distances():
    """Get all unique distances."""
    distances = get_distances()
    return jsonify(distances)


@app.route('/api/top-archers', methods=['GET'])
@login_required
def api_top_archers():
    """Return the top N archers by best score, with optional filters.

    Query parameters:
    - n: number of archers (2-10, default 5)
    - category: category code (e.g. RD, RH)
    - distance: distance string (e.g. 720-runde)
    - date_from: ISO date string (YYYY-MM-DD)
    - date_to: ISO date string (YYYY-MM-DD)
    """
    try:
        n = int(request.args.get('n', 5))
    except (ValueError, TypeError):
        n = 5
    category = request.args.get('category') or None
    distance = request.args.get('distance') or None
    date_from = request.args.get('date_from') or None
    date_to = request.args.get('date_to') or None
    result = get_top_archers(n=n, category=category, distance=distance,
                             date_from=date_from, date_to=date_to)
    return jsonify(result)


@app.route('/api/import', methods=['POST'])
@login_required
def api_import_results():
    """
    Import results from HTML content or file.
    Expects JSON with:
    - archer_name: Name of the archer
    - html_content: HTML content to parse
    OR
    - html_file: Path to HTML file
    """
    data = request.json
    archer_name = data.get('archer_name')
    html_content = data.get('html_content')
    html_file = data.get('html_file')

    if not archer_name:
        return jsonify({'error': 'archer_name is required'}), 400

    if not html_content and not html_file:
        return jsonify({'error': 'html_content or html_file is required'}), 400

    # Add or get archer
    archer_id = add_archer(archer_name)

    # Parse HTML
    if html_file:
        try:
            results = parse_html_file(html_file)
        except FileNotFoundError:
            return jsonify({'error': f'File not found: {html_file}'}), 400
    else:
        results = parse_result_html(html_content)

    # Import results
    imported_count = 0
    for result in results:
        # Add or get event
        event_db_id = add_event(
            result['event_id'],
            result['event_name'],
            result.get('event_url')
        )

        # Add result
        add_result(
            archer_id=archer_id,
            event_id=event_db_id,
            date=result['date'],
            distance=result['distance'],
            category=result['category'],
            score=result['score'],
            placement=result.get('placement')
        )
        imported_count += 1

    return jsonify({
        'message': f'Imported {imported_count} results for {archer_name}',
        'archer_id': archer_id,
        'imported_count': imported_count
    })


@app.route('/api/import/file', methods=['POST'])
@login_required
def api_import_from_file():
    """
    Import results from uploaded file.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    archer_name = request.form.get('archer_name')

    if not archer_name:
        return jsonify({'error': 'archer_name is required'}), 400

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Read file content
    html_content = file.read().decode('utf-8')

    # Add or get archer
    archer_id = add_archer(archer_name)

    # Parse and import
    results = parse_result_html(html_content)
    imported_count = 0

    for result in results:
        event_db_id = add_event(
            result['event_id'],
            result['event_name'],
            result.get('event_url')
        )

        add_result(
            archer_id=archer_id,
            event_id=event_db_id,
            date=result['date'],
            distance=result['distance'],
            category=result['category'],
            score=result['score'],
            placement=result.get('placement')
        )
        imported_count += 1

    return jsonify({
        'message': f'Imported {imported_count} results for {archer_name}',
        'archer_id': archer_id,
        'imported_count': imported_count
    })


@app.route('/api/chart/scores/<int:archer_id>', methods=['GET'])
@login_required
def api_chart_scores(archer_id):
    """
    Get chart data for archer's scores over time.
    Groups by distance/category for comparison.
    """
    category = request.args.get('category')
    distance = request.args.get('distance')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    results = get_archer_results(archer_id, category, distance, date_from, date_to)

    # Sort by date ascending for chronological chart
    results.sort(key=lambda x: x['date'])

    # Group by distance for multi-line chart
    datasets = {}
    for r in results:
        key = f"{r['distance']} - {r['category']}"
        if key not in datasets:
            datasets[key] = {
                'label': key,
                'data': [],
                'dates': []
            }
        datasets[key]['data'].append(r['score'])
        datasets[key]['dates'].append(r['date'])

    return jsonify({
        'datasets': list(datasets.values()),
        'archer_name': results[0]['archer_name'] if results else ''
    })


@app.route('/api/chart/compare', methods=['GET'])
@login_required
def api_chart_compare():
    """
    Compare multiple archers.
    """
    archer_ids = request.args.getlist('archer_ids', type=int)
    category = request.args.get('category')
    distance = request.args.get('distance')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    if not archer_ids:
        return jsonify({'error': 'archer_ids required'}), 400

    datasets = []
    for archer_id in archer_ids:
        results = get_archer_results(archer_id, category, distance, date_from, date_to)
        results.sort(key=lambda x: x['date'])

        if results:
            archer_name = results[0]['archer_name']
            datasets.append({
                'label': archer_name,
                'data': [r['score'] for r in results],
                'score_per_60': [r['score_per_60'] for r in results],
                'dates': [r['date'] for r in results],
            })

    return jsonify({'datasets': datasets})


@app.route('/api/chart/yearly/<int:archer_id>', methods=['GET'])
@login_required
def api_chart_yearly(archer_id):
    """
    Get per-year average and best scores for one archer.
    Optional query params: category, distance, date_from, date_to.
    """
    category = request.args.get('category')
    distance = request.args.get('distance')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    rows = get_archer_yearly_stats(archer_id, category, distance, date_from, date_to)

    archers = get_all_archers()
    archer_name = next((a['name'] for a in archers if a['id'] == archer_id), '')

    if not rows:
        return jsonify({'archer_name': archer_name, 'datasets': []})

    years = [r['year'] for r in rows]
    datasets = [
        {
            'label': 'Snitt',
            'data': [r['avg_score'] for r in rows],
            'years': years,
            'competitions': [r['competitions'] for r in rows],
        },
        {
            'label': 'Best',
            'data': [r['best_score'] for r in rows],
            'years': years,
        },
    ]
    return jsonify({'archer_name': archer_name, 'datasets': datasets})


@app.route('/api/chart/yearly/compare', methods=['GET'])
@login_required
def api_chart_yearly_compare():
    """
    Compare yearly average scores across multiple archers.
    Query params: archer_ids (multi), category, distance, date_from, date_to.
    """
    archer_ids = request.args.getlist('archer_ids', type=int)
    category = request.args.get('category')
    distance = request.args.get('distance')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    if not archer_ids:
        return jsonify({'error': 'archer_ids required'}), 400

    all_years: set = set()
    archer_rows: dict = {}
    archers = get_all_archers()
    archer_name_map = {a['id']: a['name'] for a in archers}

    for archer_id in archer_ids:
        rows = get_archer_yearly_stats(archer_id, category, distance, date_from, date_to)
        archer_rows[archer_id] = {r['year']: r for r in rows}
        all_years.update(r['year'] for r in rows)

    years = sorted(all_years)
    datasets = []
    for archer_id in archer_ids:
        row_map = archer_rows[archer_id]
        datasets.append({
            'label': archer_name_map.get(archer_id, str(archer_id)),
            'data': [row_map[y]['avg_score'] if y in row_map else None for y in years],
            'years': years,
        })

    return jsonify({'datasets': datasets})


# ==================== Sync API Endpoints ====================

@app.route('/api/sync/status', methods=['GET'])
@login_required
def api_sync_status():
    """Get overall sync statistics."""
    stats = get_sync_stats()
    return jsonify(stats)


@app.route('/api/sync/archers', methods=['GET'])
@login_required
def api_sync_archers():
    """Get sync status for all archers."""
    only_existing = request.args.get('only_existing', 'true').lower() == 'true'
    limit = request.args.get('limit', type=int)

    archers = get_all_sync_status(only_existing=only_existing, limit=limit)
    return jsonify(archers)


@app.route('/api/sync/logs', methods=['GET'])
@login_required
def api_sync_logs():
    """Get recent sync job logs."""
    limit = request.args.get('limit', 20, type=int)
    logs = get_recent_sync_logs(limit)
    return jsonify(logs)


@app.route('/api/sync/errors', methods=['GET'])
@login_required
def api_sync_errors():
    """Get unresolved sync errors."""
    limit = request.args.get('limit', 100, type=int)
    errors = get_unresolved_errors(limit)
    return jsonify(errors)


@app.route('/api/sync/errors/<int:error_id>/resolve', methods=['POST'])
@login_required
def api_resolve_error(error_id):
    """Mark a sync error as resolved."""
    resolve_error(error_id)
    return jsonify({'message': f'Error {error_id} marked as resolved'})


@app.route('/api/sync/start', methods=['POST'])
@login_required
def api_start_sync():
    """
    Start a sync operation.

    Expects JSON with:
    - job_type: 'daily', 'discovery', 'historical', or 'single'
    - archer_id: (optional) for single archer sync
    - start_id: (optional) for discovery start
    - end_id: (optional) for discovery end
    """
    global _sync_worker, _sync_thread

    data = request.json or {}
    job_type = data.get('job_type', 'daily')

    with _sync_lock:
        # Check if sync is already running
        if _sync_thread and _sync_thread.is_alive():
            return jsonify({'error': 'Sync already running'}), 409

        def run_sync():
            global _sync_worker
            from src.sync.worker import SyncWorker

            try:
                with _sync_lock:
                    _sync_worker = SyncWorker()
                worker = _sync_worker

                if job_type == 'daily':
                    worker.run_daily_sync()
                elif job_type == 'discovery':
                    start_id = data.get('start_id', 1)
                    end_id = data.get('end_id')
                    worker.discover_archers(start_id=start_id, end_id=end_id)
                elif job_type == 'historical':
                    archer_ids = data.get('archer_ids')
                    years = data.get('years')
                    worker.sync_historical(archer_ids=archer_ids, years=years)
                elif job_type == 'single':
                    archer_id = data.get('archer_id')
                    if archer_id:
                        worker.sync_current_season(archer_ids=[archer_id])
                else:
                    return

            except Exception as e:
                app.logger.error(f"Sync error: {e}")
            finally:
                with _sync_lock:
                    if _sync_worker:
                        _sync_worker.close()
                    _sync_worker = None

        _sync_thread = threading.Thread(target=run_sync, daemon=True)
        _sync_thread.start()

    return jsonify({
        'message': f'Started {job_type} sync',
        'job_type': job_type
    })


@app.route('/api/sync/stop', methods=['POST'])
@login_required
def api_stop_sync():
    """Stop the currently running sync operation."""
    global _sync_worker

    with _sync_lock:
        worker = _sync_worker

    if worker:
        worker.stop()
        return jsonify({'message': 'Stop signal sent'})
    else:
        return jsonify({'message': 'No sync running'}), 404


@app.route('/api/sync/running', methods=['GET'])
@login_required
def api_sync_running():
    """Check if sync is currently running."""
    with _sync_lock:
        running = _sync_thread is not None and _sync_thread.is_alive()
    return jsonify({'running': running})


# ==================== Events / Competitions API ====================

@app.route('/api/events', methods=['GET'])
@login_required
def api_get_events():
    """
    List competition rounds with stats.
    Query params: category, distance, archer_id, date_from, date_to
    """
    category  = request.args.get('category')
    distance  = request.args.get('distance')
    archer_id = request.args.get('archer_id', type=int)
    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to')

    events = get_events_list(category, distance, archer_id, date_from, date_to)
    return jsonify(events)


@app.route('/api/events/<int:event_db_id>/results', methods=['GET'])
@login_required
def api_get_event_results(event_db_id):
    """
    All archer results for a specific event.
    Query params: distance, category
    """
    distance = request.args.get('distance')
    category = request.args.get('category')

    results = get_event_results(event_db_id, distance, category)
    return jsonify(results)


# ==================== Backup API Endpoints ====================

@app.route('/api/backup', methods=['POST'])
@login_required
def api_create_backup():
    """Create a database backup. Optional JSON body: {"label": "before_migration"}"""
    data = request.json or {}
    label = data.get('label', '')
    info = create_backup(label)
    return jsonify(info)


@app.route('/api/backup/list', methods=['GET'])
@login_required
def api_list_backups():
    """List all available database backups."""
    return jsonify(list_backups())


@app.route('/api/backup/restore', methods=['POST'])
@login_required
def api_restore_backup():
    """
    Restore a backup. Expects JSON: {"filename": "archery_20250330_143000.db"}
    Automatically creates a safety backup before restoring.
    """
    data = request.json or {}
    filename = data.get('filename')
    if not filename:
        return jsonify({'error': 'filename is required'}), 400
    try:
        result = restore_backup(filename)
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404


# ==================== Auth API ====================

@app.route('/api/auth/me', methods=['GET'])
def api_auth_me():
    if current_user.is_authenticated:
        return jsonify({
            'authenticated': True,
            'user': {
                'id': current_user.id,
                'username': current_user.username,
                'email': current_user.email,
                'is_admin': current_user.is_admin,
            }
        })
    return jsonify({'authenticated': False})


@app.route('/api/auth/register', methods=['POST'])
@limiter.limit("10 per hour")
def api_auth_register():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not email or '@' not in email:
        return jsonify({'error': 'Ugyldig e-post'}), 400
    if len(username) < 3:
        return jsonify({'error': 'Brukernavn må ha minst 3 tegn'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Passord må ha minst 8 tegn'}), 400

    if get_user_by_email(email):
        return jsonify({'error': 'E-posten er allerede registrert'}), 409
    if get_user_by_username(username):
        return jsonify({'error': 'Brukernavnet er allerede tatt'}), 409

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    token = generate_verification_token()
    user_id = create_user(email, username, pw_hash, token)
    send_verification_email(app, mail, email, token)

    return jsonify({'message': 'Registrering vellykket. Sjekk e-posten din.', 'user_id': user_id}), 201


@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("20 per minute; 100 per hour")
def api_auth_login():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    user_dict = get_user_by_email(email)
    if not user_dict or not bcrypt.checkpw(password.encode(), user_dict['password_hash'].encode()):
        return jsonify({'error': 'Feil e-post eller passord'}), 401

    if not user_dict['is_verified']:
        return jsonify({'error': 'E-post ikke verifisert. Sjekk innboksen din.'}), 403

    user = User(user_dict)
    login_user(user, remember=True)
    return jsonify({
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_admin': user.is_admin,
        }
    })


@app.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    logout_user()
    return jsonify({'message': 'Logget ut'})


@app.route('/api/auth/verify/<token>', methods=['GET'])
def api_auth_verify(token):
    if verify_user_token(token):
        return '<html><body><script>window.location="/?verified=1"</script><p>Verified! <a href="/">Go to app</a></p></body></html>'
    return jsonify({'error': 'Ugyldig eller utløpt bekreftelseslenke'}), 400


@app.route('/api/auth/forgot-password', methods=['POST'])
@limiter.limit("5 per hour")
def api_auth_forgot_password():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    user_dict = get_user_by_email(email)
    if user_dict:
        token = generate_verification_token()
        set_password_reset_token(user_dict['id'], token)
        send_password_reset_email(app, mail, email, token)
    # Always 200 to avoid leaking whether email is registered
    return jsonify({'message': 'Hvis e-posten er registrert, er en tilbakestillingslenke sendt.'})


@app.route('/api/auth/reset-password', methods=['POST'])
def api_auth_reset_password():
    data = request.json or {}
    token = (data.get('token') or '').strip()
    password = data.get('password') or ''
    if len(password) < 8:
        return jsonify({'error': 'Passord må ha minst 8 tegn'}), 400
    user_dict = get_user_by_reset_token(token)
    if not user_dict:
        return jsonify({'error': 'Ugyldig eller utløpt lenke'}), 400
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    update_user_password(user_dict['id'], pw_hash)
    return jsonify({'message': 'Passord tilbakestilt. Du kan nå logge inn.'})


@app.route('/api/auth/resend-verification', methods=['POST'])
def api_auth_resend():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    user_dict = get_user_by_email(email)
    if user_dict and not user_dict['is_verified']:
        token = generate_verification_token()
        set_verification_token(user_dict['id'], token)
        send_verification_email(app, mail, email, token)
    # Always return 200 to avoid leaking whether email exists
    return jsonify({'message': 'Hvis e-posten er registrert og uverifisert, er en ny lenke sendt.'})


# ==================== Groups API ====================

@app.route('/api/groups', methods=['GET'])
@login_required
def api_get_groups():
    groups = get_user_groups(current_user.id)
    return jsonify(groups)


@app.route('/api/groups', methods=['POST'])
@login_required
def api_create_group():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Gruppenavn er påkrevd'}), 400
    try:
        group_id = create_group(current_user.id, name)
        return jsonify({'id': group_id, 'name': name, 'archer_count': 0}), 201
    except Exception:
        return jsonify({'error': 'Gruppenavn er allerede i bruk'}), 409


@app.route('/api/groups/<int:group_id>', methods=['DELETE'])
@login_required
def api_delete_group(group_id):
    group = get_group(group_id)
    if not group or group['user_id'] != current_user.id:
        return jsonify({'error': 'Ikke funnet'}), 404
    delete_group(group_id)
    return jsonify({'message': 'Gruppe slettet'})


@app.route('/api/groups/<int:group_id>/archers', methods=['GET'])
@login_required
def api_get_group_archers(group_id):
    group = get_group(group_id)
    if not group or group['user_id'] != current_user.id:
        return jsonify({'error': 'Ikke funnet'}), 404
    return jsonify(get_group_archers(group_id))


@app.route('/api/groups/<int:group_id>/archers', methods=['POST'])
@login_required
def api_add_archer_to_group(group_id):
    group = get_group(group_id)
    if not group or group['user_id'] != current_user.id:
        return jsonify({'error': 'Ikke funnet'}), 404
    archer_id = (request.json or {}).get('archer_id')
    if not archer_id:
        return jsonify({'error': 'archer_id påkrevd'}), 400
    add_archer_to_group(group_id, archer_id)
    return jsonify({'message': 'Skytter lagt til'})


@app.route('/api/groups/<int:group_id>/archers/<int:archer_id>', methods=['DELETE'])
@login_required
def api_remove_archer_from_group(group_id, archer_id):
    group = get_group(group_id)
    if not group or group['user_id'] != current_user.id:
        return jsonify({'error': 'Ikke funnet'}), 404
    remove_archer_from_group(group_id, archer_id)
    return jsonify({'message': 'Skytter fjernet'})


# ==================== Admin API ====================

def _require_admin():
    if not current_user.is_authenticated or not current_user.is_admin:
        return jsonify({'error': 'Krever admin-tilgang'}), 403
    return None


@app.route('/api/admin/users', methods=['GET'])
@login_required
def api_admin_list_users():
    err = _require_admin()
    if err: return err
    return jsonify(get_all_users())


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@login_required
def api_admin_delete_user(user_id):
    err = _require_admin()
    if err: return err
    if user_id == current_user.id:
        return jsonify({'error': 'Kan ikke slette seg selv'}), 400
    delete_user(user_id)
    return jsonify({'message': 'Bruker slettet'})


if __name__ == '__main__':
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, port=5001)
