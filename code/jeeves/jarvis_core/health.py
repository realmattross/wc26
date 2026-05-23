"""Apple Health — read JSON pushed from the iPhone via iCloud Drive.

Apple HealthKit is iOS-only, so the data has to leave the phone before
Jeeves on the Mac can see it. The iPhone writes a JSON snapshot to a
folder in iCloud Drive on a schedule (via Health Auto Export, the app,
or a DIY iOS Shortcut). iCloud syncs it to the Mac at:

    ~/Library/Mobile Documents/com~apple~CloudDocs/jeeves-health/

We read whichever file is freshest in there.

Schema flexibility:
    The iPhone-side exporter you use determines the JSON shape. We
    accept the two most common shapes — Health Auto Export's structure
    AND a flatter "today/last_night/trends_7d" shape that's easy to
    produce from a Shortcut. The reader handles both.

Metrics that matter most for Matt (Parkinson's-relevant):
    - Walking speed              (m/s, declining trend = worry)
    - Walking asymmetry          (% — clinically meaningful for PD)
    - Double support time        (% time both feet on ground — also key)
    - Walking step length        (m)
    - Step count                 (overall activity)
    - Resting heart rate         (cardiovascular trend)
    - HRV                        (autonomic function)
    - Sleep duration + quality   (PD patients often have fragmented sleep)
    - Active energy              (cumulative daily movement)

Anything else in the JSON is preserved verbatim and Jeeves can surface it
when asked, even if it's not in our typed accessors.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# Health Auto Export writes into its own sandboxed iCloud container
# (iCloud~com~ifunography~HealthExport), not the shared CloudDocs folder —
# that's why the "iCloud Drive" picker in the app couldn't see our
# user-created folder. We list the real path first, then the shared
# folder as a fallback for DIY Shortcuts that DO write into CloudDocs.
HEALTH_DIRS = [
    Path.home()
    / "Library" / "Mobile Documents"
    / "iCloud~com~ifunography~HealthExport" / "Documents" / "New Automation",
    Path.home()
    / "Library" / "Mobile Documents"
    / "com~apple~CloudDocs" / "jeeves-health",
]


@dataclass
class HealthSnapshot:
    """A normalised view across whichever schema the iPhone produced."""

    exported_at: datetime
    raw: dict           # Whole JSON, kept so Jeeves can surface unusual fields

    # Today's quick numbers. Any can be None if the phone didn't include it.
    steps: int | None = None
    active_kcal: float | None = None
    exercise_min: int | None = None
    stand_hours: int | None = None
    walking_speed_m_s: float | None = None
    walking_asymmetry_pct: float | None = None
    double_support_pct: float | None = None
    walking_step_length_m: float | None = None
    resting_hr: float | None = None
    hrv_ms: float | None = None
    water_ml: float | None = None

    # Last sleep
    sleep_total_min: int | None = None
    sleep_deep_min: int | None = None
    sleep_rem_min: int | None = None
    sleep_awake_min: int | None = None
    bedtime: datetime | None = None
    wake_time: datetime | None = None

    # 7-day rolling averages, populated when the export includes them
    avg_steps_7d: int | None = None
    avg_walking_speed_7d: float | None = None
    avg_walking_asymmetry_7d: float | None = None
    avg_double_support_7d: float | None = None
    avg_resting_hr_7d: float | None = None
    avg_sleep_hours_7d: float | None = None


# ---------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------
def _force_icloud_download(directory: Path, wait_seconds: float = 5.0) -> None:
    """Kick iCloud to materialise any placeholder (.icloud) files in ``directory``,
    then wait briefly for at least one to become a real file on disk.

    macOS represents an un-downloaded iCloud file as a hidden placeholder
    named ``.<original>.icloud`` — a normal ``glob("*.json")`` will MISS
    those, which is why Jeeves intermittently looks like it has no fresh
    health data: the file the iPhone wrote is in iCloud but hasn't been
    pulled to disk on the Mac yet.

    Two-step process:

      1) ``brctl download <dir>`` walks the directory and triggers a
         download for every placeholder. It's fire-and-forget — returns
         the moment it queues the work, NOT when the file is on disk.
      2) Poll for each placeholder to become a real file (i.e. the
         hidden ``.<name>.icloud`` stub goes away). This is the bit
         that was missing previously: without it the very next glob
         could still miss the just-being-downloaded file and Jeeves
         silently used yesterday's data.

    We cap the wait at ``wait_seconds`` so a stalled iCloud daemon can't
    wedge a voice response. If the wait expires we return anyway — the
    caller falls back to whatever's already materialised.
    """
    import subprocess
    import time

    placeholders = list(directory.glob(".*.icloud"))
    if not placeholders:
        return

    try:
        # Tell iCloud to fetch every placeholder under this dir. Fast
        # when there's nothing to do, so safe to always call.
        subprocess.run(
            ["brctl", "download", str(directory)],
            timeout=8,
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # brctl missing (non-mac runner) or iCloud hung — fall through
        # and let the caller cope with whatever's already on disk.
        return

    # Each placeholder is ``.<realname>.icloud`` — strip the leading
    # dot and the trailing ``.icloud`` to get the real filename we're
    # waiting for. The placeholder disappears once iCloud finishes the
    # download. We poll on file existence rather than placeholder
    # absence because either condition implies the file is ready.
    expected: list[Path] = []
    for p in placeholders:
        name = p.name
        if name.startswith(".") and name.endswith(".icloud"):
            expected.append(p.parent / name[1:-len(".icloud")])
    if not expected:
        return

    deadline = time.monotonic() + max(wait_seconds, 0.0)
    while time.monotonic() < deadline:
        # Materialised once at least one expected file exists AND its
        # corresponding placeholder is gone. Multiple placeholders can
        # download concurrently; once we have the newest one we can
        # proceed — _latest_file picks the best of what's there.
        if any(real.exists() for real in expected):
            return
        time.sleep(0.2)


def _latest_file() -> Path | None:
    """Return the freshest data file across all HEALTH_DIRS.

    Health Auto Export names files like HealthAutoExport-YYYY-MM-DD.json,
    and iCloud sync sometimes touches mtimes out of order during downloads,
    so we prefer the date embedded in the filename when present, falling
    back to mtime when no date is parseable.

    Before globbing, we ask iCloud to materialise any placeholder files —
    otherwise the iPhone's most-recent export can sit in iCloud unread
    and Jeeves silently uses yesterday's data.
    """
    import re

    candidates: list[Path] = []
    for d in HEALTH_DIRS:
        if not d.exists():
            continue
        _force_icloud_download(d)
        candidates.extend(d.glob("*.json"))
    if not candidates:
        return None
    candidates = [
        p for p in candidates
        if not p.name.endswith("_new_automation.json")
        and not p.name.startswith("hae_export_")
    ]
    if not candidates:
        return None

    date_pattern = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

    def sort_key(p: Path):
        m = date_pattern.search(p.name)
        if m:
            return (1, m.group(0), p.stat().st_mtime)
        # Files with no date in name sort below files that have one
        return (0, "", p.stat().st_mtime)

    return max(candidates, key=sort_key)


# ---------------------------------------------------------------------
# Schema parsers
# ---------------------------------------------------------------------
def _parse_iso(value) -> datetime | None:
    """Parse a timestamp from any of the formats the iPhone exporters emit:

      ISO 8601:                "2026-04-30T12:31:00+01:00"
      Health Auto Export v2:   "2026-04-30 12:31:00 +0100"
      Unix epoch:              1715678100
    """
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value))
        s = str(value).strip()
        if not s:
            return None
        # Try strict ISO first
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
        # Health Auto Export's "YYYY-MM-DD HH:MM:SS +ZZZZ" form
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            pass
        # Same without timezone offset
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    except Exception:
        return None
    return None


def _coerce_number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_flat_shape(data: dict) -> HealthSnapshot:
    """Handle the simple {today, last_night, trends_7d} schema that's
    natural to produce from a DIY Shortcut."""
    today = data.get("today") or {}
    sleep = data.get("last_night") or data.get("sleep") or {}
    trends = data.get("trends_7d") or data.get("trends") or {}

    snap = HealthSnapshot(
        exported_at=_parse_iso(data.get("exported_at")) or datetime.now(),
        raw=data,
        steps=int(today["steps"]) if today.get("steps") is not None else None,
        active_kcal=_coerce_number(today.get("active_energy_kcal") or today.get("active_kcal")),
        exercise_min=int(today["exercise_minutes"]) if today.get("exercise_minutes") is not None else None,
        stand_hours=int(today["stand_hours"]) if today.get("stand_hours") is not None else None,
        walking_speed_m_s=_coerce_number(today.get("walking_speed_m_s")),
        walking_asymmetry_pct=_coerce_number(today.get("walking_asymmetry_pct")),
        double_support_pct=_coerce_number(today.get("double_support_pct")),
        walking_step_length_m=_coerce_number(today.get("walking_step_length_m")),
        resting_hr=_coerce_number(today.get("resting_hr_bpm") or today.get("resting_hr")),
        hrv_ms=_coerce_number(today.get("hrv_ms") or today.get("hrv")),
        water_ml=_coerce_number(today.get("water_ml")),
        sleep_total_min=int(sleep["asleep_minutes"]) if sleep.get("asleep_minutes") is not None else None,
        sleep_deep_min=int(sleep["deep_minutes"]) if sleep.get("deep_minutes") is not None else None,
        sleep_rem_min=int(sleep["rem_minutes"]) if sleep.get("rem_minutes") is not None else None,
        sleep_awake_min=int(sleep["awake_minutes"]) if sleep.get("awake_minutes") is not None else None,
        bedtime=_parse_iso(sleep.get("bedtime")),
        wake_time=_parse_iso(sleep.get("wake_time")),
        avg_steps_7d=int(trends["avg_steps"]) if trends.get("avg_steps") is not None else None,
        avg_walking_speed_7d=_coerce_number(trends.get("avg_walking_speed")),
        avg_walking_asymmetry_7d=_coerce_number(trends.get("avg_walking_asymmetry")),
        avg_double_support_7d=_coerce_number(trends.get("avg_double_support")),
        avg_resting_hr_7d=_coerce_number(trends.get("avg_resting_hr")),
        avg_sleep_hours_7d=_coerce_number(trends.get("avg_sleep_hours")),
    )
    return snap


def _parse_health_auto_export(data: dict) -> HealthSnapshot:
    """Handle the Health Auto Export app's typical schema.

    Health Auto Export emits {"data": {"metrics": [...]}} with one entry
    per metric, each having a name + units + array of dated samples. We
    pick today's most-recent value for each Parkinson's-relevant metric
    AND convert imperial units to the metric ones our snapshot uses.
    """
    metrics = (data.get("data", {}) or {}).get("metrics", []) or []

    # Use the most recent date present in the file as our reference, not
    # datetime.now() — the iPhone sync can lag a day, so today's data may
    # actually be yesterday's. We'd rather show yesterday than nothing.
    latest_date = None
    for m in metrics:
        for sample in m.get("data", []) or []:
            ts = _parse_iso(sample.get("date") or sample.get("startDate"))
            if ts and (latest_date is None or ts.date() > latest_date):
                latest_date = ts.date()
    today = latest_date or datetime.now().date()

    # Convert raw value to canonical units based on what the exporter said.
    def to_canonical(value: float | None, units: str | None, target: str) -> float | None:
        if value is None:
            return None
        u = (units or "").strip().lower()
        if target == "m_s":
            if u in ("m/s", "ms"):
                return value
            if u in ("mi/hr", "mph"):
                return value * 0.44704
            if u in ("km/hr", "kmh", "kph"):
                return value / 3.6
            return value
        if target == "metres":
            if u in ("m",):
                return value
            if u in ("in", "inch", "inches"):
                return value * 0.0254
            if u in ("ft", "feet"):
                return value * 0.3048
            if u in ("cm",):
                return value / 100.0
            return value
        if target == "ml":
            if u in ("ml",):
                return value
            if u in ("l",):
                return value * 1000.0
            if u in ("fl_oz", "floz", "fl oz"):
                return value * 29.5735
            return value
        return value

    def find_metric(metric_name: str) -> dict | None:
        for m in metrics:
            if (m.get("name") or "").lower() == metric_name.lower():
                return m
        return None

    def latest_today(metric_name: str) -> tuple[float | None, str | None]:
        m = find_metric(metric_name)
        if not m:
            return (None, None)
        units = m.get("units")
        for sample in reversed(m.get("data", []) or []):
            ts = _parse_iso(sample.get("date") or sample.get("startDate"))
            if ts and ts.date() == today:
                qty = sample.get("qty")
                if qty is None:
                    qty = sample.get("value")
                return (_coerce_number(qty), units)
        return (None, units)

    def latest_any(metric_name: str) -> tuple[float | None, str | None]:
        m = find_metric(metric_name)
        if not m:
            return (None, None)
        samples = m.get("data", []) or []
        if not samples:
            return (None, m.get("units"))
        last = samples[-1]
        qty = last.get("qty")
        if qty is None:
            qty = last.get("value")
        return (_coerce_number(qty), m.get("units"))

    # Steps may come back fractional (Health Auto Export sometimes summarises
    # bucketed counts). Sum today's samples instead of taking the last.
    def sum_today(metric_name: str) -> float | None:
        m = find_metric(metric_name)
        if not m:
            return None
        total = 0.0
        any_match = False
        for sample in m.get("data", []) or []:
            ts = _parse_iso(sample.get("date") or sample.get("startDate"))
            if ts and ts.date() == today:
                qty = sample.get("qty")
                if qty is None:
                    qty = sample.get("value")
                num = _coerce_number(qty)
                if num is not None:
                    total += num
                    any_match = True
        return total if any_match else None

    speed_val, speed_units = latest_today("walking_speed")
    step_len_val, step_len_units = latest_today("walking_step_length")
    asym_val, _ = latest_today("walking_asymmetry_percentage")
    ds_val, _ = latest_today("walking_double_support_percentage")
    rhr_val, _ = latest_today("resting_heart_rate")
    if rhr_val is None:
        rhr_val, _ = latest_any("resting_heart_rate")
    hrv_val, _ = latest_today("heart_rate_variability")
    if hrv_val is None:
        hrv_val, _ = latest_any("heart_rate_variability")
    water_val, water_units = latest_today("dietary_water")
    if water_val is None:
        water_val, water_units = latest_today("water")

    steps_val = sum_today("step_count")
    active_val = sum_today("active_energy")

    snap = HealthSnapshot(
        exported_at=datetime.now(),
        raw=data,
        steps=int(steps_val) if steps_val is not None else None,
        active_kcal=active_val,
        walking_speed_m_s=to_canonical(speed_val, speed_units, "m_s"),
        walking_asymmetry_pct=asym_val,
        double_support_pct=ds_val,
        walking_step_length_m=to_canonical(step_len_val, step_len_units, "metres"),
        resting_hr=rhr_val,
        hrv_ms=hrv_val,
        water_ml=to_canonical(water_val, water_units, "ml"),
    )

    # Sleep — Health Auto Export V2 schema:
    #   { "name": "sleep_analysis", "units": "hr",
    #     "data": [{"asleep": 6.3, "deep": 0, "rem": 0, "awake": 0,
    #               "core": 0, "totalSleep": 6.3, "inBed": 0,
    #               "sleepStart": "...", "sleepEnd": "...", ...}] }
    #
    # Two gotchas burned us before:
    #   (a) units are HOURS in V2, not minutes — we used to coerce
    #       6.3 directly to int and lose 99% of the value.
    #   (b) only sleep_total was being lifted from the row. Stages
    #       (deep/rem/awake) need to flow too so cheap trackers that
    #       *don't* measure stages show None (not stale data) and
    #       better trackers fill them in.
    for m in metrics:
        if (m.get("name") or "").lower() != "sleep_analysis":
            continue
        samples = m.get("data", []) or []
        if not samples:
            continue
        last = samples[-1]

        # If the metric block declares its units we honour them;
        # default to hours since that's what every V2 export we've
        # ever seen ships.
        units = (m.get("units") or "hr").lower()
        mult = 60 if units in ("hr", "h", "hour", "hours") else 1

        def _mins(field):
            v = _coerce_number(last.get(field))
            # Treat zero AND missing as "no value" — cheap trackers
            # zero out the stages they can't measure, which we don't
            # want surfacing as a misleading "0m deep".
            if v is None or v == 0:
                return None
            return int(round(v * mult))

        # Total — prefer `asleep` (actual sleep) but fall back to
        # `totalSleep` (which some exports emit alongside).
        total = _mins("asleep")
        if total is None:
            total = _mins("totalSleep")
        snap.sleep_total_min = total
        snap.sleep_deep_min  = _mins("deep")
        snap.sleep_rem_min   = _mins("rem")
        snap.sleep_awake_min = _mins("awake")
        snap.bedtime   = _parse_iso(last.get("sleepStart") or last.get("startDate"))
        snap.wake_time = _parse_iso(last.get("sleepEnd")   or last.get("endDate"))
        break

    return snap


def _parse(data: dict) -> HealthSnapshot:
    """Auto-detect schema and parse."""
    if isinstance(data.get("data"), dict) and "metrics" in data.get("data", {}):
        return _parse_health_auto_export(data)
    return _parse_flat_shape(data)


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
def _all_files_sorted() -> list[Path]:
    """All known data files, newest first.

    Used by latest_snapshot for sleep-data backfill: the newest file
    won't usually have last night's sleep written into it yet, since
    Health Auto Export tends to push activity metrics throughout the
    day and the sleep_analysis row only lands hours later — sometimes
    the next morning. So if today's file is missing sleep we walk back
    through recent files for the most recent row that has it.
    """
    import re
    candidates: list[Path] = []
    for d in HEALTH_DIRS:
        if not d.exists():
            continue
        _force_icloud_download(d)
        candidates.extend(d.glob("*.json"))
    candidates = [
        p for p in candidates
        if not p.name.endswith("_new_automation.json")
        and not p.name.startswith("hae_export_")
    ]
    date_pattern = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
    def sort_key(p: Path):
        m = date_pattern.search(p.name)
        if m:
            return (1, m.group(0), p.stat().st_mtime)
        return (0, "", p.stat().st_mtime)
    return sorted(candidates, key=sort_key, reverse=True)


def _backfill_sleep(snap: HealthSnapshot, skip: Path, max_lookback: int = 5) -> None:
    """If snap has no sleep data, scan the next ``max_lookback`` older
    files for a sleep_analysis row and lift it into snap.

    Mutates ``snap`` in place. Skips the file we already parsed (the
    latest) since by definition it had nothing to give us.
    """
    if snap.sleep_total_min is not None:
        return
    files = [p for p in _all_files_sorted() if p != skip]
    for path in files[:max_lookback]:
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if not (isinstance(data.get("data"), dict) and "metrics" in data["data"]):
            continue
        for m in data["data"].get("metrics", []) or []:
            if (m.get("name") or "").lower() != "sleep_analysis":
                continue
            samples = m.get("data", []) or []
            if not samples:
                continue
            last = samples[-1]
            units = (m.get("units") or "hr").lower()
            mult = 60 if units in ("hr", "h", "hour", "hours") else 1
            def _mins(field):
                v = _coerce_number(last.get(field))
                if v is None or v == 0:
                    return None
                return int(round(v * mult))
            total = _mins("asleep") or _mins("totalSleep")
            if total is None:
                # This file had a sleep_analysis block but no usable
                # total — keep walking. Some days the row exists with
                # all zeros (e.g. tracker wasn't worn).
                continue
            snap.sleep_total_min = total
            snap.sleep_deep_min  = _mins("deep")
            snap.sleep_rem_min   = _mins("rem")
            snap.sleep_awake_min = _mins("awake")
            snap.bedtime   = _parse_iso(last.get("sleepStart") or last.get("startDate"))
            snap.wake_time = _parse_iso(last.get("sleepEnd")   or last.get("endDate"))
            return  # done — first usable hit wins


def latest_snapshot() -> HealthSnapshot | None:
    """Read and parse the freshest JSON in the iCloud Drive folder.

    Sleep data is back-filled from older files if the latest file
    doesn't have a sleep_analysis row yet — last night's sleep often
    isn't written into today's export until later in the day.
    """
    path = _latest_file()
    if not path:
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    snap = _parse(data)
    _backfill_sleep(snap, skip=path)
    return snap


def is_stale(snap: HealthSnapshot, max_hours: float = 24.0) -> bool:
    """True if the snapshot is older than max_hours."""
    age = datetime.now() - snap.exported_at
    return age > timedelta(hours=max_hours)


def status_paragraph(snap: HealthSnapshot | None) -> str:
    """Human-readable summary suitable for the LLM to read aloud."""
    if not snap:
        return (
            "No Apple Health data yet. Run a manual export in Health "
            "Auto Export, or check the activity log in the app to see "
            "if its scheduled exports are firing."
        )

    lines: list[str] = []
    age_hr = (datetime.now() - snap.exported_at).total_seconds() / 3600
    if age_hr > 24:
        lines.append(f"(Last sync was {age_hr:.0f}h ago — data is stale.)")

    today_bits = []
    if snap.steps is not None:
        today_bits.append(f"{snap.steps:,} steps")
    if snap.exercise_min is not None:
        today_bits.append(f"{snap.exercise_min}m exercise")
    if snap.active_kcal is not None:
        today_bits.append(f"{snap.active_kcal:.0f} active kcal")
    if today_bits:
        lines.append("Today: " + ", ".join(today_bits))

    gait_bits = []
    if snap.walking_speed_m_s is not None:
        gait_bits.append(f"walking speed {snap.walking_speed_m_s:.2f} m/s")
    if snap.walking_asymmetry_pct is not None:
        gait_bits.append(f"asymmetry {snap.walking_asymmetry_pct:.1f}%")
    if snap.double_support_pct is not None:
        gait_bits.append(f"double support {snap.double_support_pct:.1f}%")
    if snap.walking_step_length_m is not None:
        gait_bits.append(f"step length {snap.walking_step_length_m:.2f}m")
    if gait_bits:
        lines.append("Gait: " + ", ".join(gait_bits))

    sleep_bits = []
    if snap.sleep_total_min is not None:
        h = snap.sleep_total_min // 60
        m = snap.sleep_total_min % 60
        sleep_bits.append(f"{h}h {m}m")
    if snap.sleep_deep_min is not None:
        sleep_bits.append(f"{snap.sleep_deep_min}m deep")
    if snap.sleep_rem_min is not None:
        sleep_bits.append(f"{snap.sleep_rem_min}m REM")
    if sleep_bits:
        lines.append("Last night's sleep: " + ", ".join(sleep_bits))

    cardio_bits = []
    if snap.resting_hr is not None:
        cardio_bits.append(f"resting HR {snap.resting_hr:.0f} bpm")
    if snap.hrv_ms is not None:
        cardio_bits.append(f"HRV {snap.hrv_ms:.0f}ms")
    if cardio_bits:
        lines.append("Cardio: " + ", ".join(cardio_bits))

    if snap.water_ml is not None:
        lines.append(f"Water: {snap.water_ml:.0f}ml")

    if not lines:
        lines.append("Got the snapshot but it's empty — check the iPhone export config.")

    return "\n".join(lines)


def concerns(snap: HealthSnapshot | None) -> list[str]:
    """Return a list of short prompts describing concerns Jeeves might
    want to surface proactively. Conservative — only flags meaningful
    deviations, not every below-target number.
    """
    if not snap:
        return []
    out: list[str] = []
    now_hour = datetime.now().hour

    # Movement nudges — tied to time of day so we don't nag at 9am
    if snap.steps is not None:
        if now_hour >= 14 and snap.steps < 3000:
            out.append(f"Only {snap.steps:,} steps so far today — a short walk would help.")
        elif now_hour >= 19 and snap.steps < 6000:
            out.append(f"Steps are at {snap.steps:,} — under your usual by this hour.")

    # Sleep
    if snap.sleep_total_min is not None and snap.sleep_total_min < 360:
        h = snap.sleep_total_min // 60
        m = snap.sleep_total_min % 60
        out.append(f"Last night was only {h}h {m}m — short on sleep.")

    # Hydration
    if snap.water_ml is not None and now_hour >= 14 and snap.water_ml < 800:
        out.append(f"Water's at {snap.water_ml:.0f}ml — worth topping up.")

    # Gait — flag if today's number drifted notably from 7d average
    if (
        snap.walking_speed_m_s is not None
        and snap.avg_walking_speed_7d is not None
        and snap.walking_speed_m_s < snap.avg_walking_speed_7d * 0.92
    ):
        out.append(
            f"Walking speed is {snap.walking_speed_m_s:.2f} m/s today — "
            f"a bit below your 7-day average of {snap.avg_walking_speed_7d:.2f}."
        )

    if (
        snap.walking_asymmetry_pct is not None
        and snap.avg_walking_asymmetry_7d is not None
        and snap.walking_asymmetry_pct > snap.avg_walking_asymmetry_7d * 1.15
    ):
        out.append(
            f"Walking asymmetry today {snap.walking_asymmetry_pct:.1f}% vs "
            f"7-day average {snap.avg_walking_asymmetry_7d:.1f}%."
        )

    # HRV trend — drop is meaningful
    if (
        snap.hrv_ms is not None
        and snap.avg_resting_hr_7d is not None
        and snap.resting_hr is not None
        and snap.resting_hr > snap.avg_resting_hr_7d * 1.10
    ):
        out.append(
            f"Resting HR {snap.resting_hr:.0f} is up from your 7-day average "
            f"of {snap.avg_resting_hr_7d:.0f}."
        )

    return out
