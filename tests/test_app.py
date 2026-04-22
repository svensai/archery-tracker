"""
Tests for src/app.py — Flask API endpoints.
Uses the flask_client fixture which points to a temporary database.
"""

import json
import os
import pytest


SAMPLE_HTML_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'eksempelresultat_1200.html'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json(response):
    return json.loads(response.data)


# ---------------------------------------------------------------------------
# Basic routes
# ---------------------------------------------------------------------------

class TestIndex:
    def test_index_returns_200(self, flask_client):
        resp = flask_client.get('/')
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/archers
# ---------------------------------------------------------------------------

class TestArchersEndpoint:
    def test_get_archers_empty(self, flask_client):
        resp = flask_client.get('/api/archers')
        assert resp.status_code == 200
        assert _json(resp) == []

    def test_add_archer(self, flask_client):
        resp = flask_client.post(
            '/api/archers',
            data=json.dumps({'name': 'Test Archer'}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        body = _json(resp)
        assert body['name'] == 'Test Archer'
        assert 'id' in body

    def test_add_archer_missing_name(self, flask_client):
        resp = flask_client.post(
            '/api/archers',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_archer_appears_in_list_after_add(self, flask_client):
        flask_client.post(
            '/api/archers',
            data=json.dumps({'name': 'Alice'}),
            content_type='application/json',
        )
        resp = flask_client.get('/api/archers')
        names = [a['name'] for a in _json(resp)]
        assert 'Alice' in names


# ---------------------------------------------------------------------------
# /api/import  (HTML import)
# ---------------------------------------------------------------------------

class TestImportEndpoint:
    def _sample_html(self):
        with open(SAMPLE_HTML_PATH, encoding='utf-8') as f:
            return f.read()

    def test_import_from_html_content(self, flask_client):
        resp = flask_client.post(
            '/api/import',
            data=json.dumps({
                'archer_name': 'Marius Kramer Haslestad',
                'html_content': self._sample_html(),
            }),
            content_type='application/json',
        )
        assert resp.status_code == 200
        body = _json(resp)
        assert body['imported_count'] == 3

    def test_import_missing_archer_name(self, flask_client):
        resp = flask_client.post(
            '/api/import',
            data=json.dumps({'html_content': '<html></html>'}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_import_missing_content(self, flask_client):
        resp = flask_client.post(
            '/api/import',
            data=json.dumps({'archer_name': 'Someone'}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_results_queryable_after_import(self, flask_client):
        flask_client.post(
            '/api/import',
            data=json.dumps({
                'archer_name': 'Marius Kramer Haslestad',
                'html_content': self._sample_html(),
            }),
            content_type='application/json',
        )
        # Get archer id
        archers = _json(flask_client.get('/api/archers'))
        archer_id = archers[0]['id']

        results = _json(flask_client.get(f'/api/archers/{archer_id}/results'))
        assert len(results) == 3
        scores = {r['score'] for r in results}
        assert 558 in scores
        assert 279 in scores
        assert 278 in scores


# ---------------------------------------------------------------------------
# /api/categories and /api/distances
# ---------------------------------------------------------------------------

class TestCategoriesDistances:
    def _import_sample(self, flask_client):
        with open(SAMPLE_HTML_PATH, encoding='utf-8') as f:
            html = f.read()
        flask_client.post(
            '/api/import',
            data=json.dumps({'archer_name': 'Archer', 'html_content': html}),
            content_type='application/json',
        )

    def test_categories_after_import(self, flask_client):
        self._import_sample(flask_client)
        resp = flask_client.get('/api/categories')
        codes = [c['code'] for c in _json(resp)]
        assert 'C1' in codes

    def test_distances_after_import(self, flask_client):
        self._import_sample(flask_client)
        resp = flask_client.get('/api/distances')
        assert '18 m' in _json(resp)


# ---------------------------------------------------------------------------
# /api/chart/yearly/*
# ---------------------------------------------------------------------------

class TestYearlyChartEndpoints:
    def _import_multi_year(self, flask_client):
        """Import 3 results across 2024 and 2025."""
        import json as _json_mod
        from src.database import add_archer, add_event, add_result
        archer_id = _json_mod.loads(flask_client.post(
            '/api/archers',
            data=_json_mod.dumps({'name': 'YearlyArcher'}),
            content_type='application/json',
        ).data)['id']

        # Directly insert results via API import (avoid needing DB fixture here)
        # Use the public add_* functions already exercised in test_database
        from src.database import add_event as _add_event, add_result as _add_result
        ev1 = _add_event('YE1', 'Ev2024a')
        ev2 = _add_event('YE2', 'Ev2024b')
        ev3 = _add_event('YE3', 'Ev2025a')
        _add_result(archer_id=archer_id, event_id=ev1, date='2024-01-10',
                    distance='18 m', category='C1', score=500)
        _add_result(archer_id=archer_id, event_id=ev2, date='2024-03-05',
                    distance='18 m', category='C1', score=540)
        _add_result(archer_id=archer_id, event_id=ev3, date='2025-02-20',
                    distance='18 m', category='C1', score=560)
        return archer_id

    def test_yearly_returns_200(self, flask_client):
        archer_id = self._import_multi_year(flask_client)
        resp = flask_client.get(f'/api/chart/yearly/{archer_id}')
        assert resp.status_code == 200

    def test_yearly_has_two_datasets(self, flask_client):
        archer_id = self._import_multi_year(flask_client)
        body = _json(flask_client.get(f'/api/chart/yearly/{archer_id}'))
        assert len(body['datasets']) == 2

    def test_yearly_snitt_dataset(self, flask_client):
        archer_id = self._import_multi_year(flask_client)
        body = _json(flask_client.get(f'/api/chart/yearly/{archer_id}'))
        snitt = next(d for d in body['datasets'] if d['label'] == 'Snitt')
        assert '2024' in snitt['years']
        assert '2025' in snitt['years']

    def test_yearly_empty_archer(self, flask_client):
        resp = flask_client.post(
            '/api/archers',
            data=__import__('json').dumps({'name': 'EmptyArcher'}),
            content_type='application/json',
        )
        archer_id = _json(resp)['id']
        body = _json(flask_client.get(f'/api/chart/yearly/{archer_id}'))
        assert body['datasets'] == []

    def test_yearly_compare_returns_200(self, flask_client):
        archer_id = self._import_multi_year(flask_client)
        resp = flask_client.get(f'/api/chart/yearly/compare?archer_ids={archer_id}')
        assert resp.status_code == 200

    def test_yearly_compare_missing_ids(self, flask_client):
        resp = flask_client.get('/api/chart/yearly/compare')
        assert resp.status_code == 400

    def test_yearly_compare_has_one_dataset_per_archer(self, flask_client):
        archer_id = self._import_multi_year(flask_client)
        body = _json(flask_client.get(f'/api/chart/yearly/compare?archer_ids={archer_id}'))
        assert len(body['datasets']) == 1
        assert body['datasets'][0]['label'] == 'YearlyArcher'


# ---------------------------------------------------------------------------
# /api/sync/*
# ---------------------------------------------------------------------------

class TestSyncEndpoints:
    def test_sync_running_returns_false_initially(self, flask_client):
        resp = flask_client.get('/api/sync/running')
        assert resp.status_code == 200
        assert _json(resp)['running'] is False

    def test_sync_status(self, flask_client):
        resp = flask_client.get('/api/sync/status')
        assert resp.status_code == 200
        body = _json(resp)
        assert 'total_archers_found' in body

    def test_sync_logs_empty(self, flask_client):
        resp = flask_client.get('/api/sync/logs')
        assert resp.status_code == 200
        assert isinstance(_json(resp), list)

    def test_sync_errors_empty(self, flask_client):
        resp = flask_client.get('/api/sync/errors')
        assert resp.status_code == 200
        assert isinstance(_json(resp), list)

    def test_stop_sync_when_not_running(self, flask_client):
        resp = flask_client.post('/api/sync/stop')
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /api/archers/<id>/results — date filtering
# ---------------------------------------------------------------------------

class TestDateFiltering:
    def _seed(self, flask_client):
        """Insert an archer with results spread across 2022-2025."""
        import json as _j
        from src.database import add_archer, add_event, add_result

        archer_id = _j.loads(flask_client.post(
            '/api/archers',
            data=_j.dumps({'name': 'DateFilterArcher'}),
            content_type='application/json',
        ).data)['id']

        for i, (eid, date, score) in enumerate([
            ('DF1', '2022-03-10', 490),
            ('DF2', '2023-09-01', 510),
            ('DF3', '2024-04-15', 530),
            ('DF4', '2025-01-20', 550),
        ]):
            ev = add_event(eid, f'DateEvent{i}')
            add_result(archer_id=archer_id, event_id=ev, date=date,
                       distance='18 m', category='C1', score=score)
        return archer_id

    def test_date_from_filters_results(self, flask_client):
        archer_id = self._seed(flask_client)
        resp = flask_client.get(f'/api/archers/{archer_id}/results?date_from=2024-01-01')
        results = _json(resp)
        assert resp.status_code == 200
        assert all(r['date'] >= '2024-01-01' for r in results)
        assert len(results) == 2

    def test_date_to_filters_results(self, flask_client):
        archer_id = self._seed(flask_client)
        resp = flask_client.get(f'/api/archers/{archer_id}/results?date_to=2022-12-31')
        results = _json(resp)
        assert len(results) == 1
        assert results[0]['date'] == '2022-03-10'

    def test_date_range_filters_results(self, flask_client):
        archer_id = self._seed(flask_client)
        resp = flask_client.get(
            f'/api/archers/{archer_id}/results?date_from=2023-01-01&date_to=2024-12-31'
        )
        results = _json(resp)
        assert len(results) == 2
        dates = {r['date'] for r in results}
        assert dates == {'2023-09-01', '2024-04-15'}

    def test_results_include_score_per_60(self, flask_client):
        archer_id = self._seed(flask_client)
        resp = flask_client.get(f'/api/archers/{archer_id}/results')
        results = _json(resp)
        assert all('score_per_60' in r for r in results)
        # 18m = 60 arrows, so score_per_60 == score
        for r in results:
            assert r['score_per_60'] == float(r['score'])

    def test_chart_compare_with_date_range(self, flask_client):
        archer_id = self._seed(flask_client)
        resp = flask_client.get(
            f'/api/chart/compare?archer_ids={archer_id}&date_from=2024-01-01'
        )
        assert resp.status_code == 200
        data = _json(resp)
        assert len(data['datasets']) == 1
        # Only 2025 and 2024 results
        assert len(data['datasets'][0]['dates']) == 2

    def test_chart_compare_score_per_60_present(self, flask_client):
        archer_id = self._seed(flask_client)
        resp = flask_client.get(f'/api/chart/compare?archer_ids={archer_id}')
        data = _json(resp)
        assert 'score_per_60' in data['datasets'][0]


# ---------------------------------------------------------------------------
# /api/backup endpoints
# ---------------------------------------------------------------------------

class TestBackupEndpoints:
    def test_create_backup_returns_200(self, flask_client, monkeypatch, tmp_path):
        backup_dir = str(tmp_path / 'backups')
        monkeypatch.setattr('src.backup.DATABASE_PATH', flask_client.application.config.get(
            'DATABASE_PATH', str(tmp_path / 'archery.db')))
        monkeypatch.setattr('src.backup.BACKUP_DIR', backup_dir)
        # Ensure there is a (possibly empty) sqlite file to back up
        import sqlite3, os
        db_path = str(tmp_path / 'archery.db')
        monkeypatch.setattr('src.backup.DATABASE_PATH', db_path)
        sqlite3.connect(db_path).close()

        resp = flask_client.post(
            '/api/backup',
            data=json.dumps({'label': 'test'}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        body = _json(resp)
        assert 'filename' in body
        assert 'size_bytes' in body
        assert body['filename'].endswith('.db')

    def test_list_backups_returns_list(self, flask_client, monkeypatch, tmp_path):
        monkeypatch.setattr('src.backup.BACKUP_DIR', str(tmp_path / 'backups'))
        resp = flask_client.get('/api/backup/list')
        assert resp.status_code == 200
        assert isinstance(_json(resp), list)

    def test_restore_backup_missing_filename(self, flask_client):
        resp = flask_client.post(
            '/api/backup/restore',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_restore_backup_nonexistent_file(self, flask_client, monkeypatch, tmp_path):
        monkeypatch.setattr('src.backup.BACKUP_DIR', str(tmp_path / 'backups'))
        resp = flask_client.post(
            '/api/backup/restore',
            data=json.dumps({'filename': 'nonexistent.db'}),
            content_type='application/json',
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /api/top-archers
# ---------------------------------------------------------------------------

class TestTopArchersEndpoint:
    def _seed(self, flask_client):
        """Insert 3 archers with results in 720-runde/RD category."""
        from src.database import add_archer, add_event, add_result
        a1 = json.loads(flask_client.post('/api/archers', data=json.dumps({'name': 'Archer Alpha'}),
                                          content_type='application/json').data)['id']
        a2 = json.loads(flask_client.post('/api/archers', data=json.dumps({'name': 'Archer Beta'}),
                                          content_type='application/json').data)['id']
        a3 = json.loads(flask_client.post('/api/archers', data=json.dumps({'name': 'Archer Gamma'}),
                                          content_type='application/json').data)['id']
        ev = add_event('TE1', 'TopEvent')
        add_result(archer_id=a1, event_id=ev, date='2025-03-01', distance='720-runde', category='RD', score=574)
        add_result(archer_id=a2, event_id=ev, date='2025-03-01', distance='720-runde', category='RD', score=497)
        add_result(archer_id=a3, event_id=ev, date='2025-03-01', distance='720-runde', category='RD', score=409)
        return [a1, a2, a3]

    def test_top_archers_empty_db(self, flask_client):
        resp = flask_client.get('/api/top-archers')
        assert resp.status_code == 200
        assert _json(resp) == []

    def test_top_archers_returns_ranked_list(self, flask_client):
        ids = self._seed(flask_client)
        resp = flask_client.get('/api/top-archers?n=3')
        assert resp.status_code == 200
        data = _json(resp)
        assert len(data) == 3
        assert data[0]['best_score'] == 574
        assert data[1]['best_score'] == 497
        assert data[2]['best_score'] == 409

    def test_top_archers_category_filter(self, flask_client):
        self._seed(flask_client)
        resp = flask_client.get('/api/top-archers?category=RD')
        assert resp.status_code == 200
        data = _json(resp)
        assert len(data) >= 1
        assert data[0]['best_score'] == 574

    def test_top_archers_n_clamped_to_2(self, flask_client):
        self._seed(flask_client)
        resp = flask_client.get('/api/top-archers?n=1')
        assert resp.status_code == 200
        assert len(_json(resp)) == 2

    def test_top_archers_n_clamped_to_10(self, flask_client):
        self._seed(flask_client)
        resp = flask_client.get('/api/top-archers?n=99')
        assert resp.status_code == 200
        assert len(_json(resp)) <= 10

    def test_top_archers_date_filter(self, flask_client):
        self._seed(flask_client)
        resp = flask_client.get('/api/top-archers?date_from=2025-01-01&date_to=2025-12-31')
        assert resp.status_code == 200
        data = _json(resp)
        assert len(data) >= 1


# ---------------------------------------------------------------------------
# /api/results/flagged
# ---------------------------------------------------------------------------

class TestFlaggedResultsEndpoint:
    def _seed(self, flask_client):
        from src.database import add_archer, add_event, add_result
        archer_id = json.loads(flask_client.post(
            '/api/archers',
            data=json.dumps({'name': 'Test Archer'}),
            content_type='application/json',
        ).data)['id']
        ev = add_event('FLG1', 'FlagEvent')
        # Valid: 18 m max = 600
        add_result(archer_id=archer_id, event_id=ev, date='2025-01-01',
                   distance='18 m', category='RD', score=580)
        # Invalid: 18 m max = 600, but score 650 exceeds it
        add_result(archer_id=archer_id, event_id=ev, date='2025-01-02',
                   distance='18 m', category='RD', score=650)
        # Unknown format (3D) — must NOT be flagged even if score seems high
        add_result(archer_id=archer_id, event_id=ev, date='2025-01-03',
                   distance='3D-stevne', category='RD', score=999)
        return archer_id

    def test_flagged_returns_200(self, flask_client):
        resp = flask_client.get('/api/results/flagged')
        assert resp.status_code == 200

    def test_flagged_empty_when_all_valid(self, flask_client):
        from src.database import add_archer, add_event, add_result
        archer_id = json.loads(flask_client.post(
            '/api/archers',
            data=json.dumps({'name': 'Clean Archer'}),
            content_type='application/json',
        ).data)['id']
        ev = add_event('FLG2', 'CleanEvent')
        add_result(archer_id=archer_id, event_id=ev, date='2025-01-01',
                   distance='18 m', category='RD', score=597)
        resp = flask_client.get('/api/results/flagged')
        assert _json(resp) == []

    def test_flagged_catches_over_max_score(self, flask_client):
        self._seed(flask_client)
        data = _json(flask_client.get('/api/results/flagged'))
        assert len(data) == 1
        assert data[0]['score'] == 650
        assert data[0]['max_score'] == 600
        assert data[0]['distance'] == '18 m'

    def test_unknown_format_not_flagged(self, flask_client):
        self._seed(flask_client)
        data = _json(flask_client.get('/api/results/flagged'))
        distances = [r['distance'] for r in data]
        assert '3D-stevne' not in distances

    def test_flagged_includes_score_per_60(self, flask_client):
        self._seed(flask_client)
        data = _json(flask_client.get('/api/results/flagged'))
        assert len(data) == 1
        assert 'score_per_60' in data[0]


# ---------------------------------------------------------------------------
# /api/chart/active-archers
# ---------------------------------------------------------------------------

class TestActiveArchersChart:
    def _seed(self, flask_client):
        from src.database import add_archer, add_event, add_result
        a1 = json.loads(flask_client.post('/api/archers', data=json.dumps({'name': 'A1'}),
                                          content_type='application/json').data)['id']
        a2 = json.loads(flask_client.post('/api/archers', data=json.dumps({'name': 'A2'}),
                                          content_type='application/json').data)['id']
        ev = add_event('ACT1', 'ActEvent')
        add_result(archer_id=a1, event_id=ev, date='2024-06-01', distance='18 m', category='C1', score=550)
        add_result(archer_id=a2, event_id=ev, date='2024-06-01', distance='18 m', category='R1', score=520)
        add_result(archer_id=a1, event_id=ev, date='2025-03-01', distance='18 m', category='C1', score=560)

    def test_returns_200(self, flask_client):
        resp = flask_client.get('/api/chart/active-archers')
        assert resp.status_code == 200

    def test_total_dataset_when_no_category(self, flask_client):
        self._seed(flask_client)
        data = _json(flask_client.get('/api/chart/active-archers'))
        assert 'years' in data and 'datasets' in data
        assert data['datasets'][0]['label'] == 'Totalt'
        assert '2024' in data['years']
        assert '2025' in data['years']

    def test_total_counts_distinct_archers(self, flask_client):
        self._seed(flask_client)
        data = _json(flask_client.get('/api/chart/active-archers'))
        idx_2024 = data['years'].index('2024')
        assert data['datasets'][0]['data'][idx_2024] == 2   # a1 and a2

    def test_per_category_datasets(self, flask_client):
        self._seed(flask_client)
        data = _json(flask_client.get('/api/chart/active-archers?categories=C1&categories=R1'))
        labels = [ds['label'] for ds in data['datasets']]
        assert 'C1' in labels and 'R1' in labels
        idx_2024 = data['years'].index('2024')
        c1_ds = next(ds for ds in data['datasets'] if ds['label'] == 'C1')
        assert c1_ds['data'][idx_2024] == 1

    def test_historical_incomplete_flag(self, flask_client):
        data = _json(flask_client.get('/api/chart/active-archers'))
        assert 'historical_incomplete' in data
        assert 'unsynced_count' in data

