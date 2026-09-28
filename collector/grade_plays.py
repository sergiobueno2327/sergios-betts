"""Sergio's Betts — nightly grader for bets and paper plays (CLV grading rule).

For every ungraded play in the ledger export it computes:
  result      W / L / Void, from the Kalshi market's settlement
  filled      for maker/paper orders: did Kalshi trade at or through our limit before start?
  kalshiClose Kalshi mid (our side, ¢) at the last candle before start
  pinnClose   Pinnacle no-vig % for our side at the last odds snapshot before start
  clvPinn     pinnClose - entry  (the verdict metric)    clvKalshi = kalshiClose - entry
  ret         per-contract return: (100-entry)/entry if W, -1 if L (0 if not filled / void)
  pnl         real-money plays only: stake * ret - fee

Usage: ODDSPAPI_KEY=... python3 grade_plays.py LEDGER_DIR SNAPSHOT_ROOT_or_- OUT.json [--passes]\n  --passes grades a 'passes' export counterfactually (price = entry, else ask for YES / 100-bid for NO).
  Pinnacle close comes from OddsPapi historical odds (exact close); saved snapshots are a fallback.
  LEDGER_DIR    = folder of ledger JSON docs (ArtifactData list with out_dir -> .../bets/*.json)
  SNAPSHOT_ROOT = odds-history checkout (contains data/YYYY-MM-DD/*_pinnacle.jsonl.gz)
  OUT.json      = {doc_id: fields_to_update}; also prints a per-market CLV report.

Plays need these structured fields (added at logging time):
  kalshi_ticker  e.g. KXNFLREC-26SEP27HOUIND-INDJDOWNS2-5
  start          ISO UTC start time, e.g. 2026-09-27T17:00:00Z
  pinn           props:  {"who": "Josh Downs", "mkt": "prop:Receptions", "line": 4.5}
                 totals: {"who": "Medvedev", "mkt": "total", "line": 21.5, "per": "0"}
                 sides:  {"who": "Medvedev", "mkt": "ml", "per": "0", "team": "home"}
  side           YES / NO (YES = Over / the named outcome)
No API keys needed (Kalshi public API + saved snapshots).
"""
import datetime, glob, gzip, json, math, os, statistics, sys, time, unicodedata, re, urllib.request

B = 'https://api.elections.kalshi.com/trade-api/v2'
OP = 'https://api.oddspapi.io/v4'
OP_KEY = os.environ.get('ODDSPAPI_KEY', '')
OP_SPORT = {'NFL': (14, 'NFL'), 'NCAAF': (14, 'NCAA'), 'NBA': (11, 'NBA'), 'WNBA': (11, 'WNBA'), 'NCAAB': (11, 'NCAA'),
            'MLB': (13, 'MLB'), 'NHL': (15, 'NHL'), 'Tennis': (12, None)}
OP_UNIT = {'receptions': 'receptions', 'receivingyards': 'receivingyards', 'rushattempts': 'rushattempts', 'rushingyards': 'rushyards',
           'passcompletions': 'passcompletions', 'passattempts': 'passattempts', 'passingyards': 'passyards', 'touchdownpasses': 'tdpasses',
           'interceptions': 'interceptions', 'rebounds': 'rebounds', 'assists': 'assists', 'points': 'points', 'hits': 'hits', 'bases': 'bases',
           'homeruns': 'homeruns', 'strikeouts': 'strikeouts', 'outs': 'outs', 'shotsongoal': 'shotsongoal', 'saves': 'saves'}
_op_cache = {}


def get(u, headers=None):
    for a in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=headers or {}), timeout=40))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (a + 1))
        except Exception:
            time.sleep(2 * (a + 1))
    return None


def nrm(s):
    return re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower())


def ts(s):
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))


def power_devig(o, u):
    a, b = 1 / o, 1 / u
    lo, hi = 1.0, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        if a ** k + b ** k > 1:
            lo = k
        else:
            hi = k
    return a ** k  # prob of the first outcome


def market(ticker):
    m = get(f'{B}/markets/{ticker}') or get(f'{B}/historical/markets/{ticker}') or {}
    return m.get('market') or {}


