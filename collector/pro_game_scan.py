"""Sergio's Betts -- PRO game-line Track B scan: NFL / NBA / NHL / MLB moneyline, spread, total (added 2026-10-08, Sergio's request).

Same test and bars as ncaaf_game_scan.py: Pinnacle power-de-vigged fair (two-sided, SAME half-point line, Pinnacle margin <= 8%)
vs the Kalshi price at that line, BOTH sides of each Kalshi market, price zone 35-75c, pregame only. Edge = Pinnacle fair - Kalshi
taker cost (ask) in pts; maker-at-bid+1c edge printed as info. LIVE bar >= 3.0 pts (track "B", stake 0 until Sergio places it);
B2 2.0 to <3.0 pts is paper-only and auto-logged (track "B2", stake 0).

Pinnacle source = The Odds API LIVE Pinnacle only (h2h, spreads, totals, alternate_spreads, alternate_totals), so there is no
OddsPapi ghost-line / stale problem (that is what killed 11 of 15 raw college hits). MLB works too (OddsPapi has no MLB access).
FanDuel two-sided fair at the same line is an INFO consensus flag only (consensus_flag / ref_books). No roster Gate 1 (team markets).
Kalshi series: KX{NFL,NBA,NHL,MLB}{GAME,SPREAD,TOTAL}; team/event matching by ET date + city/nickname token overlap; games whose two
teams cannot be told apart (same-city matchups) are skipped and listed.

Run (keys as shell env vars only, never in files):
  python3 collector/pro_game_scan.py [nfl|nba|nhl|mlb|all] [--hours 36] [--write out.json]
Caveats: tie handling (NFL) and overtime treatment (NHL/NBA/MLB) follow each venue's own rules and are assumed to match for
full-game lines; Kalshi ladders are thin (volume printed). SPREAD plays carry a pinn spec the grader does not support (manual CLV).
"""
import os, sys, re, json, time, datetime, argparse, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, KALSHI_BASE, ZONE, MAX_PIN_VIG
import consensus_flag
from ref_books import REF_BOOKS, MAX_REF_VIG
from ncaaf_game_scan import (alias, parse_iso, kalshi_events, tag_date, SPREAD_RE, oa_book_fairs, classify,
                             EDGE_LIVE, EDGE_B2, PT, ET)

OA_KEY = os.environ.get('THEODDSAPI_KEY', '')
OA = 'https://api.the-odds-api.com/v4'
LEAGUES = {
    'nfl': dict(oa='americanfootball_nfl', k='KXNFL', label='NFL'),
    'nba': dict(oa='basketball_nba', k='KXNBA', label='NBA'),
    'nhl': dict(oa='icehockey_nhl', k='KXNHL', label='NHL'),
    'mlb': dict(oa='baseball_mlb', k='KXMLB', label='MLB'),
}
MKT_KEYS = 'h2h,spreads,totals,alternate_spreads,alternate_totals'


