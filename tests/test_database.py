"""
Tests for src/database.py — CRUD operations and sync tracking.
All tests use an isolated temporary SQLite file via the tmp_db fixture.
"""

import pytest
from src.database import (
    add_archer, add_event, add_result,
    get_all_archers, get_archer_results, get_all_results,
    get_categories, get_distances, get_years, get_archer_stats,
    get_archer_yearly_stats,
    add_archer_with_external_id,
    upsert_sync_status, get_sync_status, get_all_sync_status,
    get_max_external_id, mark_archer_not_found,
    log_sync_error, get_unresolved_errors, resolve_error,
    start_sync_log, update_sync_log, get_recent_sync_logs, get_current_sync_log,
    get_sync_stats,
    score_per_60, ARROWS_BY_DISTANCE,
)


# ---------------------------------------------------------------------------
# score_per_60 helper
# ---------------------------------------------------------------------------

class TestScorePer60:
    def test_18m_is_identity(self):
        """18m = 60 arrows, so score_per_60 should equal the raw score."""
        assert score_per_60(540, '18 m') == 540.0

    def test_25m_is_identity(self):
        assert score_per_60(570, '25 m') == 570.0

    def test_720_runde_normalised(self):
        """720-runde = 72 arrows → score_per_60 = score/72*60."""
        assert score_per_60(720, '720-runde') == round(720 / 72 * 60, 1)

    def test_1440_runde_normalised(self):
        assert score_per_60(1200, '1440-runde') == round(1200 / 144 * 60, 1)

    def test_unknown_distance_returns_none(self):
        """Unrecognised formats (3D, felt, ...) have no per-60 equivalent."""
        assert score_per_60(400, 'Ukjent format') is None
        assert score_per_60(300, '3D-stevne') is None

    def test_arrows_by_distance_has_expected_keys(self):
        for dist in ('18 m', '25 m', '720-runde', '1440-runde', '900-runde'):
            assert dist in ARROWS_BY_DISTANCE


# ---------------------------------------------------------------------------
# Archer CRUD
# ---------------------------------------------------------------------------

class TestArchers:
    def test_add_archer_returns_id(self, tmp_db):
        archer_id = add_archer('Test Archer')
        assert isinstance(archer_id, int)
        assert archer_id > 0

    def test_add_duplicate_archer_returns_same_id(self, tmp_db):
        id1 = add_archer('Same Name')
        id2 = add_archer('Same Name')
        assert id1 == id2

    def test_get_all_archers_empty(self, tmp_db):
        assert get_all_archers() == []

    def test_get_all_archers_after_insert(self, tmp_db):
        add_archer('Alice')
        add_archer('Bob')
        archers = get_all_archers()
        names = [a['name'] for a in archers]
        assert 'Alice' in names
        assert 'Bob' in names

    def test_add_archer_with_external_id(self, tmp_db):
        archer_id = add_archer_with_external_id(
            name='Marius Kramer Haslestad',
            external_id=1200,
            club='Bærum og Omegn Bueskyttere',
            club_id=8,
            profile_url='/Archer/Details/1200',
        )
        assert archer_id > 0
        archers = get_all_archers()
        assert any(a['name'] == 'Marius Kramer Haslestad' for a in archers)

    def test_add_archer_with_external_id_idempotent(self, tmp_db):
        id1 = add_archer_with_external_id(name='Archer X', external_id=42)
        id2 = add_archer_with_external_id(name='Archer X', external_id=42)
        assert id1 == id2

    def test_update_name_collision_does_not_crash_or_reassign(self, tmp_db):
        """Two different real people can share a name. Updating one archer's
        name to match a DIFFERENT existing archer must not raise, must not
        merge their identities, and must not leave the connection locked."""
        first_id = add_archer_with_external_id(name='Ola Nordmann', external_id=100)
        second_id = add_archer_with_external_id(name='Kari Nordmann', external_id=200)

        # Re-sync of archer 200 where the site now reports a name that
        # collides with archer 100's name.
        result_id = add_archer_with_external_id(
            name='Ola Nordmann', external_id=200, club='New Club',
        )
        assert result_id == second_id

        archers = {a['id']: a for a in get_all_archers()}
        assert archers[first_id]['name'] == 'Ola Nordmann'
        assert archers[second_id]['name'] == 'Kari Nordmann'  # not overwritten
        assert archers[second_id]['club'] == 'New Club'       # other fields still updated

        # Connection must not be left locked for subsequent writes.
        add_archer('Another Archer After Collision')


