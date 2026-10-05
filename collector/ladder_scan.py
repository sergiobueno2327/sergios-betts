"""Sergio's Betts -- Track B3: line-shift ("mispriced line") scan. PAPER ONLY (added 2026-10-04).

Problem it solves: nfl_scan/wnba_scan only compare a venue to Pinnacle at the SAME line. A venue posting a
different number (e.g. Novig Over 6.5 when Pinnacle's main is 7.5) gets skipped. This scan shifts Pinnacle's
no-vig fair price to the venue's line using the market's own ladder, then applies the usual bar.

Method:
  1. Pinnacle main line L_p, two-sided, power de-vig -> fair Over f.
  2. For a venue price at line L' != L_p (within MAX_SHIFT), estimate delta = P_over(L') - P_over(L_p) as the
     MEDIAN across OTHER books (exclude Pinnacle, the venue under test, PrizePicks) that post both lines
     (raw Over implied probs; vig is ~equal at both lines so it largely cancels). Need >= MIN_REF books.
     (Using the venue's own ladder would be circular: it just reproduces the same-line edge.)
  3. Est. fair Over at L' = f + delta; Under = 1 - that. Edge = est. fair - venue price.
  4. Bar: edge >= 3.0 pts, zone 35-75c, pregame only. Logged track "B3", stake 0. A shifted-line estimate carries
     more error than a same-line check, so B3 stays paper until reviewed (50 graded plays: promote only if
     avg CLV vs Pinnacle >= +1 and >= 55% beat the close; note Pinnacle CLV needs Pinnacle at that line,
     so grade B3 vs Kalshi/venue close + the shifted Pinnacle close).

Usage: THEODDSAPI_KEY=... python3 collector/ladder_scan.py [nfl|wnba] [--min-edge 0.03] [--write out.json]
"""
import os, sys, json, time, datetime, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, devig_power, american_to_prob, ZONE

KEY = os.environ.get('THEODDSAPI_KEY', '')
OA = 'https://api.the-odds-api.com/v4'
from nfl_scan import ODDSAPI_VENUES
VENUES = ('kalshi',) + tuple(ODDSAPI_VENUES)
REF_EXCLUDE = {'pinnacle', 'prizepicks', 'underdog', 'betr_us_dfs'}
MIN_REF = 3
MAX_PIN_VIG = 0.08  # market-width filter
CFG = {
    'nfl': dict(sport='americanfootball_nfl', label='NFL', markets={
        'player_receptions': 1.5, 'player_reception_yds': 8.0, 'player_rush_yds': 8.0, 'player_pass_yds': 15.0,
        'player_pass_tds': 1.0, 'player_pass_attempts': 3.0, 'player_pass_completions': 3.0,
        'player_rush_attempts': 2.0, 'player_pass_interceptions': 1.0}),
    'wnba': dict(sport='basketball_wnba', label='WNBA', markets={
        'player_points': 3.0, 'player_rebounds': 2.0, 'player_assists': 1.5}),
}