def candles(ticker, start_ts, end_ts, interval):
    series = ticker.split('-')[0]
    for u in (f'{B}/series/{series}/markets/{ticker}/candlesticks', f'{B}/historical/markets/{ticker}/candlesticks'):
        c = get(f'{u}?start_ts={int(start_ts)}&end_ts={int(end_ts)}&period_interval={interval}')
        time.sleep(0.4)
        if c and c.get('candlesticks'):
            return c['candlesticks']
    return []


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pinnacle_close(root, play, start):
    spec = play.get('pinn')
    if not spec:
        return None, 'no pinn spec'
    who = nrm(spec.get('who'))
    days = sorted({(start - datetime.timedelta(days=d)).date().isoformat() for d in range(0, 4)})
    best = {}  # ts -> {sel: price}
    for day in days:
        for fn in sorted(glob.glob(os.path.join(root, 'data', day, '*_pinnacle.jsonl.gz'))):
            try:
                with gzip.open(fn, 'rt') as fh:
                    for line in fh:
                        r = json.loads(line)
                        if r['mkt'] != spec['mkt'] or ts(r['ts']) >= start:
                            continue
                        if str(r.get('per', '0')) != str(spec.get('per', '0')):
                            continue
                        if 'line' in spec and spec['line'] is not None and r.get('line') is not None and abs(float(r['line']) - float(spec['line'])) > 1e-6:
                            continue
                        names = nrm(r.get('special') or '') + '|' + nrm(r.get('home')) + '|' + nrm(r.get('away'))
                        if who not in names:
                            continue
                        best.setdefault(r['ts'], {})[str(r['sel']).lower()] = r['price']
            except (OSError, EOFError):
                continue
    for t in sorted(best, reverse=True):
        q = best[t]
        if spec['mkt'] == 'ml':
            if 'home' in q and 'away' in q:
                p_home = power_devig(q['home'], q['away'])
                p = p_home if spec.get('team', 'home') == 'home' else 1 - p_home
                return (p if play['side'] == 'YES' else 1 - p), t
        elif 'over' in q and 'under' in q:
            p_over = power_devig(q['over'], q['under'])
            return (p_over if play['side'] == 'YES' else 1 - p_over), t
    return None, 'no matching snapshot before start'


def op_get(path):
    """OddsPapi GET with pacing + rate-limit retry (free tier ~1 req/s). Cached per run."""
    if path in _op_cache:
        return _op_cache[path]
    url = f"{OP}/{path}{'&' if '?' in path else '?'}apiKey={OP_KEY}"
    for a in range(6):
        time.sleep(1.3)
        try:
            j = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'curl/8'}), timeout=90))
        except Exception:
            j = None
        if isinstance(j, dict) and 'error' in j:
            time.sleep(3 * (a + 1)); continue
        if j is not None:
            _op_cache[path] = j
            return j
    return None


