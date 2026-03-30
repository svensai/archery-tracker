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
