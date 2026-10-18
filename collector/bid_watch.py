"""Bid watch (added 2026-10-18, Sergio: "How do we catch these type of fills before the market moves").
Polls LIVE Pinnacle + Novig for specific plays every N seconds until start and prints ONE line per state change (made for Monitor/phone alerts).
Alert-only: never places or cancels anything.
Per watch: fair = Pinnacle power-devig of our side (same line, margin <= 8%); ceiling = fair - 3.0 (cents); ask = Novig cost of our side.
  BID_LIVE   ask <= ceiling      -> Novig is offering the +3.0 edge NOW (check it, then act yourself)
  NEAR       ask within 1.0c above ceiling
  CEILING    ceiling moved >= 0.5c since last print (Pinnacle moved: lower or cancel your resting bid if it is above the new ceiling)
  DEAD       Pinnacle stopped quoting the line / margin > 8%
Usage: python3 bid_watch.py SPEC [SPEC ...] [--every 120]
  SPEC = 'Player|market|line|Over/Under|SPORT|startISO'   e.g. 'Jalen Brooks|receptions|1.5|Over|NFL|2026-10-18T20:05:00Z'
Needs THEODDSAPI_KEY (source .env). 1 API call per watch per poll."""
import os, sys, time, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, nrm, devig_power, american_to_prob
from order_watch import SPORT, MKT

KEY = os.environ.get('THEODDSAPI_KEY', '')
UTC = datetime.timezone.utc
t = lambda s: datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))


def setup(spec):
    who, mkt, line, side, sport, start = spec.split('|')
    sp, mk = SPORT[sport], MKT[mkt.lower()]
    evs = get(f'https://api.the-odds-api.com/v4/sports/{sp}/events?apiKey={KEY}') or []
    ev = [e for e in evs if abs((t(e['commence_time']) - t(start)).total_seconds()) <= 600]
    return dict(who=nrm(who), name=who, mk=mk, sp=sp, line=float(line), side=side, start=t(start), evs=ev, ev=None, last=None, lastceil=None)


def poll(w):
    for e in (w['evs'] if not w['ev'] else [w['ev']]):
        d = get(f"https://api.the-odds-api.com/v4/sports/{w['sp']}/events/{e['id']}/odds?apiKey={KEY}&bookmakers=pinnacle,novig&markets={w['mk']}&oddsFormat=american") or {}
        px = {}
        for b in d.get('bookmakers', []):
            for m in b.get('markets', []):
                for o in m['outcomes']:
                    if nrm(o.get('description', '')) == w['who'] and o.get('point') is not None and float(o['point']) == w['line']:
                        px.setdefault(b['key'], {})[o['name']] = o['price']
        if px:
            w['ev'] = e
            return px
    return {}


def main():
    a = sys.argv[1:]
    every = int(a[a.index('--every') + 1]) if '--every' in a else 120
    specs = [x for i, x in enumerate(a) if '|' in x]
    if not KEY or not specs:
        sys.exit(__doc__)
    ws = [setup(s) for s in specs]
    print(f'BID WATCH start: {len(ws)} play(s), every {every}s', flush=True)
    while ws:
        now = datetime.datetime.now(UTC)
        for w in list(ws):
            mins = (w['start'] - now).total_seconds() / 60
            if mins <= 0:
                print(f"{w['name']}: started, watch ends", flush=True); ws.remove(w); continue
            px = poll(w); q, n = px.get('pinnacle', {}), px.get('novig', {})
            if 'Over' not in q or 'Under' not in q:
                st, msg = 'DEAD', 'Pinnacle not quoting this line'
            else:
                po, pu = american_to_prob(q['Over']), american_to_prob(q['Under'])
                if po + pu - 1 > 0.08:
                    st, msg = 'DEAD', f'Pinnacle margin {100*(po+pu-1):.1f}% > 8%'
                else:
                    fo = devig_power(po, pu); fair = fo if w['side'] == 'Over' else 1 - fo
                    ceil = round((fair - 0.03) * 100, 1)
                    ask = round(american_to_prob(n[w['side']]) * 100, 1) if w['side'] in n else None
                    if ask is None:
                        st, msg = 'NOASK', f'no Novig {w["side"]} price; ceiling {ceil}c'
                    elif ask <= ceil:
                        st, msg = 'BID_LIVE', f'Novig ask {ask}c <= ceiling {ceil}c (edge +{fair*100-ask:.1f}). Check it'
                    elif ask <= ceil + 1.0:
                        st, msg = 'NEAR', f'ask {ask}c, ceiling {ceil}c ({ask-ceil:.1f}c away)'
                    else:
                        st, msg = 'FAR', f'ask {ask}c, ceiling {ceil}c'
                    if w['lastceil'] is not None and abs(ceil - w['lastceil']) >= 0.5 and st in ('FAR', 'NEAR', 'BID_LIVE'):
                        st = 'CEILING' if st == 'FAR' else st
                        msg += f" | ceiling moved {w['lastceil']}->{ceil}"
                    w['lastceil'] = ceil if w['lastceil'] is None or abs(ceil - w['lastceil']) >= 0.5 else w['lastceil']
            if st != w['last'] and st != 'FAR' or (st == 'FAR' and w['last'] is None):
                print(f"[{now.astimezone(datetime.timezone(datetime.timedelta(hours=-7))):%H:%M}] {st} {w['name']} {w['side']} {w['line']} ({mins:.0f}m to start): {msg}", flush=True)
            w['last'] = st
        time.sleep(every)


if __name__ == '__main__':
    main()