def pinnacle_close_op(play, start):
    """Pinnacle closing no-vig (our side) from OddsPapi historical odds: last Pinnacle quote before start."""
    spec, sp = play.get('pinn') or {}, OP_SPORT.get(play.get('sport'))
    if not (OP_KEY and spec and sp):
        return None, 'oddspapi: no key/spec/sport'
    sid, league = sp
    d0 = (start - datetime.timedelta(hours=14)).date().isoformat()
    d1 = ((start + datetime.timedelta(hours=14)).date() + datetime.timedelta(days=1)).isoformat()   # 'to' is exclusive
    fx = op_get(f'fixtures?sportId={sid}&from={d0}&to={d1}') or []
    who = nrm(spec.get('who'))
    window = 8 * 3600 if sid == 12 else 5400   # tennis order of play shifts by hours
    cand = [f for f in fx if abs((ts(f['startTime']) - start).total_seconds()) <= window
            and (league is None or league in (f.get('tournamentName') or ''))]
    unit = spec['mkt'].split(':', 1)[1] if spec['mkt'].startswith('prop:') else None
    if not unit:  # game market: participant name must match
        cand = [f for f in cand if who in nrm(f.get('participant1Name')) + '|' + nrm(f.get('participant2Name'))]
    mk = op_get(f'markets?sportId={sid}') or []
    if unit:
        mtype = 'playertotals-' + OP_UNIT.get(nrm(unit), nrm(unit))
    elif spec['mkt'] == 'total':
        mtype = 'totals-games' if sid == 12 else 'totals'
    else:
        mtype = 'moneyline'
    mids = {str(m['marketId']): m for m in mk if m.get('marketType') == mtype and str(m.get('period')) == 'result'
            and (spec['mkt'] == 'ml' or abs(float(m.get('handicap') or 0) - float(spec.get('line') or 0)) < 1e-6)}
    if not mids:
        return None, f'oddspapi: no market {mtype} line {spec.get("line")}'
    for f in cand[:12]:
        cut = ts(f['trueStartTime']) if f.get('trueStartTime') else start   # actual first serve / kickoff when known
        # BUG FOUND + FIXED 2026-09-28: this used bare "bookmakers=pinnacle" and looked up the
        # response under key 'pinnacle'. Under the paid plan (same cutover as the 3 scan scripts)
        # bare "pinnacle" 403s RESTRICTED_ACCESS -- confirmed live -- and even a successful call
        # keys its response "pinnacle+30", not "pinnacle", so the dict lookup would have missed
        # regardless. Net effect: pinnacle_close_op() has been failing 100% of the time since the
        # 2026-09-28 cutover (every call falls through all 6 retries, burning real time, then
        # returns nothing) -- the nightly grader's clvPinn (the system's verdict metric) has
        # likely been null for every play graded since then, silently, unless the snapshot
        # fallback below happened to cover it. Real slug is "pinnacle+30", URL-encoded as the
        # literal '+' decodes to a space server-side and 400s (same gotcha as the 3 scan scripts).
        h = op_get(f"historical-odds?fixtureId={f['fixtureId']}&bookmakers=pinnacle%2B30") or {}
        markets = (((h.get('bookmakers') or {}).get('pinnacle+30') or {}).get('markets') or {})
        pid = '0'
        if unit:
            pl = op_get(f"players?sportId={sid}&tournamentId={f['tournamentId']}") or {}
            names = {}
            for t in (pl.get('participants') or {}).values():
                for x in t.get('players', []):
                    n = x['playerName']
                    if ',' in n:
                        l, fn = n.split(',', 1); n = fn.strip() + ' ' + l.strip()
                    names[str(x['playerId'])] = nrm(n)
            pids = [k for k, v in names.items() if v == who]
            if not pids:
                continue
            pid = pids[0]
        last = {}
        for mid, mv in markets.items():
            if mid not in mids:
                continue
            oname = {str(o['outcomeId']): o['outcomeName'] for o in mids[mid]['outcomes']}
            for oid, ov in (mv.get('outcomes') or {}).items():
                qs = [q for q in (ov.get('players') or {}).get(pid, []) if q.get('price') and q['createdAt'] < cut.strftime('%Y-%m-%dT%H:%M:%S')]
                if qs:
                    last[oname.get(oid, oid)] = qs[-1]
        if spec['mkt'] == 'ml' and '1' in last and '2' in last:
            p1 = power_devig(last['1']['price'], last['2']['price'])
            home_is_1 = who in nrm(f.get('participant1Name'))
            p = p1 if (spec.get('team', 'home') == 'home') == home_is_1 else 1 - p1
            pinnacle_close_op.cut = cut
            return (p if play['side'] == 'YES' else 1 - p), 'oddspapi ' + max(last['1']['createdAt'], last['2']['createdAt'])
        if 'Over' in last and 'Under' in last:
            po = power_devig(last['Over']['price'], last['Under']['price'])
            pinnacle_close_op.cut = cut
            return (po if play['side'] == 'YES' else 1 - po), 'oddspapi ' + max(last['Over']['createdAt'], last['Under']['createdAt'])
    return None, f'oddspapi: not found ({len(cand)} candidate games)'


