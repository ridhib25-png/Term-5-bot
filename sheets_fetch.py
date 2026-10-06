"""Pulls both timetable tabs live from the mirror Google Sheet (public,
view-only link) and rebuilds schedule.json. No auth needed since the
sheet is viewable by anyone with the link.

Run standalone:  python3 sheets_fetch.py
Or import fetch_and_build() to call it from the bot on a refresh timer.
"""

import csv
import io
import json
import os

import requests

from parsing import build_schedule

SHEET_ID = "1ZsGRScyZhaHa0pX8AVMY5SegHrJ5iCD9EbRnYKPx5AM"
TABS = {
    "combined": 0,             # Core schedule - full PGDM combined timetable
    "bfs": 907780552,          # Bfs schedule - B&FS-only timetable
}
SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "schedule.json")


def _csv_url(gid):
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"


def _fetch_grid(gid):
    resp = requests.get(_csv_url(gid), timeout=20)
    resp.raise_for_status()
    reader = csv.reader(io.StringIO(resp.text))
    return list(reader)


def _grid_to_rows(grid):
    """Reads every row from row 5 onward and keeps whichever ones actually
    have a date in column A. This works for BOTH sheet layouts we've seen:
    the older style where each date's row is followed by 3 blank spacer
    rows (merged cells collapse to blank in CSV export), and this term's
    "Bfs schedule" tab which instead uses one row per date with no spacers.
    Iterating every row and checking for a date, rather than assuming a
    fixed step, handles either layout without needing to know which one
    a given tab uses."""
    rows = []
    for r in range(4, len(grid)):  # 0-indexed row 4 == spreadsheet row 5
        row = grid[r]
        row = row + [""] * (9 - len(row))  # pad to at least 9 cols (A-I)
        date_val, day_val = row[0], row[1]
        if not date_val:
            continue
        sessions = {
            "S1": row[2], "S2": row[3], "S3": row[4],
            "S4": row[6], "S5": row[7], "S6": row[8],
        }
        rows.append({
            "date": _parse_date_cell(date_val),
            "day": day_val,
            "sessions": sessions,
        })
    return rows


def _parse_date_cell(val):
    from parsing import clean_date
    return clean_date(val)


def fetch_and_build():
    combined_grid = _fetch_grid(TABS["combined"])
    bfs_grid = _fetch_grid(TABS["bfs"])

    combined_rows = _grid_to_rows(combined_grid)
    bfs_rows = _grid_to_rows(bfs_grid)

    schedule = build_schedule([
        (combined_rows, "combined"),
        (bfs_rows, "bfs"),
    ])

    with open(SCHEDULE_PATH, "w") as f:
        json.dump(schedule, f, indent=2)

    return schedule


if __name__ == "__main__":
    sched = fetch_and_build()
    print(f"Fetched and saved schedule.json — {len(sched)} dates.")
