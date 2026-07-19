#!/usr/bin/env python3
"""
ssrf2sml.py — generate Suunto SML files (one per dive) from a Subsurface
logbook, in the dialect SuuntoLink's "Import dive logs" actually reads.

SuuntoLink ignores DM5's database entirely: it scans the chosen folder for
*.sml files, validates Header.DateTime and Device.SerialNumber, converts each
to JSON and uploads it to your Suunto app account. So we skip DM5 and produce
exactly those files.

Usage:
    python3 ssrf2sml.py mydives.ssrf                  # writes ./SML-export/
    python3 ssrf2sml.py mydives.ssrf --test 2         # only first 2 dives
    python3 ssrf2sml.py mydives.ssrf --serial 12345678 --device "Suunto HelO2"

Units follow Suunto SML conventions: depth m, temperature Kelvin, pressure Pa.
"""

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


def parse_number(s):
    if not s:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", s)
    return float(m.group(0).replace(",", ".")) if m else None


def parse_duration_sec(s):
    if not s:
        return None
    s = s.replace("min", "").strip()
    parts = [p for p in s.split(":") if p != ""]
    try:
        parts = [int(float(p)) for p in parts]
    except ValueError:
        return None
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 60 if parts else None


def build_site_table(root):
    return {s.get("uuid"): s.get("name", "") for s in root.iter("site") if s.get("uuid")}


def text_of(elem, tag):
    e = elem.find(tag)
    return (e.text or "").strip() if e is not None and e.text else ""


def extract_dive(dive, sites):
    d = {}
    d["number"] = dive.get("number")
    d["date"] = dive.get("date")
    d["time"] = dive.get("time") or "12:00:00"
    d["duration_sec"] = parse_duration_sec(dive.get("duration"))

    dc = dive.find("divecomputer")
    scope = dc if dc is not None else dive

    depth = scope.find("depth")
    if depth is None:
        depth = dive.find("depth")
    d["maxdepth"] = parse_number(depth.get("max")) if depth is not None else None
    d["meandepth"] = parse_number(depth.get("mean")) if depth is not None else None

    temp = scope.find("temperature")
    if temp is None:
        temp = dive.find("temperature")
    d["watertemp"] = parse_number(temp.get("water")) if temp is not None else None

    cyl = dive.find("cylinder")
    d["o2pct"] = d["hepct"] = d["start_bar"] = d["end_bar"] = None
    if cyl is not None:
        d["o2pct"] = parse_number(cyl.get("o2"))
        d["hepct"] = parse_number(cyl.get("he"))
        d["start_bar"] = parse_number(cyl.get("start"))
        d["end_bar"] = parse_number(cyl.get("end"))

    samples = []
    for s in scope.iter("sample"):
        t = parse_duration_sec(s.get("time"))
        dep = parse_number(s.get("depth"))
        if t is None or dep is None:
            continue
        samples.append({
            "time": t,
            "depth": dep,
            "temp": parse_number(s.get("temp")),
            "pressure_bar": parse_number(s.get("pressure")),
        })
    samples.sort(key=lambda x: x["time"])
    d["samples"] = samples

    if d["start_bar"] is None and samples:
        pr = [s["pressure_bar"] for s in samples if s["pressure_bar"]]
        if pr:
            d["start_bar"], d["end_bar"] = pr[0], pr[-1]
    if d["maxdepth"] is None and samples:
        d["maxdepth"] = max(s["depth"] for s in samples)
    if d["duration_sec"] is None and samples:
        d["duration_sec"] = samples[-1]["time"]
    return d


def kelvin(c):
    return f"{c + 273.15:.2f}" if c is not None else None


def pascal(bar):
    return str(int(round(bar * 100000))) if bar else None


def sample_interval(samples):
    if len(samples) < 2:
        return 20
    deltas = {}
    for a, b in zip(samples, samples[1:]):
        dt = b["time"] - a["time"]
        if dt > 0:
            deltas[dt] = deltas.get(dt, 0) + 1
    return max(deltas, key=deltas.get) if deltas else 20


