#!/usr/bin/env python3
"""
WC26 Build Script — uses ESPN public API (no auth needed, works from anywhere)
Fetches live data and injects into template.html → dist/index.html
"""

import urllib.request
import urllib.error
import json
import os
import base64
import time
from datetime import datetime, timezone

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
TIMEOUT   = 15

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "WC26-Builder/2.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())

def fetch_flag_b64(url):
    """Fetch flag image and return as base64 data URI."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "WC26-Builder/2.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
            ext = url.split('.')[-1].lower()
            mime = "image/png" if ext == "png" else "image/svg+xml"
            return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception as e:
        print(f"  Flag fetch failed {url}: {e}")
        return url

def parse_group(alt_note):
    """Extract group letter from 'FIFA World Cup, Group A'"""
    if alt_note and 'Group' in alt_note:
        return alt_note.split('Group ')[-1].strip()
    return ''

def parse_stage(event):
    """Determine match type/stage"""
    slug = event.get('season', {}).get('slug', 'group-stage')
    note = event['competitions'][0].get('altGameNote', '')
    if 'group-stage' in slug or 'Group' in note:
        return 'group'
    if 'round-of-32' in slug: return 'r32'
    if 'round-of-16' in slug: return 'r16'
    if 'quarterfinal' in slug: return 'qf'
    if 'semifinal' in slug: return 'sf'
    if 'final' in slug: return 'final'
    return 'group'

def build_match(event):
    """Convert ESPN event to our app's match format"""
    comp   = event['competitions'][0]
    status = comp['status']['type']
    home_c = next((c for c in comp['competitors'] if c['homeAway'] == 'home'), comp['competitors'][0])
    away_c = next((c for c in comp['competitors'] if c['homeAway'] == 'away'), comp['competitors'][1])

    # Scorers from details
    details = comp.get('details', [])
    home_id = home_c['team']['id']
    away_id = away_c['team']['id']

    def scorers_for(team_id):
        goals = [d for d in details if d.get('scoringPlay') and d.get('team',{}).get('id') == team_id]
        parts = []
        for g in goals:
            athletes = g.get('athletesInvolved', [])
            name = athletes[0]['shortName'] if athletes else '?'
            minute = g['clock']['displayValue']
            og = ' (OG)' if g.get('ownGoal') else ''
            pk = ' (P)' if g.get('penaltyKick') else ''
            parts.append(f"{name} {minute}{og}{pk}")
        return ', '.join(parts)

    done = status['completed']
    live = status['state'] == 'in' and not done
    elapsed = comp['status'].get('displayClock', '') if live else ('notstarted' if not done else 'finished')

    return {
        'id':               comp['id'],
        'home_team_id':     home_c['team']['id'],
        'away_team_id':     away_c['team']['id'],
        'home_team_name_en': home_c['team']['displayName'],
        'away_team_name_en': away_c['team']['displayName'],
        'home_score':       int(home_c.get('score', 0) or 0),
        'away_score':       int(away_c.get('score', 0) or 0),
        'home_scorers':     scorers_for(home_id) if done or live else '',
        'away_scorers':     scorers_for(away_id) if done or live else '',
        'finished':         'TRUE' if done else 'FALSE',
        'time_elapsed':     elapsed,
        'local_date':       event['date'],
        'type':             parse_stage(event),
        'group':            parse_group(comp.get('altGameNote', '')),
        'matchday':         '1',
        'stadium_id':       comp.get('venue', {}).get('id', ''),
        'stadium_name':     comp.get('venue', {}).get('fullName', 'TBD'),
        'home_logo':        home_c['team'].get('logo', ''),
        'away_logo':        away_c['team'].get('logo', ''),
        'home_team_label':  home_c.get('curatedRank', {}).get('current', ''),
        'away_team_label':  away_c.get('curatedRank', {}).get('current', ''),
    }

def build_teams(events):
    """Extract unique teams from events"""
    teams = {}
    for e in events:
        comp = e['competitions'][0]
        grp  = parse_group(comp.get('altGameNote', ''))
        for c in comp['competitors']:
            t = c['team']
            tid = t['id']
            if tid not in teams:
                teams[tid] = {
                    'id':      tid,
                    'name_en': t['displayName'],
                    'fifa_code': t.get('abbreviation', ''),
                    'flag':    t.get('logo', ''),
                    'groups':  grp,
                }
            elif grp and not teams[tid]['groups']:
                teams[tid]['groups'] = grp
    return list(teams.values())