def nhl_actual_sog(spec, start):
    """Real shots-on-goal for a ticker-less NHL SOG play (Novig/ProphetX/Fliff/PrizePicks --
    Kalshi carries no NHL SOG market at all, confirmed repeatedly). Built 2026-09-28 to close the
    gap nhl_scan.py's to_ledger_docs docstring flagged: these plays got clvPinn but no result/W-L
    at all, since grade()'s only settlement source was Kalshi. NHL API boxscore only gives
    'F. Lastname', not a full first name, so matching is by team + last name (unique within one
    game's ~40 players in practice)."""
    who_last = nrm((spec.get('who') or '').split()[-1])
    if not who_last:
        return None, 'no player name in pinn spec'
    for d in (start.date(), start.date() - datetime.timedelta(days=1), start.date() + datetime.timedelta(days=1)):
        j = get(f"https://api-web.nhle.com/v1/score/{d.isoformat()}", headers={'User-Agent': 'curl/8'}) or {}
        for g in j.get('games', []):
            if g.get('gameState') not in ('FINAL', 'OFF'):
                continue
            gt = ts(g['startTimeUTC']) if g.get('startTimeUTC') else None
            if gt and abs((gt - start).total_seconds()) > 6 * 3600:
                continue
            gid = g['id']
            box = get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore", headers={'User-Agent': 'curl/8'}) or {}
            hits = []
            for side in ('homeTeam', 'awayTeam'):
                for grp in ('forwards', 'defense'):
                    for p in ((box.get('playerByGameStats') or {}).get(side, {}) or {}).get(grp, []) or []:
                        nm = (p.get('name') or {}).get('default', '')
                        last = nrm(nm.split('.', 1)[-1].strip()) if '.' in nm else nrm(nm)
                        if last == who_last:
                            hits.append(p)
            if len(hits) == 1:
                return int(hits[0].get('sog', 0) or 0), f'nhl boxscore game {gid}'
            if len(hits) > 1:
                return None, f'ambiguous name match ({len(hits)} players named {who_last}) in game {gid}'
    return None, 'no matching finished NHL game/player found'


def mlb_actual_stat(spec, start):
    """Real hits/total-bases for a ticker-less MLB Track B play (softness_scan.py, a player Kalshi
    doesn't list at all -- confirmed different from most MLB Track B plays, which DO have a
    kalshi_ticker even when the entry price came from Novig/etc, since Kalshi still lists and
    settles the equivalent Hits market for most players). Built 2026-09-28, same gap class as
    NHL SOG above."""
    unit = (spec.get('mkt') or '')
    stat_key = {'prop:Hits': 'hits', 'prop:Bases': 'totalBases'}.get(unit)
    if not stat_key:
        return None, f'no stat mapping for {unit}'
    who = nrm(spec.get('who'))
    day = start.date().isoformat()
    j = get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={day}") or {}
    found_no_pa = None
    for dt in j.get('dates', []):
        for g in dt.get('games', []):
            if (g.get('status') or {}).get('abstractGameState') != 'Final':
                continue
            gpk = g['gamePk']
            live = get(f"https://statsapi.mlb.com/api/v1.1/game/{gpk}/feed/live") or {}
            box = ((live.get('liveData') or {}).get('boxscore') or {}).get('teams') or {}
            for side in ('home', 'away'):
                for pdata in ((box.get(side) or {}).get('players') or {}).values():
                    nm = (pdata.get('person') or {}).get('fullName', '')
                    if nrm(nm) == who:
                        val = ((pdata.get('stats') or {}).get('batting') or {}).get(stat_key)
                        if val is not None:
                            return int(val), f'mlb boxscore game {gpk}'
                        found_no_pa = gpk   # on the roster/game but no batting stats recorded (DNP/no PA)
    if found_no_pa is not None:
        return None, f'player found in game {found_no_pa} but has no batting stats (did not play / no PA) -- likely void, not gradable as W/L'
    return None, 'no matching finished MLB game/player found'


def real_result(play, start):
    """Dispatch to the right real-world-outcome fetcher for a ticker-less play, by sport + market.
    Returns (win: bool, note: str) or (None, note) if no path exists / nothing found yet."""
    spec = play.get('pinn') or {}
    sport = play.get('sport')
    if sport == 'NHL' and spec.get('mkt') == 'prop:Shots On Goal':
        val, note = nhl_actual_sog(spec, start)
    elif sport == 'MLB' and spec.get('mkt') in ('prop:Hits', 'prop:Bases'):
        val, note = mlb_actual_stat(spec, start)
    else:
        return None, 'no real-outcome path for this sport/market'
    if val is None:
        return None, note
    line = float(spec.get('line'))
    over = val > line   # push impossible at a .5 line
    win = over if play['side'].upper() == 'YES' else not over
    return win, f'{note}, actual {val} vs line {line}'