def toks(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return [t for t in re.split(r'[^a-z0-9]+', s) if t]


def side_for(label, home, away):
    """'home' / 'away' when the Kalshi team label overlaps exactly one of the two Odds API names more than the other."""
    lt = toks(label)
    def ov(name):
        nt = toks(name)
        return sum(1 for t in lt if any(t == n or (len(t) >= 3 and n.startswith(t)) for n in nt))
    h, a = ov(home), ov(away)
    return 'home' if h > a else 'away' if a > h else None


def load_kalshi(league, max_days):
    k = LEAGUES[league]['k']
    games, today = {}, datetime.date.today()
    for role, ser in (('game', k + 'GAME'), ('spread', k + 'SPREAD'), ('total', k + 'TOTAL')):
        for e in kalshi_events(ser):
            suf = e['event_ticker'].split('-', 1)[1] if '-' in e['event_ticker'] else ''
            d = tag_date(suf)
            if not d or not (today - datetime.timedelta(days=1) <= d <= today + datetime.timedelta(days=max_days + 1)):
                continue
            g = games.setdefault(suf, dict(date=d, teams=set(), markets=dict(game=[], spread=[], total=[]), title=e.get('title')))
            for m in e.get('markets', []):
                if m.get('status') not in ('active', 'open'):
                    continue
                g['markets'][role].append(m)
                if role == 'game':
                    g['teams'].add(m.get('yes_sub_title') or '')
                elif role == 'spread':
                    mt = SPREAD_RE.match(m.get('yes_sub_title') or '')
                    if mt:
                        g['teams'].add(mt.group(1).strip())
    return games


def match_game(ev, kal):
    et_date = parse_iso(ev['commence_time']).astimezone(ET).date()
    best = []
    for suf, kg in kal.items():
        if kg['date'] != et_date:
            continue
        teams = [t for t in kg['teams'] if t]
        sides = {}
        for t in teams:
            s = side_for(t, ev['home_team'], ev['away_team'])
            if s:
                sides[t] = s
        if len(set(sides.values())) == 2:
            best.append((suf, kg, sides))
    return best[0] if len(best) == 1 else None


def oa_pin_and_refs(sport, ev):
    books = ','.join(('pinnacle',) + tuple(b for b in REF_BOOKS if b != 'pinnacle'))
    j = get(f"{OA}/sports/{sport}/events/{ev['id']}/odds?apiKey={OA_KEY}&bookmakers={books}&markets={MKT_KEYS}&oddsFormat=american")
    if not j or 'bookmakers' not in j:
        return None, {}
    pin, refs = None, {}
    for bm in j['bookmakers']:
        if bm['key'] == 'pinnacle':
            pin, _ = oa_book_fairs(bm, ev['home_team'], ev['away_team'], MAX_PIN_VIG)
        else:
            refs[bm['key']], _ = oa_book_fairs(bm, ev['home_team'], ev['away_team'], MAX_REF_VIG)
    return pin, refs


def evaluate(kg, sides, home, away, fairs, refs):
    side_of_k = lambda name: next((s for k, s in sides.items() if alias(k) == alias(name)), None)
    cands = []
    num = lambda m, k: float(m.get(k) or 0) or None

    def add(kind, mk, ksid, sel_side, line, fair_key, price, bid, label):
        f = fairs.get(fair_key) if fairs else None
        if not f or price is None:
            return
        fair = f['p'].get(sel_side)
        if fair is None:
            return
        refp = None
        for bk, rf in refs.items():
            r = rf.get(fair_key)
            if r and sel_side in r['p']:
                refp = (bk, r['p'][sel_side])
        cands.append(dict(kind=kind, ticker=mk['ticker'], ksid=ksid, sel_side=sel_side, line=line, label=label, fair=fair,
                          cost=price, edge=fair - price, bid=bid, margin=f['margin'], main=f.get('main'), status='verified',
                          fnote='Odds API live Pinnacle', ref=refp, vol=float(mk.get('volume_fp') or 0)))
    for mk in kg['markets']['game']:
        s = side_of_k(mk.get('yes_sub_title') or '')
        if not s:
            continue
        o = 'away' if s == 'home' else 'home'
        nm = lambda x: f'{home if x == "home" else away} ML'
        add('ml', mk, 'YES', s, None, ('ml', None), num(mk, 'yes_ask_dollars'), num(mk, 'yes_bid_dollars'), nm(s))
        add('ml', mk, 'NO', o, None, ('ml', None), num(mk, 'no_ask_dollars'), num(mk, 'no_bid_dollars'), nm(o))
    for mk in kg['markets']['spread']:
        mt = SPREAD_RE.match(mk.get('yes_sub_title') or '')
        s = side_of_k(mt.group(1).strip()) if mt else None
        L = mk.get('floor_strike')
        if not s or L is None:
            continue
        L = float(L)
        o = 'away' if s == 'home' else 'home'
        hl = -L if s == 'home' else L
        add('spread', mk, 'YES', s, -L, ('spread', hl), num(mk, 'yes_ask_dollars'), num(mk, 'yes_bid_dollars'),
            f'{home if s == "home" else away} {-L:+g}')
        add('spread', mk, 'NO', o, L, ('spread', hl), num(mk, 'no_ask_dollars'), num(mk, 'no_bid_dollars'),
            f'{home if o == "home" else away} {L:+g}')
    for mk in kg['markets']['total']:
        L = mk.get('floor_strike')
        if L is None:
            continue
        L = float(L)
        add('total', mk, 'YES', 'over', L, ('total', L), num(mk, 'yes_ask_dollars'), num(mk, 'yes_bid_dollars'), f'Over {L:g}')
        add('total', mk, 'NO', 'under', L, ('total', L), num(mk, 'no_ask_dollars'), num(mk, 'no_bid_dollars'), f'Under {L:g}')
    best = {}
    for c in cands:
        key = (c['kind'], c['sel_side'], c['line'])
        if key not in best or c['cost'] < best[key]['cost']:
            best[key] = c
    return list(best.values())


def scan(leagues, hours):
    if not OA_KEY:
        sys.exit('THEODDSAPI_KEY env var is required (shell only, never in files)')
    now = datetime.datetime.now(datetime.timezone.utc)
    end = now + datetime.timedelta(hours=hours)
    print(f"Pro game-line scan  {now:%Y-%m-%d %H:%M}Z  window {hours:g}h  leagues {','.join(leagues)}")
    plays, misses, skipped = [], [], []
    for lg in leagues:
        cfg = LEAGUES[lg]
        kal = load_kalshi(lg, hours / 24.0 + 1)
        evs = [e for e in (get(f"{OA}/sports/{cfg['oa']}/events?apiKey={OA_KEY}") or [])
               if now < parse_iso(e['commence_time']) <= end]
        print(f"\n## {cfg['label']}: {len(evs)} Odds API games in window, {len(kal)} Kalshi game events")
        for ev in sorted(evs, key=lambda e: e['commence_time']):
            hrs = (parse_iso(ev['commence_time']) - now).total_seconds() / 3600
            head = f"{ev['away_team']} @ {ev['home_team']}  {parse_iso(ev['commence_time']).astimezone(PT):%a %-I:%M %p} PT ({hrs:.1f}h)"
            m = match_game(ev, kal)
            if not m:
                skipped.append(f"{cfg['label']} {head}: no unique Kalshi match"); continue
            suf, kg, sides = m
            pin, refs = oa_pin_and_refs(cfg['oa'], ev)
            if not pin:
                skipped.append(f"{cfg['label']} {head}: no two-sided Pinnacle (<=8% margin)"); continue
            home, away = ev['home_team'], ev['away_team']
            mains = [f"{k[0]} {k[1] if k[1] is not None else ''}" for k, v in pin.items() if v.get('main')]
            print(f"  {head} [{suf}] Pinnacle lines {len(pin)} (main: {', '.join(mains)})")
            for c in evaluate(kg, sides, home, away, pin, refs):
                c.update(league=cfg['label'], game=f"{away} @ {home}", home=home, away=away, start=ev['commence_time'],
                         hours=hrs, suffix=suf, oa_id=ev['id'])
                cls = classify(c)
                c['cls'] = cls
                if cls:
                    plays.append(c)
                elif ZONE[0] <= c['cost'] <= ZONE[1]:
                    misses.append(c)
    plays.sort(key=lambda c: -c['edge'])
    for p in plays:
        p['consensus'] = consensus_flag.check(p['fair'], {p['ref'][0]: p['ref'][1]} if p['ref'] else None)['msg']
    print(f"\n=== {len(plays)} play(s) clear the bar (B >= {EDGE_LIVE*100:.0f} live / B2 {EDGE_B2*100:.0f}-<{EDGE_LIVE*100:.0f} paper; zone 35-75c) ===")
    for p in plays:
        tag = 'LIVE-BAR Track B' if p['cls'] == 'B' else 'B2 paper'
        bid = p['bid']
        mk_px = (f", maker@{min(bid + 0.01, p['cost'] - 0.01)*100:.0f}c edge "
                 f"{(p['fair']-min(bid+0.01,p['cost']-0.01))*100:+.1f}") if bid else ''
        print(f"[{tag}] {p['league']} {p['game']} | {p['kind']} {p['label']} (Kalshi {p['ksid']}) | cost {p['cost']*100:.0f}c fair "
              f"{p['fair']*100:.1f}c edge {p['edge']*100:+.1f}pts{mk_px} | pin margin {p['margin']*100:.1f}% "
              f"{'main' if p['main'] else 'alt'} | vol {p['vol']:.0f} | {p['hours']:.1f}h out")
        if 'gap' in p['consensus'] or 'OK' in p['consensus']:
            print(f"     {p['consensus']}" + ('' if p['main'] else '  (alt line: weak cross-check)'))
    misses.sort(key=lambda c: -c['edge'])
    print("\nClosest in-zone misses (info):")
    for p in misses[:8]:
        print(f"  {p['league']} {p['game']} | {p['kind']} {p['label']} | cost {p['cost']*100:.0f}c fair {p['fair']*100:.1f}c edge {p['edge']*100:+.1f}")
    if skipped:
        print(f"\nSkipped ({len(skipped)}):")
        for s in skipped:
            print('  ' + s)
    return plays


def to_ledger_docs(plays):
    best = {}
    for p in plays:
        direction = p['sel_side'] + ('-fav' if p['kind'] == 'spread' and p['line'] < 0 else '-dog' if p['kind'] == 'spread' else '')
        k = (p['oa_id'], p['kind'], direction)
        if k not in best or best[k]['edge'] < p['edge']:
            best[k] = p
    docs = []
    slug = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())[:6]
    for p in best.values():
        et = parse_iso(p['start']).astimezone(ET)
        ln = '' if p['line'] is None else str(p['line']).replace('.', '').replace('-', 'm').replace('+', 'p')
        selkey = slug(p['home'] if p['sel_side'] == 'home' else p['away']) if p['kind'] != 'total' else p['sel_side'][0]
        tr = p['cls']
        doc_id = f"{et.date()}-{'b2-' if tr == 'B2' else ''}{p['league'].lower()}-{slug(p['away'])}-{slug(p['home'])}-{p['kind'][:3]}-{selkey}{ln}"
        mname = {'ml': 'Moneyline', 'spread': 'Spread', 'total': 'Total'}[p['kind']]
        yes_team = p['sel_side'] if p['ksid'] == 'YES' else ('away' if p['sel_side'] == 'home' else 'home')
        if p['kind'] == 'total':
            pinn = dict(who=p['home'], mkt='total', line=p['line'], per='0')
        elif p['kind'] == 'ml':
            pinn = dict(who=p['home'], mkt='ml', per='0', team=yes_team)
        else:
            pinn = dict(who=p['home'], mkt='spread', per='0', team=yes_team, line=(p['line'] if p['ksid'] == 'YES' else -p['line']))
        data = dict(
            date=str(et.date()), game=p['game'].replace(' @ ', ' at '), player=f"{p['away']}–{p['home']}",
            market=f"{mname} {p['label']}" if p['kind'] != 'ml' else f"{mname} {p['label'].replace(' ML', '')}",
            entry=round(p['cost'] * 100, 1), fair=round(p['fair'] * 100, 1), edge=round(p['edge'] * 100, 1),
            fairSource=f"Pinnacle no-vig (The Odds API live, power de-vig, {p['margin']*100:.1f}% margin, {'main' if p['main'] else 'alt'} line) vs Kalshi {p['ksid']} ask",
            side=p['ksid'], sport=p['league'], track=tr, tier=('B2' if tr == 'B2' else None), stake=0, fee=0, orderType='taker',
            kalshi_ticker=p['ticker'], start=p['start'], pinn=pinn, close=None, result=None, zone='35–75',
            status=('Paper — B2 (2–3pt band), pending fill (kalshi)' if tr == 'B2' else 'Paper — Track B (live bar), stake 0 until Sergio places, pending fill (kalshi)'),
            note=(f"pro_game_scan.py {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%MZ}: {p['hours']:.1f}h before start. "
                  f"Kalshi {p['ksid']} ask {p['cost']*100:.0f}c vs Pinnacle fair {p['fair']*100:.1f}c = {p['edge']*100:+.1f} pts. {p['consensus']} "
                  "Same-game ladder lines are one correlated play. "
                  + ("Spread CLV close is a manual backfill. " if p['kind'] == 'spread' else '')
                  + "Re-check Pinnacle price/line before start; paper only."))
        docs.append(dict(id=doc_id, data={k: v for k, v in data.items() if v is not None}))
    return docs


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('league', nargs='?', default='all')
    ap.add_argument('--hours', type=float, default=36)
    ap.add_argument('--write')
    a = ap.parse_args()
    lg = list(LEAGUES) if a.league == 'all' else [a.league]
    pl = scan(lg, a.hours)
    if a.write:
        d = to_ledger_docs(pl)
        json.dump(d, open(a.write, 'w'), indent=1)
        print(f"\nWrote {len(d)} ledger-ready docs to {a.write}")