def build_stadiums(events):
    """Extract unique stadiums from events"""
    stads = {}
    for e in events:
        comp = e['competitions'][0]
        v = comp.get('venue', {})
        vid = v.get('id')
        if vid and vid not in stads:
            addr = v.get('address', {})
            city = addr.get('city', '')
            # Determine country from city
            country = 'United States'
            if any(c in city for c in ['Mexico', 'Guadalajara', 'Monterrey']): country = 'Mexico'
            if any(c in city for c in ['Vancouver', 'Toronto']): country = 'Canada'
            stads[vid] = {
                'id':         vid,
                'name_en':    v.get('fullName', 'TBD'),
                'city_en':    city,
                'country_en': country,
                'capacity':   v.get('capacity', 0),
            }
    return list(stads.values())

def build_groups(events, teams_map):
    """Build group standings from match results"""
    groups = {}
    for e in events:
        comp  = e['competitions'][0]
        grp   = parse_group(comp.get('altGameNote', ''))
        if not grp: continue
        if grp not in groups:
            groups[grp] = {}
        done = comp['status']['type']['completed']
        if not done: continue
        home_c = next(c for c in comp['competitors'] if c['homeAway'] == 'home')
        away_c = next(c for c in comp['competitors'] if c['homeAway'] == 'away')
        hs = int(home_c.get('score', 0) or 0)
        as_ = int(away_c.get('score', 0) or 0)
        for tid, ts, os_ in [(home_c['team']['id'], hs, as_), (away_c['team']['id'], as_, hs)]:
            if tid not in groups[grp]:
                groups[grp][tid] = {'team_id': tid, 'mp':0,'w':0,'d':0,'l':0,'gf':0,'ga':0,'gd':0,'pts':0}
            g = groups[grp][tid]
            g['mp'] += 1; g['gf'] += ts; g['ga'] += os_; g['gd'] = g['gf'] - g['ga']
            if ts > os_:   g['w']+=1; g['pts']+=3
            elif ts == os_: g['d']+=1; g['pts']+=1
            else:           g['l']+=1

    result = []
    for grp_name in sorted(groups.keys()):
        teams_list = sorted(groups[grp_name].values(), key=lambda x: (-x['pts'], -x['gd'], -x['gf']))
        result.append({'name': grp_name, 'teams': teams_list})
    return result

def main():
    print("=== WC26 Build (ESPN API) ===")
    NOW = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    print(f"Time: {NOW}")

    # 1. Fetch all matches
    print("\nFetching matches from ESPN...")
    data   = fetch(f"{ESPN_BASE}/scoreboard?limit=200&dates=20260611-20260719")
    events = data.get('events', [])
    print(f"  ✅ {len(events)} matches fetched")

    # 2. Parse into our format
    matches = [build_match(e) for e in events]
    teams   = build_teams(events)
    stads   = build_stadiums(events)
    teams_map = {t['id']: t for t in teams}
    groups  = build_groups(events, teams_map)

    done  = sum(1 for m in matches if m['finished'] == 'TRUE')
    live  = sum(1 for m in matches if m['time_elapsed'] not in ('notstarted','finished'))
    print(f"  Done: {done}, Live: {live}, Teams: {len(teams)}, Groups: {len(groups)}")

    # 3. Fetch and embed flag images
    print("\nFetching flag images...")
    flag_cache = {}
    for t in teams:
        url = t.get('flag', '')
        if url and url not in flag_cache:
            flag_cache[url] = fetch_flag_b64(url)
            time.sleep(0.03)
        if url:
            t['flag'] = flag_cache[url]
    print(f"  ✅ {len(flag_cache)} flags embedded")

    # 4. Serialize
    GAMES_J    = json.dumps(matches, ensure_ascii=False, separators=(',',':'))
    TEAMS_J    = json.dumps(teams,   ensure_ascii=False, separators=(',',':'))
    STADS_J    = json.dumps(stads,   ensure_ascii=False, separators=(',',':'))
    GROUPS_J   = json.dumps(groups,  ensure_ascii=False, separators=(',',':'))

    # 5. Inject into template
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tpl_path = os.path.join(root, 'template.html')
    with open(tpl_path, encoding='utf-8') as f:
        html = f.read()

    html = html.replace('__GAMES__',    GAMES_J)
    html = html.replace('__TEAMS__',    TEAMS_J)
    html = html.replace('__STADIUMS__', STADS_J)
    html = html.replace('__GROUPS__',   GROUPS_J)
    html = html.replace('__NOW__',      NOW)
    html = html.replace('__DONE__',     str(done))
    html = html.replace('__TOTAL__',    str(len(matches)))

    # 6. Write output
    dist = os.path.join(root, 'dist')
    os.makedirs(dist, exist_ok=True)
    with open(os.path.join(dist, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    for fname in ['netlify.toml', '_redirects']:
        src = os.path.join(root, fname)
        if os.path.exists(src):
            import shutil
            shutil.copy(src, os.path.join(dist, fname))

    print(f"\n✅ Built dist/index.html — {len(html)//1024}KB")
    print(f"   {done}/{len(matches)} played · {NOW}")

if __name__ == '__main__':
    main()
