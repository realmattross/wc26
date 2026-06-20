#!/usr/bin/env python3
"""
WC26 Build Script — ESPN public API via curl (bypasses SSL issues on GitHub runners)
"""
import subprocess, json, os, base64, time, shutil
from datetime import datetime, timezone

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?limit=200&dates=20260611-20260719"

def curl_json(url):
    result = subprocess.run(
        ["curl", "-s", "-L", "--max-time", "30", "-A", "WC26/2.0", url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise Exception(f"curl failed: {result.stderr}")
    return json.loads(result.stdout)

def curl_bytes(url):
    result = subprocess.run(
        ["curl", "-s", "-L", "--max-time", "15", "-A", "WC26/2.0", url],
        capture_output=True
    )
    return result.stdout if result.returncode == 0 else None

def fetch_flag_b64(url):
    data = curl_bytes(url)
    if data:
        ext = url.split(".")[-1].lower()
        mime = "image/png" if ext == "png" else "image/svg+xml"
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    return url

def parse_group(alt_note):
    if alt_note and "Group" in alt_note:
        return alt_note.split("Group ")[-1].strip()
    return ""

def parse_stage(event):
    slug = event.get("season", {}).get("slug", "group-stage")
    note = event["competitions"][0].get("altGameNote", "")
    if "group" in slug or "Group" in note: return "group"
    if "round-of-32" in slug: return "r32"
    if "round-of-16" in slug: return "r16"
    if "quarterfinal" in slug: return "qf"
    if "semifinal" in slug: return "sf"
    if "final" in slug: return "final"
    return "group"

def build_match(event):
    comp   = event["competitions"][0]
    status = comp["status"]["type"]
    home_c = next((c for c in comp["competitors"] if c["homeAway"] == "home"), comp["competitors"][0])
    away_c = next((c for c in comp["competitors"] if c["homeAway"] == "away"), comp["competitors"][1])
    details = comp.get("details", [])
    home_id = home_c["team"]["id"]
    away_id = away_c["team"]["id"]
    def scorers_for(tid):
        goals = [d for d in details if d.get("scoringPlay") and str(d.get("team",{}).get("id")) == str(tid)]
        parts = []
        for g in goals:
            ath = g.get("athletesInvolved", [])
            name = ath[0]["shortName"] if ath else "?"
            minute = g["clock"]["displayValue"]
            og = " (OG)" if g.get("ownGoal") else ""
            pk = " (P)" if g.get("penaltyKick") else ""
            parts.append(f"{name} {minute}{og}{pk}")
        return ", ".join(parts)
    done = status["completed"]
    live = status["state"] == "in" and not done
    elapsed = comp["status"].get("displayClock","") if live else ("notstarted" if not done else "finished")
    return {
        "id": comp["id"],
        "home_team_id": home_c["team"]["id"],
        "away_team_id": away_c["team"]["id"],
        "home_team_name_en": home_c["team"]["displayName"],
        "away_team_name_en": away_c["team"]["displayName"],
        "home_score": int(home_c.get("score", 0) or 0),
        "away_score": int(away_c.get("score", 0) or 0),
        "home_scorers": scorers_for(home_id) if done or live else "",
        "away_scorers": scorers_for(away_id) if done or live else "",
        "finished": "TRUE" if done else "FALSE",
        "time_elapsed": elapsed,
        "local_date": event["date"],
        "type": parse_stage(event),
        "group": parse_group(comp.get("altGameNote","")),
        "matchday": "1",
        "stadium_id": comp.get("venue",{}).get("id",""),
        "stadium_name": comp.get("venue",{}).get("fullName","TBD"),
        "home_logo": home_c["team"].get("logo",""),
        "away_logo": away_c["team"].get("logo",""),
        "home_team_label": "",
        "away_team_label": "",
    }

def build_teams(events):
    teams = {}
    for e in events:
        comp = e["competitions"][0]
        grp  = parse_group(comp.get("altGameNote",""))
        for c in comp["competitors"]:
            t = c["team"]; tid = t["id"]
            if tid not in teams:
                teams[tid] = {"id":tid,"name_en":t["displayName"],"fifa_code":t.get("abbreviation",""),"flag":t.get("logo",""),"groups":grp}
            elif grp and not teams[tid]["groups"]:
                teams[tid]["groups"] = grp
    return list(teams.values())

def build_stadiums(events):
    stads = {}
    for e in events:
        v = e["competitions"][0].get("venue",{}); vid = v.get("id")
        if vid and vid not in stads:
            addr = v.get("address",{}); city = addr.get("city","")
            country = "United States"
            if any(x in city for x in ["Mexico","Guadalajara","Monterrey"]): country = "Mexico"
            if any(x in city for x in ["Vancouver","Toronto"]): country = "Canada"
            stads[vid] = {"id":vid,"name_en":v.get("fullName","TBD"),"city_en":city,"country_en":country,"capacity":v.get("capacity",0)}
    return list(stads.values())

def build_groups(events):
    groups = {}
    for e in events:
        comp = e["competitions"][0]; grp = parse_group(comp.get("altGameNote",""))
        if not grp or not comp["status"]["type"]["completed"]: continue
        if grp not in groups: groups[grp] = {}
        home_c = next(c for c in comp["competitors"] if c["homeAway"]=="home")
        away_c = next(c for c in comp["competitors"] if c["homeAway"]=="away")
        hs = int(home_c.get("score",0) or 0); as_ = int(away_c.get("score",0) or 0)
        for tid,ts,os_ in [(home_c["team"]["id"],hs,as_),(away_c["team"]["id"],as_,hs)]:
            if tid not in groups[grp]: groups[grp][tid] = {"team_id":tid,"mp":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,"gd":0,"pts":0}
            g = groups[grp][tid]; g["mp"]+=1; g["gf"]+=ts; g["ga"]+=os_; g["gd"]=g["gf"]-g["ga"]
            if ts>os_: g["w"]+=1; g["pts"]+=3
            elif ts==os_: g["d"]+=1; g["pts"]+=1
            else: g["l"]+=1
    return [{"name":k,"teams":sorted(v.values(),key=lambda x:(-x["pts"],-x["gd"],-x["gf"]))} for k,v in sorted(groups.items())]

def main():
    print("=== WC26 Build (ESPN via curl) ===")
    NOW = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    print(f"Time: {NOW}")
    print("Fetching matches...")
    data   = curl_json(ESPN_URL)
    events = data.get("events",[])
    print(f"  Got {len(events)} matches")
    matches = [build_match(e) for e in events]
    teams   = build_teams(events)
    stads   = build_stadiums(events)
    groups  = build_groups(events)
    done    = sum(1 for m in matches if m["finished"]=="TRUE")
    print(f"  Done:{done}, Teams:{len(teams)}, Groups:{len(groups)}")
    print("Fetching flags...")
    flag_cache = {}
    for t in teams:
        url = t.get("flag","")
        if url and url not in flag_cache:
            flag_cache[url] = fetch_flag_b64(url)
            time.sleep(0.02)
        if url: t["flag"] = flag_cache[url]
    print(f"  {len(flag_cache)} flags embedded")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root,"template.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__GAMES__",    json.dumps(matches, ensure_ascii=False, separators=(",",":")))
    html = html.replace("__TEAMS__",    json.dumps(teams,   ensure_ascii=False, separators=(",",":")))
    html = html.replace("__STADIUMS__", json.dumps(stads,   ensure_ascii=False, separators=(",",":")))
    html = html.replace("__GROUPS__",   json.dumps(groups,  ensure_ascii=False, separators=(",",":")))
    html = html.replace("__NOW__",      NOW)
    html = html.replace("__DONE__",     str(done))
    html = html.replace("__TOTAL__",    str(len(matches)))
    dist = os.path.join(root,"dist"); os.makedirs(dist,exist_ok=True)
    with open(os.path.join(dist,"index.html"),"w",encoding="utf-8") as f: f.write(html)
    for fn in ["netlify.toml","_redirects"]:
        src = os.path.join(root,fn)
        if os.path.exists(src): shutil.copy(src,os.path.join(dist,fn))
    print(f"Built dist/index.html — {len(html)//1024}KB | {done}/{len(matches)} played | {NOW}")

if __name__ == "__main__":
    main()