# ---------------------------------------------------------------------------
# Events and Results
# ---------------------------------------------------------------------------

class TestResults:
    def _setup_archer_event(self, tmp_db):
        archer_id = add_archer('Test Archer')
        event_id = add_event('2026055', 'Kråkestevne', '/Event/Result/2026055')
        return archer_id, event_id

    def test_add_event_returns_id(self, tmp_db):
        event_id = add_event('EVT001', 'Test Event')
        assert isinstance(event_id, int)
        assert event_id > 0

    def test_add_event_idempotent(self, tmp_db):
        id1 = add_event('EVT001', 'Test Event')
        id2 = add_event('EVT001', 'Test Event')
        assert id1 == id2

    def test_add_result(self, tmp_db):
        archer_id, event_id = self._setup_archer_event(tmp_db)
        add_result(
            archer_id=archer_id,
            event_id=event_id,
            date='2026-02-25',
            distance='18 m',
            category='C1',
            score=558,
            placement=4,
        )
        results = get_archer_results(archer_id)
        assert len(results) == 1
        assert results[0]['score'] == 558

    def test_result_includes_score_per_60(self, tmp_db):
        """Results returned from get_archer_results must contain score_per_60."""
        archer_id = add_archer('ScoreArcher')
        ev = add_event('SP1', 'Event')
        add_result(archer_id=archer_id, event_id=ev, date='2026-01-01',
                   distance='18 m', category='C1', score=540)
        results = get_archer_results(archer_id)
        assert 'score_per_60' in results[0]
        assert results[0]['score_per_60'] == 540.0  # 18m = 60 arrows

    def test_score_per_60_normalised_for_720(self, tmp_db):
        """720-runde results should have score_per_60 < score."""
        archer_id = add_archer('OutdoorArcher')
        ev = add_event('OUT1', 'Outdoor Event')
        add_result(archer_id=archer_id, event_id=ev, date='2026-06-15',
                   distance='720-runde', category='C1', score=648)
        results = get_archer_results(archer_id)
        assert results[0]['score_per_60'] == round(648 / 72 * 60, 1)

    def test_duplicate_result_does_not_raise(self, tmp_db):
        archer_id, event_id = self._setup_archer_event(tmp_db)
        kwargs = dict(archer_id=archer_id, event_id=event_id,
                      date='2026-02-25', distance='18 m',
                      category='C1', score=558)
        add_result(**kwargs)
        add_result(**kwargs)  # must not raise
        assert len(get_archer_results(archer_id)) == 1

    def test_get_archer_results_filter_by_category(self, tmp_db):
        archer_id = add_archer('Archer')
        ev1 = add_event('E1', 'Ev1')
        ev2 = add_event('E2', 'Ev2')
        add_result(archer_id=archer_id, event_id=ev1, date='2026-01-01',
                   distance='18 m', category='C1', score=500)
        add_result(archer_id=archer_id, event_id=ev2, date='2026-01-02',
                   distance='18 m', category='R1', score=600)

        c1_results = get_archer_results(archer_id, category='C1')
        assert len(c1_results) == 1
        assert c1_results[0]['category'] == 'C1'

    def test_get_archer_results_filter_by_date_from(self, tmp_db):
        archer_id = add_archer('DateArcher')
        ev1 = add_event('D1', 'Old Event')
        ev2 = add_event('D2', 'New Event')
        add_result(archer_id=archer_id, event_id=ev1, date='2023-06-01',
                   distance='18 m', category='C1', score=500)
        add_result(archer_id=archer_id, event_id=ev2, date='2025-01-15',
                   distance='18 m', category='C1', score=560)

        results = get_archer_results(archer_id, date_from='2024-01-01')
        assert len(results) == 1
        assert results[0]['date'] == '2025-01-15'

    def test_get_archer_results_filter_by_date_to(self, tmp_db):
        archer_id = add_archer('DateArcher2')
        ev1 = add_event('D3', 'Old')
        ev2 = add_event('D4', 'New')
        add_result(archer_id=archer_id, event_id=ev1, date='2023-06-01',
                   distance='18 m', category='C1', score=500)
        add_result(archer_id=archer_id, event_id=ev2, date='2025-01-15',
                   distance='18 m', category='C1', score=560)

        results = get_archer_results(archer_id, date_to='2023-12-31')
        assert len(results) == 1
        assert results[0]['date'] == '2023-06-01'

    def test_get_archer_results_filter_by_date_range(self, tmp_db):
        archer_id = add_archer('RangeArcher')
        for i, (eid, date, score) in enumerate([
            ('R1', '2022-03-01', 490),
            ('R2', '2023-08-15', 510),
            ('R3', '2024-02-20', 530),
            ('R4', '2025-11-01', 550),
        ]):
            ev = add_event(eid, f'Event {i}')
            add_result(archer_id=archer_id, event_id=ev, date=date,
                       distance='18 m', category='C1', score=score)

        results = get_archer_results(archer_id, date_from='2023-01-01', date_to='2024-12-31')
        assert len(results) == 2
        dates = {r['date'] for r in results}
        assert dates == {'2023-08-15', '2024-02-20'}

    def test_get_all_results_date_filter(self, tmp_db):
        archer_id = add_archer('AllArcher')
        ev1 = add_event('ALL1', 'E1')
        ev2 = add_event('ALL2', 'E2')
        add_result(archer_id=archer_id, event_id=ev1, date='2022-01-01',
                   distance='18 m', category='C1', score=500)
        add_result(archer_id=archer_id, event_id=ev2, date='2025-01-01',
                   distance='18 m', category='C1', score=560)

        results = get_all_results(date_from='2024-01-01')
        assert len(results) == 1
        assert results[0]['score'] == 560

    def test_get_all_results_score_per_60(self, tmp_db):
        archer_id = add_archer('AllArcher2')
        ev = add_event('ALL3', 'E')
        add_result(archer_id=archer_id, event_id=ev, date='2025-01-01',
                   distance='18 m', category='C1', score=555)
        results = get_all_results()
        assert 'score_per_60' in results[0]

    def test_get_all_results(self, tmp_db):
        archer_id = add_archer('Archer')
        ev = add_event('EV', 'Event')
        add_result(archer_id=archer_id, event_id=ev, date='2026-01-01',
                   distance='18 m', category='C1', score=555)
        results = get_all_results()
        assert len(results) == 1

    def test_get_categories(self, tmp_db):
        archer_id = add_archer('Archer')
        ev = add_event('EV', 'Event')
        add_result(archer_id=archer_id, event_id=ev, date='2026-01-01',
                   distance='18 m', category='C1', score=555)
        categories = get_categories()
        assert 'C1' in categories

    def test_get_distances(self, tmp_db):
        archer_id = add_archer('Archer')
        ev = add_event('EV', 'Event')
        add_result(archer_id=archer_id, event_id=ev, date='2026-01-01',
                   distance='18 m', category='C1', score=555)
        distances = get_distances()
        assert '18 m' in distances

    def test_get_years(self, tmp_db):
        archer_id = add_archer('Archer')
        ev1 = add_event('S1', 'Ev1')
        ev2 = add_event('S2', 'Ev2')
        add_result(archer_id=archer_id, event_id=ev1, date='2024-06-01',
                   distance='18 m', category='C1', score=500)
        add_result(archer_id=archer_id, event_id=ev2, date='2026-01-01',
                   distance='18 m', category='C1', score=555)
        years = get_years()
        assert years == [2026, 2024]

    def test_archer_stats(self, tmp_db):
        archer_id = add_archer('Archer')
        ev1 = add_event('S1', 'Ev1')
        ev2 = add_event('S2', 'Ev2')
        add_result(archer_id=archer_id, event_id=ev1, date='2026-01-01',
                   distance='18 m', category='C1', score=500)
        add_result(archer_id=archer_id, event_id=ev2, date='2026-01-08',
                   distance='18 m', category='C1', score=600)
        stats = get_archer_stats(archer_id)
        assert stats['total_competitions'] == 2
        assert stats['best_score'] == 600
        by_cat = stats['by_category']
        assert any(s['category'] == 'C1' for s in by_cat)
        c1 = next(s for s in by_cat if s['category'] == 'C1')
        assert c1['best_score'] == 600
        assert c1['competitions'] == 2