def grade(play, root, now):
    out = {}
    tk = play.get('kalshi_ticker')
    m = None
    # BUG FOUND + FIXED 2026-09-28 (2nd audit pass, same day): this used to hard-require
    # kalshi_ticker up front and bail with "needs kalshi_ticker" before ever calling
    # pinnacle_close_op() -- even for a play whose own logged note says it's a non-Kalshi-venue
    # prop (e.g. Novig-only) and explicitly expects "grader will still resolve pinnClose/CLV".
    # Confirmed live: 2026-09-27-sabato-hits-o sat with gradeNote "needs kalshi_ticker" and no
    # clvPinn ever computed, contradicting its own note. There's no automated source for W/L on
    # a non-Kalshi venue (no settlement feed to poll), so result/ret/pnl/graded still require a
    # Kalshi ticker -- but CLV (the actual verdict metric) doesn't depend on Kalshi at all and
    # was being needlessly blocked by this. Fixed: without a ticker, skip everything
    # Kalshi-specific (kalshiClose, fill check, result) and still compute pinnClose/clvPinn.
    if tk:
        m = market(tk)
        if not m:
            return {'gradeNote': f'Kalshi market {tk} not found'}
    elif not play.get('start'):
        return {'gradeNote': 'needs kalshi_ticker or start'}
    start = ts(play['start']) if play.get('start') else ts(m.get('occurrence_datetime') or m['close_time'])
    if now < start:
        return None  # not started yet
    side, entry = play['side'].upper(), float(play['entry'])
    pinnacle_close_op.cut = None
    pc, pnote = pinnacle_close_op(play, start)
    if pinnacle_close_op.cut:
        start = pinnacle_close_op.cut   # actual start (tennis order of play etc.)
    kc = None
    out['kalshiClose'] = None
    out['filled'] = True
    if m:
        # Kalshi close: last 1-min candle before start (fallback hourly)
        cs = candles(tk, start.timestamp() - 3 * 3600, start.timestamp(), 1) or candles(tk, start.timestamp() - 48 * 3600, start.timestamp(), 60)
        for c in reversed(cs):
            b, a = f(c.get('yes_bid', {}).get('close_dollars')), f(c.get('yes_ask', {}).get('close_dollars'))
            if b and a and 0 < b < a < 1:
                mid = (a + b) / 2
                kc = round((mid if side == 'YES' else 1 - mid) * 100, 1)
                break
        out['kalshiClose'] = kc
        # Fill check for maker / paper orders: any trade at or through our limit between logging and start
        maker = play.get('orderType') == 'maker' or 'pending fill' in str(play.get('status', '')).lower()
        if maker:
            t0 = ts(play['date'] + 'T00:00:00Z').timestamp()
            hc = candles(tk, t0, start.timestamp(), 60)
            lim = entry / 100
            if side == 'YES':
                filled = any(f(c.get('price', {}).get('low_dollars')) is not None and f(c['price']['low_dollars']) <= lim for c in hc)
            else:
                filled = any(f(c.get('price', {}).get('high_dollars')) is not None and f(c['price']['high_dollars']) >= 1 - lim for c in hc)
            out['filled'] = filled
    if pc is None and root and os.path.isdir(os.path.join(root, 'data')):
        pc2, pnote2 = pinnacle_close(root, play, start)
        if pc2 is not None:
            pc, pnote = pc2, 'snapshot ' + pnote2
        else:
            pnote = f'{pnote}; {pnote2}'

    out['pinnClose'] = round(pc * 100, 1) if pc is not None else None
    out['pinnCloseAt'] = pnote
    out['clvPinn'] = round(pc * 100 - entry, 1) if pc is not None else None
    out['clvKalshi'] = round(kc - entry, 1) if kc is not None else None
    if not m:
        # Real-world-outcome grading, added 2026-09-28: ticker-less plays (NHL SOG always;
        # occasional MLB Track B players Kalshi doesn't list) used to stop here with clvPinn but
        # no result/W-L at all. Now try the actual game box score before giving up.
        win, rnote = real_result(play, start)
        out['filled'] = True
        if win is None:
            out['gradeNote'] = f'CLV computed via Pinnacle close; no Kalshi ticker — result: {rnote}'
            return out
        out['result'] = 'W' if win else 'L'
        out['ret'] = round((100 - entry) / entry, 4) if win else -1.0
        out['graded'] = True
        out['gradedAt'] = now.strftime('%Y-%m-%dT%H:%MZ')
        out['gradeNote'] = f'graded via real-world result (no Kalshi ticker): {rnote}'
        return out
    res = (m.get('result') or '').lower()
    if m.get('status') in ('finalized', 'settled') and res in ('yes', 'no'):
        win = (res == 'yes') == (side == 'YES')
        out['result'] = 'W' if win else 'L'
        r = (100 - entry) / entry if win else -1.0
        out['ret'] = round(r, 4) if out['filled'] else 0.0
        if float(play.get('stake') or 0) > 0:
            out['pnl'] = round(float(play['stake']) * r - float(play.get('fee') or 0), 2)
        out['graded'] = True
        out['gradedAt'] = now.strftime('%Y-%m-%dT%H:%MZ')
    elif res in ('void', 'scratched') or m.get('status') == 'voided':
        out.update(result='Void', ret=0.0, graded=True, gradedAt=now.strftime('%Y-%m-%dT%H:%MZ'))
    else:
        out['gradeNote'] = f"started; Kalshi status {m.get('status')} — result pending"
    return out


