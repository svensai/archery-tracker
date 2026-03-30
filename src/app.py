"""
Flask web application for archery results visualization.
"""

import os
import sys
import threading
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.database import (
    init_database, add_archer, add_event, add_result,
    get_all_archers, get_archer_results, get_all_results,
    get_categories, get_distances, get_archer_stats,
    get_archer_yearly_stats,
    get_sync_stats, get_all_sync_status, get_recent_sync_logs,
    get_unresolved_errors, resolve_error
)
from src.scraper import parse_result_html, parse_html_file, get_category_description
from src.backup import create_backup, list_backups, restore_backup

app = Flask(__name__, 
            template_folder='../templates',
            static_folder='../static')
CORS(app)

# Initialize database on startup
init_database()

# Global reference to sync worker (for manual sync triggers)
_sync_worker = None
_sync_thread = None
_sync_lock = threading.Lock()


@app.route('/')
def index():
    """Main page with visualization."""
    return render_template('index.html')


@app.route('/api/archers', methods=['GET'])
def api_get_archers():
    """Get all archers."""
    archers = get_all_archers()
    return jsonify(archers)


@app.route('/api/archers', methods=['POST'])
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
def api_get_archer_results(archer_id):
    """Get results for an archer with optional filtering."""
    category = request.args.get('category')
    distance = request.args.get('distance')
    
    results = get_archer_results(archer_id, category, distance)
    return jsonify(results)


@app.route('/api/archers/<int:archer_id>/stats', methods=['GET'])
def api_get_archer_stats(archer_id):
    """Get statistics for an archer."""
    stats = get_archer_stats(archer_id)
    return jsonify(stats)


@app.route('/api/results', methods=['GET'])
def api_get_results():
    """Get all results with optional filtering."""
    category = request.args.get('category')
    distance = request.args.get('distance')
    
    results = get_all_results(category, distance)
    return jsonify(results)


@app.route('/api/categories', methods=['GET'])
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
def api_get_distances():
    """Get all unique distances."""
    distances = get_distances()
    return jsonify(distances)


@app.route('/api/import', methods=['POST'])
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
def api_chart_scores(archer_id):
    """
    Get chart data for archer's scores over time.
    Groups by distance/category for comparison.
    """
    category = request.args.get('category')
    distance = request.args.get('distance')
    
    results = get_archer_results(archer_id, category, distance)
    
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
def api_chart_compare():
    """
    Compare multiple archers.
    """
    archer_ids = request.args.getlist('archer_ids', type=int)
    category = request.args.get('category')
    distance = request.args.get('distance')
    
    if not archer_ids:
        return jsonify({'error': 'archer_ids required'}), 400
    
    datasets = []
    for archer_id in archer_ids:
        results = get_archer_results(archer_id, category, distance)
        results.sort(key=lambda x: x['date'])
        
        if results:
            archer_name = results[0]['archer_name']
            datasets.append({
                'label': archer_name,
                'data': [r['score'] for r in results],
                'dates': [r['date'] for r in results]
            })
    
    return jsonify({'datasets': datasets})


@app.route('/api/chart/yearly/<int:archer_id>', methods=['GET'])
def api_chart_yearly(archer_id):
    """
    Get per-year average and best scores for one archer.
    Optional query params: category, distance.
    """
    category = request.args.get('category')
    distance = request.args.get('distance')

    rows = get_archer_yearly_stats(archer_id, category, distance)

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
def api_chart_yearly_compare():
    """
    Compare yearly average scores across multiple archers.
    Query params: archer_ids (multi), category, distance.
    """
    archer_ids = request.args.getlist('archer_ids', type=int)
    category = request.args.get('category')
    distance = request.args.get('distance')

    if not archer_ids:
        return jsonify({'error': 'archer_ids required'}), 400

    all_years: set = set()
    archer_rows: dict = {}
    archers = get_all_archers()
    archer_name_map = {a['id']: a['name'] for a in archers}

    for archer_id in archer_ids:
        rows = get_archer_yearly_stats(archer_id, category, distance)
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
def api_sync_status():
    """Get overall sync statistics."""
    stats = get_sync_stats()
    return jsonify(stats)


@app.route('/api/sync/archers', methods=['GET'])
def api_sync_archers():
    """Get sync status for all archers."""
    only_existing = request.args.get('only_existing', 'true').lower() == 'true'
    limit = request.args.get('limit', type=int)
    
    archers = get_all_sync_status(only_existing=only_existing, limit=limit)
    return jsonify(archers)


@app.route('/api/sync/logs', methods=['GET'])
def api_sync_logs():
    """Get recent sync job logs."""
    limit = request.args.get('limit', 20, type=int)
    logs = get_recent_sync_logs(limit)
    return jsonify(logs)


@app.route('/api/sync/errors', methods=['GET'])
def api_sync_errors():
    """Get unresolved sync errors."""
    limit = request.args.get('limit', 100, type=int)
    errors = get_unresolved_errors(limit)
    return jsonify(errors)


@app.route('/api/sync/errors/<int:error_id>/resolve', methods=['POST'])
def api_resolve_error(error_id):
    """Mark a sync error as resolved."""
    resolve_error(error_id)
    return jsonify({'message': f'Error {error_id} marked as resolved'})


@app.route('/api/sync/start', methods=['POST'])
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
def api_sync_running():
    """Check if sync is currently running."""
    with _sync_lock:
        running = _sync_thread is not None and _sync_thread.is_alive()
    return jsonify({'running': running})


# ==================== Backup API Endpoints ====================

@app.route('/api/backup', methods=['POST'])
def api_create_backup():
    """Create a database backup. Optional JSON body: {"label": "before_migration"}"""
    data = request.json or {}
    label = data.get('label', '')
    info = create_backup(label)
    return jsonify(info)


@app.route('/api/backup/list', methods=['GET'])
def api_list_backups():
    """List all available database backups."""
    return jsonify(list_backups())


@app.route('/api/backup/restore', methods=['POST'])
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


if __name__ == '__main__':
    app.run(debug=True, port=5001)
