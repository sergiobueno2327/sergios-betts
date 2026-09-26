"""Sergio's Betts — odds history collector (one snapshot per run).

Saves Pinnacle (via Pinnapi) and Kalshi prices for in-scope sports so we build our own
open-to-close history for backtests and CLV grading.

Usage:  PINNAPI_KEY=... python3 collect_odds.py OUT_DIR
Writes: OUT_DIR/data/YYYY-MM-DD/HHMMZ_pinnacle.jsonl.gz and HHMMZ_kalshi.jsonl.gz
        (UTC date/time of the snapshot). Prints a one-line summary.

Row formats (one JSON object per line):
  pinnacle: {ts, sport, league, eid, parent, starts, home, away, mkt, per, sel, line, price, max}
     mkt = ml | spread | total | tt_home | tt_away | prop:<unit> ; per = 0 game, 1 first half/period ...
     price = decimal odds (raw, with vig). De-vig later.
  kalshi:   {ts, series, event, ticker, title, sub, strike, close_time, bid, ask, last, vol, oi}
     bid/ask in dollars (Yes side). No bid = 1 - yes ask.
No API keys are stored in this file or the output.
"""
import gzip, json, os, sys, time, datetime, urllib.request

PINNAPI_KEY = os.environ.get('PINNAPI_KEY', '')
PINN_SPORTS = {2: 'Tennis', 3: 'Basketball', 4: 'Hockey', 5: 'Football', 6: 'Baseball'}
PINN_LEAGUES = ('NBA', 'WNBA', 'NHL', 'NFL', 'NCAA', 'MLB', 'ATP', 'WTA', 'Challenger')
KALSHI_SERIES = [
    # NBA / WNBA
    'KXNBAREB', 'KXNBAAST', 'KXNBAPTS', 'KXNBA3PT', 'KXNBAGAME', 'KXNBASPREAD', 'KXNBATOTAL',
    'KXWNBAREB', 'KXWNBAAST', 'KXWNBAPTS', 'KXWNBA3PT',
    # MLB
    'KXMLBHIT', 'KXMLBTB', 'KXMLBKS', 'KXMLBHRR', 'KXMLBHR', 'KXMLBOUTS', 'KXMLBGAME', 'KXMLBTOTAL', 'KXMLBSPREAD',
    # NFL
    'KXNFLPASSYDS', 'KXNFLPASSCOMP', 'KXNFLPASSATT', 'KXNFLPASSTDS', 'KXNFLPASSINT', 'KXNFLREC', 'KXNFLRECYDS',
    'KXNFLRSHATT', 'KXNFLRSHYDS', 'KXNFLANYTD', 'KXNFLGAME', 'KXNFLSPREAD', 'KXNFLTOTAL',
    # NHL
    'KXNHLGOAL', 'KXNHLPTS', 'KXNHLAST', 'KXNHLSAVE', 'KXNHLGAME', 'KXNHLTOTAL', 'KXNHLSPREAD',
    # College
    'KXNCAAFGAME', 'KXNCAAFSPREAD', 'KXNCAAFTOTAL', 'KXNCAAMBGAME', 'KXNCAAMBSPREAD', 'KXNCAAMBTOTAL',
    # Tennis
    'KXATPMATCH', 'KXWTAMATCH', 'KXATPCHALLENGERMATCH', 'KXWTACHALLENGERMATCH', 'KXATPGAMETOTAL', 'KXWTAGTOTAL',
    'KXATPGAMESPREAD',
]
AUTO_SERIES_KEYWORDS = ('SOG', 'SHOTS')  # auto-add any new NHL shots-on-goal series Kalshi lists
KALSHI = 'https://api.elections.kalshi.com/trade-api/v2'


def get(url, headers=None, tries=3):
    for a in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=40))
        except Exception:
            time.sleep(2 * (a + 1))
    return None


