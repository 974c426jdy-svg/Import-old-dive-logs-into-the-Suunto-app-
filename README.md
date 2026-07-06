# Import old dive logs into the Suunto app (via Subsurface → SDE → DM5 → SuuntoLink)

Get your historical dive logs from **Subsurface** (or anything that can export to
Subsurface: divelogs.de, Dive Log Manager, MacDive, Diving Log, old Suunto SDM
files, most dive computers) into the **Suunto app**, so your dive history and
totals live alongside dives from a modern Suunto watch (Ocean, Nautic, etc.).

Tested with: Suunto DM5 1.5.4 on macOS, SuuntoLink, Suunto app (2026).
Should work identically on Windows (paths differ).

## Why this is hard

The Suunto app has exactly one import door: **SuuntoLink → "Import dive logs"**,
which reads a **DM5** installation. DM5 in turn only imports SDE, SDP, SML and
Suunto's own XML — none of which Subsurface or any third-party logbook can
export. On top of that, the SDE dialect DM5 actually accepts is undocumented,
locale-sensitive, and has an import bug that silently drops tank pressures.

This repo closes the gap with two small Python scripts (no dependencies,
stock python3):

- **`subsurface2sde.py`** — converts a Subsurface logbook (.ssrf / exported
  XML) into an `.sde` file DM5 imports: full depth profiles, temperatures,
  gas mix, locations, buddies, notes, weights, tank pressures.
- **`fix_pressures.py`** — works around the DM5 import bug: DM5 stores
  imported start/end pressures at dive level (bar) but its UI reads
  gas-mixture pressures (millibar), which the importer never fills.
  This patches them straight into DM5's SQLite database.

## Steps

1. **Get everything into Subsurface** (skip if already there). Subsurface
   imports from divelogs.de, UDDF, MacDive, Diving Log, old Suunto DM/SDM
   files and dozens of dive computers.

2. **Export**: Subsurface → File → Export → *Subsurface XML* → `mydives.ssrf`.

3. **Trial run** (always test 3 dives first):
   ```
   python3 subsurface2sde.py mydives.ssrf --test 3
   ```
   Import the resulting `mydives.sde` in DM5 (File → Import) and check dates,
   depths, profiles. Useful flags if something looks off:
   - `--date-format mdy` — if day/month are swapped
   - `--decimal dot` — if your system locale uses decimal points
     (default is comma, e.g. Swedish/German locales)

4. **Full run**: same command without `--test`, import in DM5.

5. **Fix tank pressures**: quit DM5, then:
   ```
   python3 fix_pressures.py
   ```
   It finds DM5's database automatically (`~/.config/Suunto/**/DM4.db` on
   Mac — yes, hidden dot-folder, and yes, DM5's database is called DM4.db;
   on Windows `%APPDATA%\Suunto\DM5`), makes a backup, patches the mixture
   pressures, and prints every dive so you can spot gaps. Reopen DM5.

6. **Push to the Suunto app**: SuuntoLink → menu (☰) → **Import dive logs** →
   it auto-fills the DM5 folder → OK → log in. Dives appear in your Suunto
   app diary.

## Hard-won gotchas

- **DM5 silently skips duplicates** (matched on date+time). A failed-looking
  import is often duplicate-skipping. For a guaranteed clean slate, quit DM5
  and move its data folder away: `mv ~/.config/Suunto ~/Desktop/dm5-backup`.
  Reinstalling DM5 does NOT reset it — the database survives in the hidden
  folder.
- **Import from ONE source only.** Duplicates that reach the Suunto app must
  be deleted there one at a time.
- **Old dives won't get the 3D dive view** on Ocean/Nautic — that needs
  GPS/motion data recorded by the watch itself. They do count toward history
  and totals.
- Dives showing 0 bar after `fix_pressures.py` genuinely lack pressure data
  in the source log — DM5's fields are editable by hand for those.
- DM5's own log lives at `~/Library/Logs/SuuntoDM5/` (Mac) if you need to
  debug.

## SDE format notes (for the curious / other tool authors)

An `.sde` is a ZIP containing one XML per dive (`0.xml`, `1.xml`, ...),
root `<SUUNTO><HEADER>...<MSG>...`, in DM5's dialect:

- Decimal separator follows the OS locale (comma on Swedish/German systems)
- `DATE` is `dd.mm.yyyy`
- Dive-level `CYLINDERSTARTPRESSURE`/`CYLINDERENDPRESSURE` are **bar**,
  but the `DiveMixture` table in the database is **millibar** — and the UI
  reads the latter (hence `fix_pressures.py`)
- The weight field is spelled `WEIGTH` (sic, Suunto's own typo)
- Profile samples: `SAMPLE` → `SAMPLETIME` (s), `DEPTH`, `PRESSURE`,
  `TEMPERATURE`, `SACRATE`, `CYLPRESS`
- The importer ignores unknown/extra whitespace but the element set above
  (mirrored from a real DM5 export) is known-good

## Disclaimer

Use at your own risk; always keep backups of your logbooks and let the trial
run prove itself before a full import. Not affiliated with Suunto.