# ---------------------------------------------------------------------------
# Yearly stats
# ---------------------------------------------------------------------------

class TestArcherYearlyStats:
    def _setup(self, tmp_db):
        archer_id = add_archer('Archer')
        ev1 = add_event('Y1', 'Event 2024a')
        ev2 = add_event('Y2', 'Event 2024b')
        ev3 = add_event('Y3', 'Event 2025a')
        add_result(archer_id=archer_id, event_id=ev1, date='2024-01-10',
                   distance='18 m', category='C1', score=500)
        add_result(archer_id=archer_id, event_id=ev2, date='2024-03-05',
                   distance='18 m', category='C1', score=540)
        add_result(archer_id=archer_id, event_id=ev3, date='2025-02-20',
                   distance='18 m', category='C1', score=560)
        return archer_id

    def test_returns_two_years(self, tmp_db):
        archer_id = self._setup(tmp_db)
        rows = get_archer_yearly_stats(archer_id)
        assert len(rows) == 2

    def test_years_ordered_ascending(self, tmp_db):
        archer_id = self._setup(tmp_db)
        rows = get_archer_yearly_stats(archer_id)
        years = [r['year'] for r in rows]
        assert years == sorted(years)

    def test_correct_competitions_count(self, tmp_db):
        archer_id = self._setup(tmp_db)
        rows = get_archer_yearly_stats(archer_id)
        year_2024 = next(r for r in rows if r['year'] == '2024')
        assert year_2024['competitions'] == 2

    def test_correct_avg_score(self, tmp_db):
        archer_id = self._setup(tmp_db)
        rows = get_archer_yearly_stats(archer_id)
        year_2024 = next(r for r in rows if r['year'] == '2024')
        assert year_2024['avg_score'] == 520.0

    def test_correct_best_score(self, tmp_db):
        archer_id = self._setup(tmp_db)
        rows = get_archer_yearly_stats(archer_id)
        year_2024 = next(r for r in rows if r['year'] == '2024')
        assert year_2024['best_score'] == 540

    def test_filter_by_category(self, tmp_db):
        archer_id = add_archer('FilterArcher')
        ev1 = add_event('FC1', 'Ev1')
        ev2 = add_event('FC2', 'Ev2')
        add_result(archer_id=archer_id, event_id=ev1, date='2024-01-01',
                   distance='18 m', category='C1', score=500)
        add_result(archer_id=archer_id, event_id=ev2, date='2024-01-02',
                   distance='18 m', category='R1', score=600)
        rows = get_archer_yearly_stats(archer_id, category='C1')
        assert len(rows) == 1
        assert rows[0]['best_score'] == 500

    def test_filter_by_date_from(self, tmp_db):
        archer_id = self._setup(tmp_db)
        rows = get_archer_yearly_stats(archer_id, date_from='2025-01-01')
        assert len(rows) == 1
        assert rows[0]['year'] == '2025'

    def test_filter_by_date_range(self, tmp_db):
        archer_id = self._setup(tmp_db)
        rows = get_archer_yearly_stats(archer_id, date_from='2024-01-01', date_to='2024-12-31')
        assert len(rows) == 1
        assert rows[0]['year'] == '2024'
        assert rows[0]['competitions'] == 2

    def test_empty_for_unknown_archer(self, tmp_db):
        rows = get_archer_yearly_stats(9999)
        assert rows == []


