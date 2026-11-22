"""Unit tests for federal fiscal-calendar helpers."""

from __future__ import annotations

from datetime import date

import pytest
from ingestion.models import (
    federal_fiscal_month,
    federal_fiscal_quarter,
    federal_fiscal_year,
)


@pytest.mark.parametrize(
    "d,fy,fm,fq",
    [
        (date(2025, 10, 1), 2026, 1, 1),  # first day of FY2026
        (date(2025, 11, 15), 2026, 2, 1),
        (date(2025, 12, 31), 2026, 3, 1),
        (date(2026, 1, 1), 2026, 4, 2),
        (date(2026, 3, 31), 2026, 6, 2),
        (date(2026, 4, 1), 2026, 7, 3),
        (date(2026, 6, 30), 2026, 9, 3),
        (date(2026, 7, 1), 2026, 10, 4),
        (date(2026, 9, 30), 2026, 12, 4),  # last day of FY2026
    ],
)
def test_fiscal_calendar(d, fy, fm, fq):
    assert federal_fiscal_year(d) == fy
    assert federal_fiscal_month(d) == fm
    assert federal_fiscal_quarter(d) == fq


def test_fiscal_year_boundary_rolls_at_october():
    assert federal_fiscal_year(date(2025, 9, 30)) == 2025
    assert federal_fiscal_year(date(2025, 10, 1)) == 2026


def test_fiscal_months_cover_1_to_12_across_a_year():
    months = {
        federal_fiscal_month(date(2025, 10, 1).replace(month=((m + 8) % 12) + 1)) for m in range(12)
    }
    assert months == set(range(1, 13))
