"""Staff roster loader."""
from __future__ import annotations

import csv
from pathlib import Path

from closer.data.models import Staff

STAFF_CSV = Path(__file__).resolve().parent / "staff.csv"


def load_staff(path: Path = STAFF_CSV) -> tuple[Staff, ...]:
    with path.open(newline="") as f:
        return tuple(
            Staff(**{**row, "commission_eligible": row["commission_eligible"].strip().lower() == "true"})
            for row in csv.DictReader(f)
        )