# ---------------------------------------------------------------------------
# Sync status
# ---------------------------------------------------------------------------

class TestSyncStatus:
    def test_upsert_and_get(self, tmp_db):
        upsert_sync_status(external_id=1200, name='Marius', exists_online=True)
        status = get_sync_status(1200)
        assert status is not None
        assert status['name'] == 'Marius'
        assert status['exists_online']

    def test_get_nonexistent_returns_none(self, tmp_db):
        assert get_sync_status(9999) is None

    def test_upsert_updates_existing(self, tmp_db):
        upsert_sync_status(external_id=1200, name='Old Name')
        upsert_sync_status(external_id=1200, name='New Name')
        assert get_sync_status(1200)['name'] == 'New Name'

    def test_get_all_sync_status(self, tmp_db):
        upsert_sync_status(external_id=1, name='A', exists_online=True)
        upsert_sync_status(external_id=2, name='B', exists_online=True)
        all_statuses = get_all_sync_status(only_existing=True)
        assert len(all_statuses) >= 2

    def test_get_max_external_id_empty(self, tmp_db):
        assert get_max_external_id() == 0

    def test_get_max_external_id(self, tmp_db):
        upsert_sync_status(external_id=5, name='Five')
        upsert_sync_status(external_id=10, name='Ten')
        assert get_max_external_id() == 10

    def test_mark_archer_not_found(self, tmp_db):
        mark_archer_not_found(999)
        status = get_sync_status(999)
        assert status is not None
        assert not status['exists_online']


