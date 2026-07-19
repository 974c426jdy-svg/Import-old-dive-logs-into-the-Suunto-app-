#!/usr/bin/env python3
"""
fix_pressures.py — patch tank pressures directly into DM5's database.

The SDE import stores start/end pressure correctly at dive level (bar) but the
DM5 UI displays the gas-mixture pressures (millibar), which the importer never
fills in. This script copies dive-level pressures into the mixture table.

Usage (with DM5 CLOSED):
    python3 fix_pressures.py            # finds DM4.db under ~/.config/Suunto
    python3 fix_pressures.py /path/to/DM4.db
"""

import glob
import os
import shutil
import sqlite3
import sys


def find_db():
    hits = glob.glob(os.path.expanduser("~/.config/Suunto/**/DM4.db"), recursive=True)
    if not hits:
        sys.exit("Could not find DM4.db under ~/.config/Suunto — pass its path as argument.")
    if len(hits) > 1:
        print("Multiple databases found, using newest:")
        for h in hits:
            print("  ", h)
    return max(hits, key=os.path.getmtime)


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else find_db()
    print("Database:", db)

    backup = db + ".backup"
    shutil.copy2(db, backup)
    print("Backup written to:", backup)

    con = sqlite3.connect(db)
    cur = con.cursor()

    # 1) Dive-level bar -> mixture-level mbar (what the UI shows)
    cur.execute("""
        UPDATE DiveMixture SET StartPressure = (
            SELECT CAST(d.StartPressure * 1000 AS INTEGER) FROM Dive d
            WHERE d.DiveId = DiveMixture.DiveId
              AND d.StartPressure IS NOT NULL AND d.StartPressure > 0)
        WHERE (StartPressure IS NULL OR StartPressure < 10000)
          AND EXISTS (SELECT 1 FROM Dive d WHERE d.DiveId = DiveMixture.DiveId
              AND d.StartPressure IS NOT NULL AND d.StartPressure > 0)
    """)
    n_start = cur.rowcount
    cur.execute("""
        UPDATE DiveMixture SET EndPressure = (
            SELECT CAST(d.EndPressure * 1000 AS INTEGER) FROM Dive d
            WHERE d.DiveId = DiveMixture.DiveId
              AND d.EndPressure IS NOT NULL AND d.EndPressure > 0)
        WHERE (EndPressure IS NULL OR EndPressure < 10000)
          AND EXISTS (SELECT 1 FROM Dive d WHERE d.DiveId = DiveMixture.DiveId
              AND d.EndPressure IS NOT NULL AND d.EndPressure > 0)
    """)
    n_end = cur.rowcount

    con.commit()

    # show result
    print(f"Patched {n_start} start and {n_end} end pressures.")
    print("\nDive overview after patch:")
    q = """SELECT d.DiveId, datetime(d.StartTime/10000000 - 62135596800, 'unixepoch') AS date,
                  d.StartPressure AS dive_start_bar, d.EndPressure AS dive_end_bar,
                  m.StartPressure AS mix_start_mbar, m.EndPressure AS mix_end_mbar
           FROM Dive d LEFT JOIN DiveMixture m ON m.DiveId = d.DiveId
           ORDER BY d.StartTime LIMIT 400"""
    for row in cur.execute(q):
        print("  ", row)
    con.close()
    print("\nDone. Start DM5 and check the pressures.")
    print(f"If anything looks wrong: restore with  mv '{backup}' '{db}'")


if __name__ == "__main__":
    main()