def grade_pass(p, root, now):
    """Counterfactual grade for a logged PASS: what would have happened at the price we passed on."""
    q = dict(p)
    if q.get('entry') is None:
        if q.get('side', '').upper() == 'NO':
            q['entry'] = 100 - float(q['bid']) if q.get('bid') is not None else None
        else:
            q['entry'] = q.get('ask')
    if q.get('entry') is None or not q.get('side'):
        return {'gradeNote': 'pass lacks side/price'}
    q.update(orderType='taker', stake=0, status='pass')
    u = grade(q, root, now)
    if u and u.get('result') in ('W', 'L'):
        u['result'] = 'Would have won' if u['result'] == 'W' else 'Would have lost'
        u['passPrice'] = q['entry']
    return u


def report(plays):
    groups = {}
    for p in plays:
        if p.get('clvPinn') is None or p.get('filled') is False:
            continue
        key = f"{p.get('sport')} {'Track ' + p.get('track', '?')} {p.get('market', '').split()[0] if p.get('track') == 'A' else ''}".strip()
        groups.setdefault(key, []).append(p)
    lines = []
    for k, ps in sorted(groups.items()):
        c = [p['clvPinn'] for p in ps]
        se = statistics.pstdev(c) / math.sqrt(len(c)) if len(c) > 1 else float('nan')
        beat = sum(x > 0 for x in c) / len(c) * 100
        rs = [p['ret'] for p in ps if p.get('ret') is not None]
        lines.append(f"{k}: n {len(c)} | Pinnacle CLV {statistics.mean(c):+.2f} ±{se:.2f} | beat close {beat:.0f}%"
                     + (f" | ROI {statistics.mean(rs) * 100:+.1f}% (n {len(rs)})" if rs else '')
                     + (' | checkpoint: FIRST READ' if len(c) >= 100 else '') + (' | checkpoint: DECISION' if len(c) >= 300 else ''))
    return lines


def main():
    ledger, root, out_fn = sys.argv[1:4]
    passes_mode = '--passes' in sys.argv   # grade a 'passes' export counterfactually
    now = datetime.datetime.now(datetime.timezone.utc)
    docs = {os.path.basename(fn)[:-5]: json.load(open(fn)) for fn in glob.glob(os.path.join(ledger, '*.json'))}
    updates = {}
    for did, p in sorted(docs.items()):
        if p.get('graded') or (not passes_mode and 'pass' in str(p.get('status', '')).lower()):
            continue
        if passes_mode and p.get('result'):
            continue   # already graded by hand
        try:
            u = grade_pass(p, root, now) if passes_mode else grade(p, root, now)
        except Exception as e:  # never let one play break the run
            u = {'gradeNote': f'grader error: {e}'}
        if u:
            updates[did] = u
            docs[did] = {**p, **u}
            print(did, json.dumps(u))
    json.dump(updates, open(out_fn, 'w'), indent=1)
    if passes_mode:
        g = [d for d in docs.values() if str(d.get('result', '')).startswith('Would')]
        w = sum(str(d['result']).startswith('Would have won') for d in g)
        c = [d['clvPinn'] for d in g if d.get('clvPinn') is not None]
        print(f"--- Passes: {len(g)} graded | would have won {w} / lost {len(g) - w}"
              + (f" | avg Pinnacle CLV at pass price {statistics.mean(c):+.2f} (n {len(c)})" if c else '') + ' ---')
    else:
        print('\n'.join(['--- CLV report (all graded, filled plays) ---'] + report(list(docs.values()))))


if __name__ == '__main__':
    main()
