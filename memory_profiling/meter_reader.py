"""Read the latest Configurable Robot meter values from a meter CSV file.

The Robot writes a CSV header containing ``Time``, ``Info``, and every meter
name. Rows may contain blanks for meters that were not selected by the
corresponding ``<meter-list>``. Consequently, each getter searches backwards
for the most recent non-empty value of the requested meter.

No values are cached: every public getter reads the CSV file again.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union


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


class MeterReader:
    """Read current meter values from a Robot-generated CSV file."""

    _METADATA_COLUMNS = {"Time", "Info"}

    def __init__(self, csv_path: Union[str, Path] = "robotlogs/meters.csv"):
        self.csv_path = Path(csv_path)

    def _read_csv(self) -> Tuple[List[str], List[Dict[str, str]]]:
        """Open and read the CSV. This method intentionally does no caching."""
        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                raise MeterCSVError(f"Meter CSV has no header: {self.csv_path}")

            # Trim column names because hand-edited/example XML sometimes has
            # whitespace around meter text.
            fieldnames = [name.strip() for name in reader.fieldnames if name]
            rows: List[Dict[str, str]] = []
            for raw_row in reader:
                row = {
                    key.strip(): (value.strip() if value is not None else "")
                    for key, value in raw_row.items()
                    if key is not None
                }
                rows.append(row)

        return fieldnames, rows

    @staticmethod
    def _convert_value(value: str) -> MeterValue:
        """Convert numeric values while preserving any future textual values."""
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value

    def get_meter(self, meter_name: str) -> Optional[MeterValue]:
        """Return the latest non-empty value for ``meter_name``.

        The file is reopened on every call. ``None`` means that the meter is a
        valid CSV column but has no recorded value yet. An unknown meter name
        raises ``KeyError``.
        """
        fieldnames, rows = self._read_csv()
        meter_name = meter_name.strip()
        if meter_name not in fieldnames or meter_name in self._METADATA_COLUMNS:
            raise KeyError(f"Meter is not present in {self.csv_path}: {meter_name}")

        for row in reversed(rows):
            value = row.get(meter_name, "")
            if value != "":
                return self._convert_value(value)
        return None

    def get_all_meters(self) -> Dict[str, Optional[MeterValue]]:
        """Return every meter column and its latest non-empty value.

        The CSV is opened only once for this snapshot. Values can originate
        from different rows when multiple meter lists write to the same file.
        """
        fieldnames, rows = self._read_csv()
        meters = [
            name for name in fieldnames
            if name not in self._METADATA_COLUMNS
        ]
        values: Dict[str, Optional[MeterValue]] = {}
        for meter_name in meters:
            values[meter_name] = None
            for row in reversed(rows):
                value = row.get(meter_name, "")
                if value != "":
                    values[meter_name] = self._convert_value(value)
                    break
        return values

    def get_video_used_memory(self) -> Optional[MeterValue]:
        return self.get_meter("Video-Used-Memory")

    def get_video_free_memory(self) -> Optional[MeterValue]:
        return self.get_meter("Video-Free-Memory")

    def get_video_total_memory(self) -> Optional[MeterValue]:
        return self.get_meter("Video-Total-Memory")

    def get_used_memory(self) -> Optional[MeterValue]:
        return self.get_meter("Used-Memory")

    def get_free_memory(self) -> Optional[MeterValue]:
        return self.get_meter("Free-Memory")

    def get_total_memory(self) -> Optional[MeterValue]:
        return self.get_meter("Total-Memory")

    def get_file_buffer_cache(self) -> Optional[MeterValue]:
        return self.get_meter("File-Buffer-Cache")

    def get_page_cache(self) -> Optional[MeterValue]:
        return self.get_meter("Page-Cache")

    def get_real_free_memory(self) -> Optional[MeterValue]:
        return self.get_meter("Real-Free-Memory")

    def get_cmr(self) -> Optional[MeterValue]:
        return self.get_meter("CMR")

    def get_max_cmr(self) -> Optional[MeterValue]:
        return self.get_meter("Max-CMR")

    def get_sys_cpu(self) -> Optional[MeterValue]:
        return self.get_meter("Sys-CPU")

    def get_user_cpu(self) -> Optional[MeterValue]:
        return self.get_meter("User-CPU")

    def get_idle_cpu(self) -> Optional[MeterValue]:
        return self.get_meter("Idle-CPU")

    def get_games_played(self) -> Optional[MeterValue]:
        return self.get_meter("Games-Played")

    def get_turnover(self) -> Optional[MeterValue]:
        return self.get_meter("Turnover")

    def get_total_win(self) -> Optional[MeterValue]:
        return self.get_meter("Total-Win")

    def get_credit(self) -> Optional[MeterValue]:
        return self.get_meter("Credit")

    def get_bet(self) -> Optional[MeterValue]:
        return self.get_meter("Bet")

    def get_last_win(self) -> Optional[MeterValue]:
        return self.get_meter("Last-Win")

    def get_hopper_pay(self) -> Optional[MeterValue]:
        return self.get_meter("Hopper-Pay")

    def get_jackpot(self) -> Optional[MeterValue]:
        return self.get_meter("Jackpot")