# ---------------------------------------------------------------------------
# Sync errors
# ---------------------------------------------------------------------------

class TestSyncErrors:
    def test_log_error(self, tmp_db):
        log_sync_error(1200, 'http_error', 'Connection refused',
                       http_status=503, request_url='/Archer/Details/1200')
        errors = get_unresolved_errors()
        assert len(errors) == 1
        assert errors[0]['error_type'] == 'http_error'

    def test_resolve_error(self, tmp_db):
        log_sync_error(1200, 'parse_error', 'Bad HTML')
        error_id = get_unresolved_errors()[0]['id']
        resolve_error(error_id)
        assert get_unresolved_errors() == []


# ---------------------------------------------------------------------------
# Sync log
# ---------------------------------------------------------------------------

class TestSyncLog:
    def test_start_and_update_log(self, tmp_db):
        log_id = start_sync_log('daily_sync')
        assert log_id > 0
        update_sync_log(log_id, archers_processed=10, results_added=5,
                        errors_count=0, status='completed')
        logs = get_recent_sync_logs(limit=1)
        assert logs[0]['status'] == 'completed'
        assert logs[0]['archers_processed'] == 10

    def test_get_sync_stats(self, tmp_db):
        upsert_sync_status(external_id=1, name='Archer', exists_online=True)
        stats = get_sync_stats()
        assert 'total_archers_found' in stats
        assert stats['total_archers_found'] >= 1

    def test_log_stores_total_and_progress(self, tmp_db):
        log_id = start_sync_log('historical_sync', total=42)
        update_sync_log(log_id, archers_processed=10, results_added=5,
                        errors_count=0, status='running')
        current = get_current_sync_log()
        assert current['id'] == log_id
        assert current['total'] == 42
        assert current['archers_processed'] == 10
        assert current['completed_at'] is None

    def test_stopped_job_gets_completed_at(self, tmp_db):
        log_id = start_sync_log('daily_sync', total=5)
        update_sync_log(log_id, archers_processed=2, status='stopped')
        current = get_current_sync_log()
        assert current['status'] == 'stopped'
        assert current['completed_at'] is not None

    def test_get_current_sync_log_empty(self, tmp_db):
        assert get_current_sync_log() is None
