#!/usr/bin/env python3
"""
subsurface2sde.py  (v4)
Convert a Subsurface dive log export to a Suunto .sde file that DM5 imports.

v2: mirrors the exact SDE dialect of DM5's own export (UTF-8, decimal commas,
    DM5 field order, zip entries 0.xml, 1.xml, ...)
v3: tank pressures in bar (mbar was read as 0 by DM5)
v4: writes tank pressure into per-sample CYLPRESS (DM5 derives start/end
    pressure from samples; constant 0 there was zeroing everything out).
    Interpolates start->end when the profile has no pressure readings.

Usage:
    python3 subsurface2sde.py Peter.ssrf --test 3
    python3 subsurface2sde.py Peter.ssrf
    python3 subsurface2sde.py Peter.ssrf --decimal dot      # if DM5 misreads depths
    python3 subsurface2sde.py Peter.ssrf --date-format mdy  # if day/month swapped
"""

import argparse
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


# ---------- unit parsing helpers ----------

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
    if len(parts) == 1:
        return parts[0] * 60
    return None


# ---------- Subsurface XML reading ----------

def build_site_table(root):
    sites = {}
    for site in root.iter("site"):
        uuid = site.get("uuid")
        if uuid:
            sites[uuid] = site.get("name", "")
    return sites


def text_of(elem, tag):
    e = elem.find(tag)
    return (e.text or "").strip() if e is not None and e.text else ""


def extract_dive(dive, sites):
    d = {}
    d["number"] = dive.get("number")
    d["date"] = dive.get("date")
    d["time"] = dive.get("time")
    d["duration_sec"] = parse_duration_sec(dive.get("duration"))
    d["tags"] = dive.get("tags", "")

    loc = ""
    dsid = dive.get("divesiteid")
    if dsid and dsid in sites:
        loc = sites[dsid]
    if not loc:
        loc = text_of(dive, "location")
    d["location"] = loc

    d["buddy"] = text_of(dive, "buddy")
    d["divemaster"] = text_of(dive, "divemaster")
    d["notes"] = text_of(dive, "notes")
    d["suit"] = text_of(dive, "suit")

    weight = 0.0
    for ws in dive.findall("weightsystem"):
        w = parse_number(ws.get("weight"))
        if w:
            weight += w
    d["weight_kg"] = weight if weight > 0 else None

    cyl = dive.find("cylinder")
    d["o2pct"] = d["hepct"] = None
    d["cyl_start_bar"] = d["cyl_end_bar"] = None
    if cyl is not None:
        d["o2pct"] = parse_number(cyl.get("o2"))
        d["hepct"] = parse_number(cyl.get("he"))
        d["cyl_start_bar"] = parse_number(cyl.get("start"))
        d["cyl_end_bar"] = parse_number(cyl.get("end"))

    dc = dive.find("divecomputer")
    scope = dc if dc is not None else dive

    model = (dc.get("model", "") if dc is not None else "") or ""
    d["model"] = re.sub(r"(?i)^suunto\s+", "", model) or "HelO2"

    depth = scope.find("depth")
    if depth is None:
        depth = dive.find("depth")
    d["maxdepth"] = parse_number(depth.get("max")) if depth is not None else None
    d["meandepth"] = parse_number(depth.get("mean")) if depth is not None else None

    temp = scope.find("temperature")
    if temp is None:
        temp = dive.find("temperature")
    d["watertemp"] = parse_number(temp.get("water")) if temp is not None else None
    d["airtemp"] = parse_number(temp.get("air")) if temp is not None else None

    samples = []
    for s in scope.iter("sample"):
        t_ = parse_duration_sec(s.get("time"))
        dep = parse_number(s.get("depth"))
        if t_ is None or dep is None:
            continue
        samples.append({
            "time": t_,
            "depth": dep,
            "temp": parse_number(s.get("temp")),
            "pressure_bar": parse_number(s.get("pressure")),
        })
    samples.sort(key=lambda x: x["time"])
    d["samples"] = samples

    if d["cyl_start_bar"] is None and samples:
        pr = [s["pressure_bar"] for s in samples if s["pressure_bar"]]
        if pr:
            d["cyl_start_bar"], d["cyl_end_bar"] = pr[0], pr[-1]
    if d["maxdepth"] is None and samples:
        d["maxdepth"] = max(s["depth"] for s in samples)
    if d["duration_sec"] is None and samples:
        d["duration_sec"] = samples[-1]["time"]

    return d


# ---------- DM5-dialect SDE writing ----------

DEC = ","            # set from --decimal
PRESSURE_FACTOR = 1  # 1 = bar (DM5), 1000 = mbar (old SDM)


