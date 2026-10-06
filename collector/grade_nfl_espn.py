"""Real-result grader for NFL props via ESPN box scores (no Kalshi ticker needed). Added 2026-10-05.
Usage: python3 grade_nfl_espn.py LEDGER_DIR OUT_DIR ESPN_EVENT_ID[,ID...] [--include-invalidated]
Writes OUT_DIR/<doc_id>.json with result/ret/graded fields for ungraded NFL plays whose player is in the box score.
Markets handled: receptions, receiving yards, rushing yards/attempts, pass attempts/completions/yards, interceptions.
Plays with status containing INVALIDATED are skipped unless --include-invalidated (they were killed pre-game).
"""
import sys, os, json, glob, re, unicodedata, datetime, urllib.request

def nrm(s): return re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower())
def get(u): return json.load(urllib.request.urlopen(u, timeout=30))

def box_stats(event_id):
    s = get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}")
    out = {}
    for t in s['boxscore']['players']:
        for cat in t['statistics']:
            L = cat['labels']
            for a in cat['athletes']:
                d = out.setdefault(nrm(a['athlete']['displayName']), {})
                st = dict(zip(L, a['stats']))
                n = cat['name']
                if n == 'receiving': d['receptions'], d['receiving_yards'] = float(st['REC']), float(st['YDS'])
                if n == 'rushing': d['rushing_attempts'], d['rushing_yards'] = float(st['CAR']), float(st['YDS'])
                if n == 'passing':
                    c, att = st['C/ATT'].split('/'); d['passing_completions'], d['passing_attempts'] = float(c), float(att)
                    d['passing_yards'], d['passing_interceptions'] = float(st['YDS']), float(st['INT'])
    return out

KEYS = [('receiving_yards', ['receivingyards', 'receptionyds', 'receiving_yards', 'reception_yds', 'receiving yards']),
        ('receptions', ['receptions']), ('rushing_yards', ['rushingyards', 'rushyards', 'rushing yards']),
        ('rushing_attempts', ['rushingattempts', 'rushattempts', 'rushing attempts']),
        ('passing_attempts', ['passattempts', 'passingattempts', 'pass attempts']),
        ('passing_completions', ['passcompletions', 'passingcompletions', 'pass completions']),
        ('passing_yards', ['passingyards', 'passyards']), ('passing_interceptions', ['interceptions', 'passinginterceptions'])]

def stat_key(market):
    m = re.sub(r'\s+(over|under)\s+[\d.]+$', '', market.lower()).replace('_', '').replace(' ', '')
    for k, alts in KEYS:
        if any(m == a.replace(' ', '').replace('_', '') for a in alts): return k
    return None

_box_cache, _day_cache = {}, {}

def nfl_actual(play, start_iso):
    """(value, note) for a play's NFL stat from ESPN box scores, or (None, why). Used by grade_plays.real_result."""
    sk = stat_key(play.get('market', ''))
    if not sk: return None, 'unsupported NFL market'
    who = nrm(play.get('player', ''))
    start = datetime.datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
    if start > datetime.datetime.now(datetime.timezone.utc): return None, 'not started'
    for off in (0, -1):
        day = (start + datetime.timedelta(days=off)).strftime('%Y%m%d')
        if day not in _day_cache:
            try: _day_cache[day] = get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={day}")['events']
            except Exception: _day_cache[day] = []
        for e in _day_cache[day]:
            if e['status']['type']['name'] != 'STATUS_FINAL': continue
            if e['id'] not in _box_cache:
                try: _box_cache[e['id']] = box_stats(e['id'])
                except Exception: _box_cache[e['id']] = {}
            b = _box_cache[e['id']]
            if who in b and sk in b[who]:
                return b[who][sk], f"ESPN box score {e['shortName']}"
    return None, 'player not in a final ESPN box score (DNP or game not final)'


if __name__ == '__main__':
    led, outd, ids = sys.argv[1], sys.argv[2], sys.argv[3].split(',')
    incl = '--include-invalidated' in sys.argv
    os.makedirs(outd, exist_ok=True)
    box = {}
    for i in ids:
        for k, v in box_stats(i).items(): box.setdefault(k, {}).update(v)
    for f in sorted(glob.glob(os.path.join(led, '*.json'))):
        d = json.load(open(f)); doc = os.path.basename(f)[:-5]
        if d.get('sport') != 'NFL' or d.get('result') or d.get('graded'): continue
        if 'INVALIDATED' in (d.get('status') or '') and not incl: continue
        who = nrm(d.get('player', '')); sk = stat_key(d.get('market', ''))
        line = (d.get('pinn') or {}).get('line')
        if line is None:
            mm = re.search(r'(?:over|under)\s+([\d.]+)\s*$', d.get('market', ''), re.I)
            line = float(mm.group(1)) if mm else None
        if os.path.exists(os.path.join(led, '..', 'upd', doc + '.json')) and json.load(open(os.path.join(led, '..', 'upd', doc + '.json'))).get('graded'): continue
        if who not in box or not sk or line is None or sk not in box[who]:
            print('skip', doc, who in box, sk); continue
        actual = box[who][sk]
        over = actual > line
        won = over if d.get('side') == 'YES' else (not over)
        entry = d.get('entry')
        ret = round((100 - entry) / entry, 4) if won else -1.0
        res = dict(result='W' if won else 'L', ret=ret, graded=True,
                   gradedAt=datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'),
                   gradeNote=f"graded via ESPN box score: {sk} actual {actual:g} vs line {line}")
        json.dump(res, open(os.path.join(outd, doc + '.json'), 'w'))
        print(doc, d['side'], res['result'], res['gradeNote'], 'ret', ret)
