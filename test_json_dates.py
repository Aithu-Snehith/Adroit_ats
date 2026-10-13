"""Dates in API responses are '2026-10-13 12:00:05' text on every database.

On Postgres the driver returns datetime objects, which Flask's default JSON sent as
'Tue, 13 Oct 2026 12:00:05 GMT' - the screens slice dates as text, so the tracker showed
'Tue, 13 Oc' and the interview-date box came up empty. app._JSONProvider fixes the format.
Standalone; throwaway SQLite DB. Run: python test_json_dates.py
"""
import datetime
import decimal
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_json_dates.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
from flask import jsonify  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


with app_module.app.app_context():
    out = jsonify({"at": datetime.datetime(2026, 10, 13, 12, 0, 5), "day": datetime.date(2026, 10, 13),
                   "n": decimal.Decimal("1.5"), "s": "2026-10-13 09:00:00"}).get_json()
check(out["at"] == "2026-10-13 12:00:05", f"datetime as SQLite-style text: {out['at']!r}")
check(out["at"][:10] == "2026-10-13", "slicing the first 10 characters gives the date")
check(out["day"] == "2026-10-13", f"date: {out['day']!r}")
check(out["s"] == "2026-10-13 09:00:00", "text dates unchanged")
check(out["n"] == "1.5", f"Decimal still serialises: {out['n']!r}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: API dates are '2026-10-13 12:00:05' on SQLite and Postgres (no 'Tue, 13 Oc' on the live site).")
