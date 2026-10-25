"""NBA actual player stat from ESPN box score (points/rebounds/assists/threes/PRA)."""
import json, re, unicodedata, urllib.request, datetime

def _get(u):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'curl/8'}), timeout=40))
    except Exception:
        return None

def _n(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z]', '', s.replace('jr.', '').replace('iii', '').replace('ii', ''))

_cache = {}

def nba_actual(play, start):
    spec = play.get('pinn') or {}
    who, mkt = _n(spec.get('who')), str(spec.get('mkt', '')).split(':')[-1]
    # US game date: start in UTC minus 8h covers evening tips
    day = (start - datetime.timedelta(hours=8)).strftime('%Y%m%d')
    sb = _cache.get(day) or _get(f'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={day}')
    if not sb:
        return None, 'espn nba scoreboard unavailable'
    _cache[day] = sb
    teams = [t.strip() for t in str(play.get('game', '')).split('@')]
    for e in sb.get('events', []):
        names = ' '.join(c['team']['displayName'] for c in e['competitions'][0]['competitors'])
        if teams and not all(t.split()[-1] in names for t in teams if t):
            continue
        if e['status']['type']['state'] != 'post':
            return None, f"game {e['id']} not final yet"
        key = 'ev' + e['id']
        box = _cache.get(key) or _get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={e['id']}")
        if not box:
            return None, 'espn nba boxscore unavailable'
        _cache[key] = box
        for t in box['boxscore']['players']:
            s = t['statistics'][0]
            ks = s['keys']
            for a in s['athletes']:
                if _n(a['athlete']['displayName']) != who:
                    continue
                if a.get('didNotPlay') or not a.get('stats'):
                    return None, f"{a['athlete']['displayName']} did not play (void, not W/L)"
                v = dict(zip(ks, a['stats']))
                pts, reb, ast = float(v['points']), float(v['rebounds']), float(v['assists'])
                thr = float(v['threePointFieldGoalsMade-threePointFieldGoalsAttempted'].split('-')[0])
                val = {'Points': pts, 'Rebounds': reb, 'Assists': ast, 'Threes': thr, 'PRA': pts + reb + ast,
                       'Pts+Rebs+Asts': pts + reb + ast}.get(mkt)
                if val is None:
                    return None, f'unsupported NBA market {mkt}'
                return val, f"espn nba boxscore event {e['id']}"
        return None, 'player not in boxscore (did not play)'
    return None, 'no matching finished NBA game'
