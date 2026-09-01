"""Read the latest Configurable Robot meter values from a meter CSV file."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Optional, Union


MeterValue = Union[int, float, str]

# Kept with the profiler so this folder can be shipped without Config Builder.
# Names and order match Robot/PlatformApi.cpp and Robot/Types.h.
ALL_METERS = [
    "Video-Used-Memory", "Video-Free-Memory", "Video-Total-Memory",
    "Used-Memory", "Free-Memory", "Total-Memory", "File-Buffer-Cache",
    "Page-Cache", "Real-Free-Memory", "CMR", "Max-CMR",
    "Sys-CPU", "User-CPU", "Idle-CPU",
    "Games-Played", "Turnover", "Total-Win", "Credit",
    "Bet", "Last-Win", "Hopper-Pay", "Jackpot",
]


class MeterCSVError(ValueError):
    """Raised when a Robot meter CSV cannot be interpreted."""


def _convert_value(value: str) -> MeterValue:
    """Convert numeric values while preserving any future textual values."""
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def read_all_meters(
    csv_path: Union[str, Path] = "robotlogs/meters.csv",
) -> Dict[str, Optional[MeterValue]]:
    """Return each meter column's latest non-empty value."""
    csv_path = Path(csv_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise MeterCSVError(f"Meter CSV has no header: {csv_path}")

        meters = [
            name.strip()
            for name in reader.fieldnames
            if name and name.strip() not in {"Time", "Info"}
        ]
        values: Dict[str, Optional[MeterValue]] = {
            meter: None for meter in meters
        }
        for row in reader:
            for raw_name, raw_value in row.items():
                if raw_name is None or raw_name.strip() not in values:
                    continue
                value = raw_value.strip() if raw_value is not None else ""
                if value:
                    values[raw_name.strip()] = _convert_value(value)
    return values
