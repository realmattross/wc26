#!/usr/bin/env python3
"""
WC26 Build Script
Fetches live data from worldcup26.ir and injects into template.html → index.html
Run by GitHub Actions on a schedule.
"""

import urllib.request
import json
import os
import base64
import time
from datetime import datetime, timezone

TOKEN   = os.environ.get("WC26_API_TOKEN", "TH1cqV6bmZcmtV3SWDtH1N1roCEcAGpB7V")
BASE    = "https://worldcup26.ir"
TIMEOUT = 15

def fetch(path):
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {TOKEN}", "User-Agent": "WC26-Builder/1.0"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())

def fetch_flag(url):
    """Fetch a flag image and return as base64 data URI."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "WC26-Builder/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
            mime = "image/png" if url.endswith(".png") else "image/svg+xml"
            return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception as e:
        print(f"  Flag fetch failed for {url}: {e}")
        return url  # Fall back to URL

def main():
    print("=== WC26 Build ===")
    print(f"Time: {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}")

    # 1. Fetch all API data
    print("\nFetching API data...")
    try:
        games    = fetch("/get/games")["games"]
        teams    = fetch("/get/teams")["teams"]
        stadiums = fetch("/get/stadiums")["stadiums"]
        groups   = fetch("/get/groups")["groups"]
        print(f"  ✅ Games: {len(games)}, Teams: {len(teams)}, Stadiums: {len(stadiums)}, Groups: {len(groups)}")
    except Exception as e:
        print(f"  ❌ API fetch failed: {e}")
        raise

    # 2. Fetch and embed flag images as base64
    print("\nFetching flag images...")
    flag_cache = {}
    for i, t in enumerate(teams):
        url = t.get("flag", "")
        if url and url not in flag_cache:
            flag_cache[url] = fetch_flag(url)
            time.sleep(0.05)  # Be polite to flagcdn.com
        if url:
            t["flag"] = flag_cache[url]
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(teams)} flags fetched...")
    print(f"  ✅ {len(flag_cache)} unique flags embedded")

    # 3. Build stats
    done  = sum(1 for g in games if g["finished"].upper() == "TRUE")
    total = len(games)
    now   = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    # 4. Serialize to compact JSON
    GAMES_J    = json.dumps(games,    ensure_ascii=False, separators=(",", ":"))
    TEAMS_J    = json.dumps(teams,    ensure_ascii=False, separators=(",", ":"))
    STADIUMS_J = json.dumps(stadiums, ensure_ascii=False, separators=(",", ":"))
    GROUPS_J   = json.dumps(groups,   ensure_ascii=False, separators=(",", ":"))

    print(f"\nData sizes: games={len(GAMES_J)//1024}KB, teams={len(TEAMS_J)//1024}KB")

    # 5. Read template and inject
    template_path = os.path.join(os.path.dirname(__file__), "..", "template.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    html = html.replace("__GAMES__",    GAMES_J)
    html = html.replace("__TEAMS__",    TEAMS_J)
    html = html.replace("__STADIUMS__", STADIUMS_J)
    html = html.replace("__GROUPS__",   GROUPS_J)
    html = html.replace("__NOW__",      now)
    html = html.replace("__DONE__",     str(done))
    html = html.replace("__TOTAL__",    str(total))

    # Verify all placeholders replaced
    remaining = [p for p in ["__GAMES__","__TEAMS__","__STADIUMS__","__GROUPS__","__NOW__","__DONE__","__TOTAL__"] if p in html]
    if remaining:
        print(f"  ⚠️  Unreplaced placeholders: {remaining}")

    # 6. Write output
    out_dir = os.path.join(os.path.dirname(__file__), "..", "dist")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    # 7. Copy Netlify config files into dist
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    for fname in ["netlify.toml", "_redirects"]:
        src = os.path.join(repo_root, fname)
        dst = os.path.join(out_dir, fname)
        if os.path.exists(src):
            with open(src) as s, open(dst, "w") as d:
                d.write(s.read())

    size_kb = len(html) // 1024
    print(f"\n✅ Built: dist/index.html ({size_kb}KB)")
    print(f"   {done}/{total} matches played · Updated {now}")

if __name__ == "__main__":
    main()
