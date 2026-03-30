"""
Tests for src/scraper.py — HTML parsing and date conversion.
"""

import pytest
from src.scraper import (
    parse_norwegian_date,
    parse_result_html,
    parse_archer_page,
    get_category_description,
    normalize_distance,
)


# ---------------------------------------------------------------------------
# parse_norwegian_date
# ---------------------------------------------------------------------------

class TestParseNorwegianDate:
    def test_full_format_with_year_in_string(self):
        # "25 feb. 26" -> 2026-02-25
        assert parse_norwegian_date('25 feb. 26') == '2026-02-25'

    def test_full_format_all_months(self):
        months = {
            'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
            'mai': '05', 'jun': '06', 'jul': '07', 'aug': '08',
            'sep': '09', 'okt': '10', 'nov': '11', 'des': '12',
        }
        for abbr, num in months.items():
            result = parse_norwegian_date(f'01 {abbr}. 25')
            assert result == f'2025-{num}-01', f"Failed for month '{abbr}'"

    def test_year_provided_separately(self):
        # Two-part date + explicit year string
        assert parse_norwegian_date('16 jan', '2026') == '2026-01-16'

    def test_four_digit_year_passthrough(self):
        # Year already four digits
        assert parse_norwegian_date('06 feb. 2026') == '2026-02-06'

    def test_invalid_date_returns_original(self):
        bad = 'not-a-date'
        assert parse_norwegian_date(bad) == bad


# ---------------------------------------------------------------------------
# parse_result_html  (uses the real sample HTML file)
# ---------------------------------------------------------------------------

class TestParseResultHtml:
    def test_returns_three_results(self, sample_html):
        results = parse_result_html(sample_html)
        assert len(results) == 3

    def test_all_required_fields_present(self, sample_html):
        required = {'event_id', 'event_name', 'date', 'distance', 'category', 'score'}
        for r in parse_result_html(sample_html):
            assert required.issubset(r.keys()), f"Missing fields in: {r}"

    def test_first_result_values(self, sample_html):
        # Results come out in document order (newest first in the HTML)
        results = parse_result_html(sample_html)
        first = results[0]
        assert first['event_name'] == 'Kråkestevne'
        assert first['date'] == '2026-02-25'
        assert first['distance'] == '18 m'
        assert first['category'] == 'C1'
        assert first['score'] == 558
        assert first['placement'] == 4

    def test_second_result_values(self, sample_html):
        results = parse_result_html(sample_html)
        second = results[1]
        assert second['event_name'] == 'ULLR Cup 4'
        assert second['date'] == '2026-02-06'
        assert second['score'] == 279
        assert second['placement'] == 3

    def test_third_result_values(self, sample_html):
        results = parse_result_html(sample_html)
        third = results[2]
        assert third['event_name'] == 'ULLR Cup 3'
        assert third['date'] == '2026-01-16'
        assert third['score'] == 278
        assert third['placement'] == 2

    def test_empty_html_returns_empty_list(self):
        assert parse_result_html('<html><body></body></html>') == []


# ---------------------------------------------------------------------------
# parse_archer_page
# ---------------------------------------------------------------------------

class TestParseArcherPage:
    def test_returns_archer_info(self, sample_html):
        info = parse_archer_page(sample_html, external_id=1200)
        assert info is not None

    def test_archer_name(self, sample_html):
        info = parse_archer_page(sample_html, external_id=1200)
        assert info.name == 'Marius Kramer Haslestad'

    def test_archer_club(self, sample_html):
        info = parse_archer_page(sample_html, external_id=1200)
        assert 'Bærum' in info.club

    def test_external_id(self, sample_html):
        info = parse_archer_page(sample_html, external_id=1200)
        assert info.external_id == 1200

    def test_current_year_results_populated(self, sample_html):
        info = parse_archer_page(sample_html, external_id=1200)
        assert len(info.current_year_results) == 3

    def test_years_available_contains_multiple_years(self, sample_html):
        info = parse_archer_page(sample_html, external_id=1200)
        # The sample HTML has years 2018-2026 in the dropdown
        assert len(info.years_available) >= 2

    def test_wrong_id_logs_warning_but_still_parses(self, sample_html):
        # parse_archer_page logs a warning on ID mismatch but still returns data
        info = parse_archer_page(sample_html, external_id=9999)
        assert info is not None
        # The name is still correctly parsed from the page
        assert info.name == 'Marius Kramer Haslestad'


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

class TestGetCategoryDescription:
    def test_compound_men(self):
        assert 'Compound' in get_category_description('C1')

    def test_unknown_category_returns_code(self):
        desc = get_category_description('ZZ')
        assert 'ZZ' in desc


class TestNormalizeDistance:
    def test_18m_maps_to_indoor(self):
        # '18 m' is a known key that normalizes to a descriptive label
        assert normalize_distance('18 m') == '18m Indoor'

    def test_25m_maps_to_indoor(self):
        assert normalize_distance('25 m') == '25m Indoor'

    def test_unknown_distance_passthrough(self):
        # Unknown distances are returned unchanged
        assert normalize_distance('60 m') == '60 m'
