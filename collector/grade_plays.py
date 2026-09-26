"""Sergio's Betts — nightly grader for bets and paper plays (CLV grading rule).

For every ungraded play in the ledger export it computes:
  result      W / L / Void, from the Kalshi market's settlement
  filled      for maker/paper orders: did Kalshi trade at or through our limit before start?
  kalshiClose Kalshi mid (our side, ¢) at the last candle before start
  pinnClose   Pinnacle no-vig % for our side at the last odds snapshot before start
  clvPinn     pinnClose - entry  (the verdict metric)    clvKalshi = kalshiClose - entry
  ret         per-contract return: (100-entry)/entry if W, -1 if L (0 if not filled / void)
  pnl         real-money plays only: stake * ret - fee

Usage: ODDSPAPI_KEY=... python3 grade_plays.py LEDGER_DIR SNAPSHOT_ROOT_or_- OUT.json
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


def get(u):
    for a in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u), timeout=40))
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
        h = op_get(f"historical-odds?fixtureId={f['fixtureId']}&bookmakers=pinnacle") or {}
        markets = (((h.get('bookmakers') or {}).get('pinnacle') or {}).get('markets') or {})
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


def grade(play, root, now):
    out = {}
    tk = play.get('kalshi_ticker')
    if not tk:
        return {'gradeNote': 'needs kalshi_ticker'}
    m = market(tk)
    if not m:
        return {'gradeNote': f'Kalshi market {tk} not found'}
    start = ts(play['start']) if play.get('start') else ts(m.get('occurrence_datetime') or m['close_time'])
    if now < start:
        return None  # not started yet
    side, entry = play['side'].upper(), float(play['entry'])
    pinnacle_close_op.cut = None
    pc, pnote = pinnacle_close_op(play, start)
    if pinnacle_close_op.cut:
        start = pinnacle_close_op.cut   # actual start (tennis order of play etc.)
    # Kalshi close: last 1-min candle before start (fallback hourly)
    cs = candles(tk, start.timestamp() - 3 * 3600, start.timestamp(), 1) or candles(tk, start.timestamp() - 48 * 3600, start.timestamp(), 60)
    kc = None
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
    else:
        out['filled'] = True
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
    now = datetime.datetime.now(datetime.timezone.utc)
    docs = {os.path.basename(fn)[:-5]: json.load(open(fn)) for fn in glob.glob(os.path.join(ledger, '*.json'))}
    updates = {}
    for did, p in sorted(docs.items()):
        if p.get('graded') or 'pass' in str(p.get('status', '')).lower():
            continue
        try:
            u = grade(p, root, now)
        except Exception as e:  # never let one play break the run
            u = {'gradeNote': f'grader error: {e}'}
        if u:
            updates[did] = u
            docs[did] = {**p, **u}
            print(did, json.dumps(u))
    json.dump(updates, open(out_fn, 'w'), indent=1)
    print('\n'.join(['--- CLV report (all graded, filled plays) ---'] + report(list(docs.values()))))


if __name__ == '__main__':
    main()
