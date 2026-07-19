# Import old dive logs into the Suunto app

Get your historical dive logs from **Subsurface** (or anything that can export
to Subsurface: divelogs.de, Dive Log Manager, MacDive, Diving Log, old Suunto
SDM files, most dive computers) into the **Suunto app**, so your dive history
and totals live alongside dives from a modern Suunto watch (Ocean, Nautic, …).

Tested end-to-end on macOS with SuuntoLink + Suunto app (2026), logbook of 56
dives from a Suunto HelO2. Windows should work identically. No dependencies —
stock python3.

## TL;DR — the route that works

SuuntoLink's "Import dive logs" does **not** read DM5 or its database at all.
It scans the folder you pick for `*.sml` files (one dive each), validates two
fields (`Header.DateTime`, `Device.SerialNumber`), converts each file to JSON
and uploads it to your Suunto account. So you can skip DM5 entirely:

1. Get your dives into Subsurface, then File → Export → *Subsurface XML*
   (`mydives.ssrf`).
2. Generate SML files:
   ```
   python3 ssrf2sml.py mydives.ssrf --serial <any Suunto serial> --device "Suunto HelO2"
   ```
   This writes one `.sml` per dive into `SML-export/`.
3. **Test first**: put 1–2 of the files in a separate folder, SuuntoLink →
   menu (☰) → **Import dive logs** → select that folder → log in. Then check
   those dives in the phone app: depth, duration, temperature, tank pressures.
   These uploads go straight to your live account, so verify before bulk.
4. Import the full `SML-export/` folder. Already-uploaded dives are skipped
   server-side ("already exists" — dedupe is on date+time), so re-running is
   safe.

What carries over: date/time, duration, full depth profile, temperatures, gas
mix (O2/He), tank start/end pressure + per-sample pressure, max/avg depth.
SML has no field for location/buddy/notes, so those stay in your logbook app.

Units in SML, if you're adapting the script: depth in meters, temperature in
**Kelvin**, pressure in **Pascal**, times in seconds, dates as naive local ISO
(`2014-09-13T12:10:00`).

## Do the dives reach the watch?

No — and that's Suunto's design, not a limitation of the import. Sync to the
watch is one-way: a Suunto watch only ever shows dives it recorded itself.
The app/cloud diary is the archive where your full history and totals live.
Imported dives also don't get the 3D dive view (that needs GPS/motion data
recorded by an Ocean/Nautic during the dive).

## Optional: getting the dives into DM5 too

Only worth it if you actually use DM5 as a desktop logbook — it is NOT needed
for the Suunto app. Two extra tools:

- `subsurface2sde.py` converts the Subsurface export to a `.sde` file that
  DM5 imports (File → Import): profiles, temps, gas, locations, buddies,
  notes, weights.
- `fix_pressures.py` fixes a DM5 import bug afterwards: DM5 stores imported
  tank pressures at dive level (bar) but its UI reads gas-mixture pressures
  (millibar) which the importer never fills. Run it with DM5 closed; it backs
  up the database first.

DM5 gotchas, hard-won:
- DM5 silently skips duplicates (matched on date+time) — a "failed" import is
  often duplicate-skipping.
- Reinstalling DM5 does NOT reset it. On Mac its data hides in
  `~/.config/Suunto/Suunto DM5/<version>/` (a hidden dot-folder), and the
  database is called `DM4.db` (yes, really).
- The SDE dialect is locale-sensitive: decimal commas on Swedish/German
  systems (`--decimal dot` flag if yours uses points), dates as `dd.mm.yyyy`
  (`--date-format mdy` flag if swapped), and the weight field is spelled
  `WEIGTH` (Suunto's own typo).
- DM5's log: `~/Library/Logs/SuuntoDM5/`. SuuntoLink's log (different app!):
  `~/Library/Application Support/Suuntolink/suuntolink_ui.log` — import
  results and per-file conversion errors appear there.

## How this was figured out

The SDE dialect was reverse-engineered from a real DM5 export and DM5's
SQLite database; the SML route was found by reading SuuntoLink's import code
(it's an Electron app — `dive_import.js` / `dive_converter.js` in
`SuuntoLink.app/Contents/Resources/app/` are plain JavaScript). Details in
the format notes above; PRs with Windows confirmations welcome.

## Disclaimer

Use at your own risk; keep backups of your logbooks, and always do the small
test import first. Not affiliated with Suunto.