def pinnacle_rows(ts):
    rows, calls = [], 0
    if not PINNAPI_KEY:
        return rows, calls
    for sid, sname in PINN_SPORTS.items():
        j = get(f'https://pinnapi.com/kit/v1/prematch/fixtures?sport_id={sid}&include_specials=1',
                {'x-portal-apikey': PINNAPI_KEY, 'User-Agent': 'curl/8'}) or {}  # Pinnapi 403s Python's default UA (fixed 2026-09-25)
        calls += 1
        for e in j.get('events', []):
            lg = e.get('league_name') or ''
            if not any(k in lg for k in PINN_LEAGUES):
                continue
            base = dict(ts=ts, sport=sname, league=lg, eid=e.get('event_id'), parent=e.get('parent_id'),
                        starts=e.get('starts'), home=e.get('home'), away=e.get('away'))
            cat = e.get('special_category')
            if cat:  # props / specials
                if cat not in ('Player Props', 'Game Props'):
                    continue  # skip futures
                unit = e.get('special_units') or ''
                for per, mlist in (e.get('special_markets') or {}).items():
                    for m in mlist:
                        for p in m.get('prices', []):
                            if p.get('price'):
                                rows.append(dict(base, mkt=f'prop:{unit}', special=e.get('special'), per=per.replace('num_', ''),
                                                 sel=p.get('name'), line=p.get('points'), price=p['price'], max=m.get('max_risk')))
                continue
            for per, P in (e.get('periods') or {}).items():
                if P.get('status') not in (None, 'open'):
                    continue
                n = str(P.get('number', per.replace('num_', '')))
                meta = P.get('meta') or {}
                ml = P.get('money_line') or {}
                for sel in ('home', 'away', 'draw'):
                    if ml.get(sel):
                        rows.append(dict(base, mkt='ml', per=n, sel=sel, line=None, price=ml[sel], max=meta.get('max_money_line')))
                for s in (P.get('spreads') or {}).values():
                    for sel in ('home', 'away'):
                        if s.get(sel):
                            rows.append(dict(base, mkt='spread', per=n, sel=sel, line=s.get('hdp'), price=s[sel], max=s.get('max')))
                for t in (P.get('totals') or {}).values():
                    for sel in ('over', 'under'):
                        if t.get(sel):
                            rows.append(dict(base, mkt='total', per=n, sel=sel, line=t.get('points'), price=t[sel], max=t.get('max')))
                for side, tts in (P.get('team_totals') or {}).items():
                    for t in (tts or {}).values():
                        for sel in ('over', 'under'):
                            if t.get(sel):
                                rows.append(dict(base, mkt=f'tt_{side}', per=n, sel=sel, line=t.get('points'), price=t[sel], max=t.get('max')))
        time.sleep(1)
    return rows, calls


def kalshi_rows(ts):
    series = list(KALSHI_SERIES)
    s = get(f'{KALSHI}/series?category=Sports') or {}
    for x in s.get('series', []):
        t = x.get('ticker', '')
        if t.startswith('KXNHL') and any(k in t for k in AUTO_SERIES_KEYWORDS) and t not in series:
            series.append(t)
    rows, new_sog = [], [t for t in series if t not in KALSHI_SERIES]
    for st in series:
        cur = ''
        while True:
            d = get(f'{KALSHI}/events?series_ticker={st}&status=open&limit=200&with_nested_markets=true'
                    + (f'&cursor={cur}' if cur else '')) or {}
            for e in d.get('events', []):
                for m in e.get('markets', []):
                    if m.get('status') not in ('active', 'open'):
                        continue
                    f = lambda k: float(m.get(k) or 0)
                    rows.append(dict(ts=ts, series=st, event=e.get('event_ticker'), ticker=m.get('ticker'),
                                     title=m.get('title'), sub=m.get('yes_sub_title'), strike=m.get('floor_strike'),
                                     close_time=m.get('close_time'), bid=f('yes_bid_dollars'), ask=f('yes_ask_dollars'),
                                     last=f('last_price_dollars'), vol=m.get('volume_fp') or m.get('volume'),
                                     oi=m.get('open_interest_fp') or m.get('open_interest')))
            cur = d.get('cursor')
            if not cur or not d.get('events'):
                break
            time.sleep(0.2)
        time.sleep(0.1)
    return rows, new_sog


def dump(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, 'wt') as f:
        for r in rows:
            f.write(json.dumps(r, separators=(',', ':')) + '\n')


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else '.'
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    stem = os.path.join(out, 'data', now.strftime('%Y-%m-%d'), now.strftime('%H%MZ'))
    pr, calls = pinnacle_rows(ts)
    kr, new_sog = kalshi_rows(ts)
    if pr:
        dump(stem + '_pinnacle.jsonl.gz', pr)
    if kr:
        dump(stem + '_kalshi.jsonl.gz', kr)
    leagues = {}
    for r in pr:
        leagues[r['league']] = leagues.get(r['league'], 0) + 1
    top = ', '.join(f'{k} {v}' for k, v in sorted(leagues.items(), key=lambda x: -x[1])[:6])
    print(f'{ts} pinnacle rows {len(pr)} ({calls} Pinnapi calls{"" if PINNAPI_KEY else ", NO KEY"}) [{top}] | '
          f'kalshi rows {len(kr)}' + (f' | NEW NHL SOG SERIES: {new_sog}' if new_sog else ''))


if __name__ == '__main__':
    main()
