"""Order watch (added 2026-10-14). Checks Sergio's OPEN real resting orders against LIVE Pinnacle.
Alert-only: it never places or cancels anything. Math is all in probabilities (cents), never American-odds differences.

Usage: python3 order_watch.py LEDGER_DIR      (LEDGER_DIR = folder of exported bets/*.json docs)
Open order = stake > 0, not graded, status starts with 'OPEN' or 'PARTIAL', game not started.
For each: fresh Pinnacle two-sided fair for OUR side (power de-vig, same line, margin <= 8%, quote <= 10 min old),
  edge = fair - resting price (pts); ceiling = fair - 3.0.
  edge >= 3.0  -> HOLD
  edge <  3.0  -> LOWER/CANCEL (price is above the ceiling; instruction gives the new ceiling)
  no verified live quote -> CHECK MANUALLY (never silently passes).
Also prints the Novig ask so you can see how far your bid is from filling, and minutes to start."""
import os, sys, json, glob, datetime, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_pin_verify import _nrm, _prob, MAX_VIG, MAX_AGE_S
from nfl_scan import devig_power

KEY = os.environ.get('THEODDSAPI_KEY', '')
MIN_EDGE = 3.0
SPORT = {'NHL': 'icehockey_nhl', 'NFL': 'americanfootball_nfl', 'WNBA': 'basketball_wnba', 'NBA': 'basketball_nba', 'MLB': 'baseball_mlb'}
MKT = {  # ledger pinn.mkt label (lowercased, 'prop:' stripped) -> Odds API market key
    'shots on goal': 'player_shots_on_goal', 'sog': 'player_shots_on_goal', 'assists': 'player_assists', 'points': 'player_points',
    'goals': 'player_goals', 'rebounds': 'player_rebounds', 'threes': 'player_threes',
    'receptions': 'player_receptions', 'receiving yards': 'player_reception_yds', 'rushing yards': 'player_rush_yds',
    'rushing attempts': 'player_rush_attempts', 'passing yards': 'player_pass_yds', 'pass attempts': 'player_pass_attempts',
    'passing attempts': 'player_pass_attempts', 'pass completions': 'player_pass_completions',
    'passing completions': 'player_pass_completions', 'touchdown passes': 'player_pass_tds', 'interceptions': 'player_pass_interceptions',
}


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.load(r)
    except Exception as e:
        print('  API error:', e)
        return None


def load_open(ledger):
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for fn in glob.glob(os.path.join(ledger, '*.json')):
        d = json.load(open(fn))
        d = d.get('data', d)
        st = str(d.get('status', '')).upper()
        if float(d.get('stake') or 0) <= 0 or d.get('graded') or not (st.startswith('OPEN') or st.startswith('PARTIAL')):
            continue
        start = d.get('start')
        if not start or datetime.datetime.fromisoformat(start.replace('Z', '+00:00')) <= now:
            continue
        d['_id'] = os.path.basename(fn)[:-5]
        out.append(d)
    return out


def check(o, events_cache):
    sport = SPORT.get(o.get('sport'))
    pn = o.get('pinn') or {}
    mk = MKT.get(str(pn.get('mkt', '')).lower().replace('prop:', '').strip())
    if not (sport and mk and pn.get('who') and pn.get('line') is not None):
        return None, 'unsupported market/sport for auto-check'
    st = datetime.datetime.fromisoformat(o['start'].replace('Z', '+00:00'))
    if sport not in events_cache:
        events_cache[sport] = _get(f'https://api.the-odds-api.com/v4/sports/{sport}/events?apiKey={KEY}') or []
    ev = [e for e in events_cache[sport]
          if abs((datetime.datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00')) - st).total_seconds()) <= 600]
    nm, line = _nrm(pn['who']), pn['line']
    for e in ev:
        r = _get(f"https://api.the-odds-api.com/v4/sports/{sport}/events/{e['id']}/odds?apiKey={KEY}&regions=us,eu&bookmakers=pinnacle,novig&markets={mk}&oddsFormat=american")
        if not r:
            continue
        q = {}
        for b in r.get('bookmakers', []):
            for m in b.get('markets', []):
                lu = m.get('last_update') or b.get('last_update')
                age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(lu.replace('Z', '+00:00'))).total_seconds() if lu else None
                for x in m['outcomes']:
                    if _nrm(x['description']) == nm and x.get('point') == line:
                        q[(b['key'], x['name'])] = (x['price'], age)
        if ('pinnacle', 'Over') not in q or ('pinnacle', 'Under') not in q:
            continue
        (ov, a1), (un, a2) = q[('pinnacle', 'Over')], q[('pinnacle', 'Under')]
        if (a1 or 0) > MAX_AGE_S or (a2 or 0) > MAX_AGE_S:
            return None, f'Pinnacle quote stale ({max(a1 or 0, a2 or 0)/60:.0f} min old)'
        po, pu = _prob(ov), _prob(un)
        if po + pu - 1 > MAX_VIG:
            return None, f'Pinnacle margin {(po+pu-1)*100:.1f}% > 8%'
        fo = devig_power(po, pu)
        side_over = o.get('side') == 'YES'
        fair = (fo if side_over else 1 - fo) * 100
        nv = q.get(('novig', 'Over' if side_over else 'Under'))
        ask = _prob(nv[0]) * 100 if nv else None
        return dict(fair=fair, ask=ask, margin=(po + pu - 1) * 100), None
    return None, 'Pinnacle two-sided line not found at the same line'


def main(ledger):
    orders = load_open(ledger)
    print(f'ORDER WATCH: {len(orders)} open real order(s)')
    cache, now = {}, datetime.datetime.now(datetime.timezone.utc)
    for o in orders:
        res, why = check(o, cache)
        mins = (datetime.datetime.fromisoformat(o['start'].replace('Z', '+00:00')) - now).total_seconds() / 60
        head = f"{o['player']} {o['market']} | resting {o['entry']}c ${o['stake']} | starts in {mins/60:.1f}h"
        if not res:
            print(f"  CHECK MANUALLY: {head} -- {why}")
            continue
        edge = res['fair'] - float(o['entry'])
        ceil = res['fair'] - MIN_EDGE
        gap = f", Novig ask {res['ask']:.1f}c ({res['ask']-float(o['entry']):.1f}c above your bid)" if res['ask'] else ''
        if edge >= MIN_EDGE:
            print(f"  HOLD: {head} | fair {res['fair']:.1f}% edge +{edge:.1f} ceiling {ceil:.1f}c{gap}")
        else:
            print(f"  LOWER/CANCEL: {head} | fair {res['fair']:.1f}% edge {edge:+.1f} < 3.0 -- new ceiling {ceil:.1f}c; "
                  f"lower the bid to {ceil:.1f}c or below, or cancel{gap}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1])