def scan(which='nfl', edge_min=0.03, days=3):
    if not KEY:
        sys.exit('THEODDSAPI_KEY env var is required')
    cfg = CFG[which]
    now = datetime.datetime.now(datetime.timezone.utc)
    evs = [e for e in get(f"{OA}/sports/{cfg['sport']}/events?apiKey={KEY}") or []
           if now < datetime.datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00')) < now + datetime.timedelta(days=days)]
    print(f"{cfg['label']}: {len(evs)} upcoming events (next {days}d)")
    base = {m: m for m in cfg['markets']}
    mk = ','.join(list(base) + [m + '_alternate' for m in base])
    plays = []
    for ev in evs:
        d = get(f"{OA}/sports/{cfg['sport']}/events/{ev['id']}/odds?apiKey={KEY}&regions=us,us2,us_ex,eu&markets={mk}&oddsFormat=american")
        if not d:
            continue
        # ladder[(stat, player)][book][line] = {'Over': p, 'Under': p}
        ladder = {}
        for b in d.get('bookmakers', []):
            for m in b['markets']:
                stat = m['key'].replace('_alternate', '')
                for o in m['outcomes']:
                    if o.get('point') is None or not o.get('description'):
                        continue
                    ladder.setdefault((stat, o['description']), {}).setdefault(b['key'], {}).setdefault(float(o['point']), {})[o['name']] = american_to_prob(o['price'])
        for (stat, player), books in ladder.items():
            pin = books.get('pinnacle')
            if not pin:
                continue
            mains = [(L, s) for L, s in pin.items() if 'Over' in s and 'Under' in s]
            if not mains:
                continue
            Lp, sp = mains[0]
            if sp['Over'] + sp['Under'] - 1 > MAX_PIN_VIG:
                continue  # WIDE MARKET
            f_over = devig_power(sp['Over'], sp['Under'])
            max_shift = cfg['markets'][stat]
            for vk in VENUES:
                for L2, sv in books.get(vk, {}).items():
                    if abs(L2 - Lp) < 0.01 or abs(L2 - Lp) > max_shift:
                        continue
                    deltas = []
                    for bk, lad in books.items():
                        if bk == vk or bk in REF_EXCLUDE:
                            continue
                        if Lp in lad and L2 in lad and 'Over' in lad[Lp] and 'Over' in lad[L2]:
                            deltas.append(lad[L2]['Over'] - lad[Lp]['Over'])
                    if len(deltas) < MIN_REF:
                        continue
                    delta = statistics.median(deltas)
                    est_over = min(max(f_over + delta, 0.01), 0.99)
                    for side, p_est, price in (('Over', est_over, sv.get('Over')), ('Under', 1 - est_over, sv.get('Under'))):
                        if price is None:
                            continue
                        edge = p_est - price
                        if edge >= edge_min and ZONE[0] <= price <= ZONE[1]:
                            plays.append(dict(game=f"{ev['away_team']} @ {ev['home_team']}", start=ev['commence_time'], stat=stat,
                                              player=player, venue=vk, side=side, line=L2, pin_line=Lp,
                                              est_fair=round(p_est * 100, 1), price=round(price * 100, 1), edge=round(edge * 100, 1),
                                              pin_fair_at_pin_line=round(f_over * 100, 1), ref_books=len(deltas),
                                              ref_spread=round((max(deltas) - min(deltas)) * 100, 1), sport=cfg['label']))
        time.sleep(0.3)
    plays.sort(key=lambda p: -p['edge'])
    print(f"\n{len(plays)} B3 (line-shift, PAPER) candidates >= {edge_min*100:.0f}pts, zone 35-75c:")
    for p in plays:
        print(p)
    return plays


def to_ledger_docs(plays):
    docs = {}
    for p in plays:
        et = datetime.datetime.fromisoformat(p['start'].replace('Z', '+00:00')).astimezone(datetime.timezone(datetime.timedelta(hours=-4)))
        did = f"{et.date().isoformat()}-b3-{p['sport'].lower()}-{p['player'].split()[-1].lower()}-{p['stat'].replace('player_', '')}{str(p['line']).replace('.', '')}-{p['side'][0].lower()}"
        cur = docs.get(did)
        if cur and cur['data']['edge'] >= p['edge']:
            continue
        docs[did] = dict(id=did, data=dict(
            date=et.date().isoformat(), game=p['game'], player=p['player'], market=f"{p['stat'].replace('player_', '')} {p['side']} {p['line']}",
            entry=p['price'], fair=p['est_fair'], fairSource=f"Pinnacle {p['pin_line']} shifted to {p['line']} via {p['ref_books']}-book ladder (ladder_scan.py) vs {p['venue']}",
            edge=p['edge'], side='YES' if p['side'] == 'Over' else 'NO', sport=p['sport'], track='B3', stake=0, fee=0, orderType='taker',
            start=p['start'], status=f"Paper — Track B3 line-shift, pending fill ({p['venue']})",
            note=f"B3 line-shift scan. Pinnacle main line {p['pin_line']} (fair Over {p['pin_fair_at_pin_line']}%) shifted to {p['line']}; ref spread {p['ref_spread']}pts across {p['ref_books']} books. Gate 1 roster check NOT yet done.",
            close=None, result=None, zone='35–75'))
    return list(docs.values())


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in CFG else 'nfl'
    em = float(sys.argv[sys.argv.index('--min-edge') + 1]) if '--min-edge' in sys.argv else 0.03
    pl = scan(which, em)
    if '--write' in sys.argv:
        out = sys.argv[sys.argv.index('--write') + 1]
        json.dump(to_ledger_docs(pl), open(out, 'w'), indent=1)
        print(f"Wrote ledger docs to {out}")