def dive_to_sml(d, serial, device_name):
    o2 = d["o2pct"] if d["o2pct"] else 21.0
    he = d["hepct"] or 0
    out = []
    w = out.append
    w('<?xml version="1.0" encoding="utf-8"?>\n')
    w('<sml xmlns="http://www.suunto.com/schemas/sml" '
      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n')
    w("  <DeviceLog>\n")
    w("    <Header>\n")
    w("      <Depth>\n")
    w(f"        <Max>{d['maxdepth']:.2f}</Max>\n" if d["maxdepth"] is not None else "        <Max>0</Max>\n")
    if d["meandepth"] is not None:
        w(f"        <Avg>{d['meandepth']:.2f}</Avg>\n")
    w("      </Depth>\n")
    w(f"      <DateTime>{d['date']}T{d['time']}</DateTime>\n")
    w(f"      <Duration>{d['duration_sec'] or 0}</Duration>\n")
    w("      <PauseDuration>0</PauseDuration>\n")
    w(f"      <SampleInterval>{sample_interval(d['samples'])}</SampleInterval>\n")
    w("      <Activity>Diving</Activity>\n")
    w("      <Diving>\n")
    w("        <Algorithm>Suunto Technical RGBM</Algorithm>\n")
    w(f"        <DiveMode>{'Mixed' if he else ('Nitrox' if o2 > 21 else 'Air')}</DiveMode>\n")
    w("        <Gases>\n")
    w("          <Gas>\n")
    w("            <State>Primary</State>\n")
    w(f"            <Oxygen>{o2:g}</Oxygen>\n")
    if he:
        w(f"            <Helium>{he:g}</Helium>\n")
    if d["start_bar"]:
        w(f"            <StartPressure>{pascal(d['start_bar'])}</StartPressure>\n")
    if d["end_bar"]:
        w(f"            <EndPressure>{pascal(d['end_bar'])}</EndPressure>\n")
    w("          </Gas>\n")
    w("        </Gases>\n")
    w("      </Diving>\n")
    w("    </Header>\n")
    w("    <Device>\n")
    w(f"      <Name>{escape(device_name)}</Name>\n")
    w(f"      <SerialNumber>{escape(str(serial))}</SerialNumber>\n")
    w("      <Info>\n")
    w("        <SW>1.0.0</SW>\n")
    w("        <HW>A</HW>\n")
    w('        <BatteryAtStart xsi:nil="true"/>\n')
    w('        <BatteryAtEnd xsi:nil="true"/>\n')
    w("      </Info>\n")
    w("    </Device>\n")
    w("    <Samples>\n")
    for s in d["samples"]:
        w("      <Sample>\n")
        w(f"        <Time>{s['time']}</Time>\n")
        w(f"        <Depth>{s['depth']:.2f}</Depth>\n")
        if s["temp"] is not None:
            w(f"        <Temperature>{kelvin(s['temp'])}</Temperature>\n")
        if s["pressure_bar"]:
            w("        <Cylinders>\n          <Cylinder>\n")
            w(f"            <Pressure>{pascal(s['pressure_bar'])}</Pressure>\n")
            w("          </Cylinder>\n        </Cylinders>\n")
        w("      </Sample>\n")
    w("    </Samples>\n")
    w("  </DeviceLog>\n")
    w("</sml>\n")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(description="Subsurface -> Suunto SML files for SuuntoLink import")
    ap.add_argument("input", help="Subsurface logbook (.ssrf or .xml)")
    ap.add_argument("-o", "--outdir", default="SML-export", help="Output folder (default SML-export)")
    ap.add_argument("--test", type=int, metavar="N", help="Only first N dives")
    ap.add_argument("--serial", default="21400832", help="Device serial number to stamp on dives")
    ap.add_argument("--device", default="Suunto HelO2", help="Device name to stamp on dives")
    args = ap.parse_args()

    try:
        root = ET.parse(args.input).getroot()
    except (ET.ParseError, FileNotFoundError) as e:
        sys.exit(f"Cannot read {args.input}: {e}")
    if root.tag not in ("divelog", "dives"):
        sys.exit("Not a Subsurface XML export.")

    sites = build_site_table(root)
    dives = list(root.iter("dive"))
    if args.test:
        dives = dives[: args.test]

    os.makedirs(args.outdir, exist_ok=True)
    written = 0
    for i, dive in enumerate(dives, start=1):
        d = extract_dive(dive, sites)
        if not d["date"]:
            continue
        name = f"dive-{d['date']}-{d['time'].replace(':', '')}.sml"
        with open(os.path.join(args.outdir, name), "w", encoding="utf-8") as f:
            f.write(dive_to_sml(d, args.serial, args.device))
        written += 1

    print(f"Wrote {written} SML files to {args.outdir}/")
    print("Next: SuuntoLink > menu > Import dive logs > choose that folder.")
    print("TEST WITH 1-2 FILES FIRST — uploads go straight to your Suunto account.")


if __name__ == "__main__":
    main()