def num(v, decimals=2):
    """30.62 -> '30,62' (DM5 on a Swedish-locale system uses decimal commas)."""
    if v is None:
        return ""
    if float(v).is_integer():
        return str(int(v))
    return f"{float(v):.{decimals}f}".replace(".", DEC)


def pres(bar):
    """Tank pressure in the unit DM5 expects (bar by default)."""
    return str(int(round(bar * PRESSURE_FACTOR))) if bar else ""


def sdm_date(date_str, order="dmy"):
    if not date_str:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
    if not m:
        return date_str
    y, mo, da = m.groups()
    return f"{mo}.{da}.{y}" if order == "mdy" else f"{da}.{mo}.{y}"


def t(name, value=""):
    """DM5 style: <TAG>value</TAG> or self-closing <TAG /> when empty."""
    v = "" if value is None else str(value)
    return f"<{name}>{escape(v)}</{name}>" if v != "" else f"<{name} />"


def _fill_pressures(samples, start_bar, end_bar):
    """Per-sample pressure for every sample: real readings, interpolated gaps,
    or start->end interpolation if the profile has no readings at all."""
    if not samples:
        return []
    known = [(i, s["pressure_bar"]) for i, s in enumerate(samples) if s["pressure_bar"]]
    if not known:
        if not (start_bar and end_bar):
            return [None] * len(samples)
        total = samples[-1]["time"] or 1
        return [start_bar + (end_bar - start_bar) * (s["time"] / total) for s in samples]
    out = [None] * len(samples)
    for i, s in enumerate(samples):
        if s["pressure_bar"]:
            out[i] = s["pressure_bar"]
    # hold first known value before it, last known after it, interpolate between
    first_i, last_i = known[0][0], known[-1][0]
    for i in range(first_i):
        out[i] = known[0][1]
    for i in range(last_i + 1, len(samples)):
        out[i] = known[-1][1]
    for (i0, p0), (i1, p1) in zip(known, known[1:]):
        t0, t1 = samples[i0]["time"], samples[i1]["time"]
        for i in range(i0 + 1, i1):
            frac = (samples[i]["time"] - t0) / (t1 - t0) if t1 > t0 else 0
            out[i] = p0 + (p1 - p0) * frac
    return out


def dive_to_dm5_xml(d, number, date_order):
    """Mirror the element set and order of a real DM5 SDE export."""
    samples = d["samples"]
    interval = 20
    if len(samples) >= 2:
        deltas = {}
        for a, b in zip(samples, samples[1:]):
            dt = b["time"] - a["time"]
            if dt > 0:
                deltas[dt] = deltas.get(dt, 0) + 1
        if deltas:
            interval = max(deltas, key=deltas.get)

    notes = d["notes"]
    if d["suit"]:
        notes = (notes + "\nSuit: " + d["suit"]).strip()
    if d["tags"]:
        notes = (notes + "\nTags: " + d["tags"]).strip()

    date = sdm_date(d["date"], date_order)
    time = d["time"] or "12:00:00"
    logtitle = f"{date} {time} {d['model']}".strip()

    last_temp = None
    for s in reversed(samples):
        if s["temp"] is not None:
            last_temp = s["temp"]
            break

    o2 = d["o2pct"]
    o2_out = num(o2) if o2 and o2 > 21 else "0"

    parts = []
    parts.append('<?xml version="1.0"?>')
    parts.append('<SUUNTO xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
                 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">')
    parts.append("<HEADER>" + t("MSGNAME", "SDM001A") + t("MSGPACKING", "1") + "</HEADER>")
    parts.append("<MSG>")
    parts.append(t("WRISTOPID", "0"))
    parts.append(t("SAMPLECNT", len(samples)))
    parts.append(t("DATE", date))
    parts.append(t("TIME", time))
    parts.append(t("MAXDEPTH", num(d["maxdepth"])))
    parts.append(t("MEANDEPTH", num(d["meandepth"])))
    parts.append(t("SAMPLEINTERVAL", interval))
    parts.append(t("LOGTITLE", logtitle))
    parts.append(t("LOGNOTES", notes))
    parts.append(t("FOLDER"))
    parts.append(t("LOCATION", d["location"]))
    parts.append(t("SITE"))
    parts.append(t("WEATHER"))
    parts.append(t("WATERVISIBILITY", "0"))
    parts.append(t("AIRTEMP", num(d["airtemp"], 0) or "0"))
    parts.append(t("WATERTEMPMAXDEPTH", num(d["watertemp"], 0) or "0"))
    parts.append(t("WATERTEMPATEND", num(last_temp if last_temp is not None else d["watertemp"], 0) or "0"))
    parts.append(t("PARTNER", d["buddy"]))
    parts.append(t("DIVEMASTER", d["divemaster"]))
    parts.append(t("CYLINDERUNITS", "0"))
    parts.append(t("CYLINDERSTARTPRESSURE", pres(d["cyl_start_bar"])))
    parts.append(t("CYLINDERENDPRESSURE", pres(d["cyl_end_bar"])))
    parts.append(t("DIVENUMBER", number))
    parts.append(t("DCDIVENUMBER"))
    parts.append(t("DIVESERIES"))
    parts.append(t("DEVICEMODEL", d["model"]))
    parts.append(t("DIVETIMESEC", d["duration_sec"] or ""))
    parts.append(t("GASMODE", "2"))
    parts.append(t("OLFPCT", "0"))
    parts.append(t("WEIGTH", num(d["weight_kg"]) or "0"))  # sic — Suunto's own spelling
    parts.append(t("O2PCT", o2_out))
    parts.append(t("HEPCT_0", num(d["hepct"]) or "0"))
    parts.append(t("MIXTYPE_0", "0"))
    parts.append(t("PO2MAX", "0"))
    parts.append(t("RGBMPCT"))
    parts.append(t("HPTRANSMITTER"))
    parts.append(t("SURFACETIME", "0"))
    parts.append(t("NOPROFILE", "" if samples else "1"))
    for i in range(1, 10):
        parts.append(t(f"PREVTPRESSURE_{i}"))

    # v4: DM5 derives tank pressure from per-sample CYLPRESS, so every sample
    # needs a value. Use real readings where present, linear interpolation in
    # the gaps, and start->end interpolation when the profile has none at all.
    filled = _fill_pressures(samples, d["cyl_start_bar"], d["cyl_end_bar"])
    for s, p in zip(samples, filled):
        parts.append("<SAMPLE>"
                     + t("SAMPLETIME", s["time"])
                     + t("DEPTH", num(s["depth"]))
                     + t("PRESSURE", pres(p))
                     + t("TEMPERATURE", num(s["temp"]))
                     + t("SACRATE")
                     + t("CYLPRESS", str(int(round(p * 1000))) if p else "0")  # mbar!
                     + "</SAMPLE>")

    parts.append("</MSG></SUUNTO>")
    return "".join(parts)


# ---------- main ----------

def main():
    global DEC, PRESSURE_FACTOR
    ap = argparse.ArgumentParser(description="Convert Subsurface XML/.ssrf to Suunto .sde (DM5 dialect)")
    ap.add_argument("input", help="Subsurface logbook (.ssrf or .xml)")
    ap.add_argument("-o", "--output", help="Output .sde file (default: <input>.sde)")
    ap.add_argument("--test", type=int, metavar="N", help="Only convert the first N dives")
    ap.add_argument("--date-format", choices=["dmy", "mdy"], default="dmy",
                    help="Date order (default dmy = 18.07.2004)")
    ap.add_argument("--decimal", choices=["comma", "dot"], default="comma",
                    help="Decimal separator (default comma, matching Swedish DM5)")
    ap.add_argument("--pressure-unit", choices=["bar", "mbar"], default="bar",
                    help="Tank pressure unit in output (default bar)")
    args = ap.parse_args()
    DEC = "," if args.decimal == "comma" else "."
    PRESSURE_FACTOR = 1000 if args.pressure_unit == "mbar" else 1

    try:
        tree = ET.parse(args.input)
    except ET.ParseError as e:
        sys.exit(f"Could not parse {args.input}: {e}")
    except FileNotFoundError:
        sys.exit(f"File not found: {args.input}")
    root = tree.getroot()

    if root.tag not in ("divelog", "dives"):
        sys.exit(f"Not a Subsurface XML export (root <{root.tag}>). "
                 "In Subsurface: File > Export > Subsurface XML.")

    sites = build_site_table(root)
    dives = list(root.iter("dive"))
    if not dives:
        sys.exit("No dives found in the file.")
    if args.test:
        dives = dives[: args.test]

    out_path = args.output or re.sub(r"\.(ssrf|xml)$", "", args.input, flags=re.I) + ".sde"

    written = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, dive in enumerate(dives, start=1):
            d = extract_dive(dive, sites)
            if not d["date"]:
                continue
            xml_text = dive_to_dm5_xml(d, d["number"] or i, args.date_format)
            zf.writestr(f"{written}.xml", xml_text.encode("utf-8"))
            written += 1

    print(f"Wrote {written} dives to {out_path}  [script v5]")
    print("Next: DM5 > File > Import > choose this .sde file.")


if __name__ == "__main__":
    main()
