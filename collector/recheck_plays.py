"""Live recheck (added 2026-10-18, Sergio: "Always double check that").
Re-prices every UNSTARTED, ungraded ledger play against LIVE Pinnacle + Novig on the SAME line and prints the current edge next to the logged edge.
Use it before telling Sergio about a play, and at the end of every scan run. Read-only (prints; --json FILE writes {id: recheck} for note updates).
Usage: python3 recheck_plays.py LEDGER_BETS_DIR [--hours 12] [--json out.json]     (needs THEODDSAPI_KEY; source .env)
Edge = Pinnacle power-devig fair of the play's side - Novig cost (probability pts). Status: HOLDS (>=2.0 or >=3.0 for live-eligible), WEAKER (still >0 but under the bar), DEAD (<=0 or line moved/not quoted)."""
import os, sys, json, glob, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, nrm, devig_power, american_to_prob
from order_watch import SPORT, MKT

KEY = os.environ.get('THEODDSAPI_KEY', '')
UTC = datetime.timezone.utc


def _t(s):
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))


def main(ledger, hours, out):
    if not KEY:
        sys.exit('THEODDSAPI_KEY required')
    now = datetime.datetime.now(UTC)
    plays = []
    for fn in glob.glob(os.path.join(ledger, '*.json')):
        d = json.load(open(fn)); d = d.get('data', d)
        if d.get('graded') or not d.get('start') or float(d.get('edge') or 0) <= 0:
            continue
        mins = (_t(d['start']) - now).total_seconds() / 60
        pn = d.get('pinn') or {}
        if 0 < mins <= hours * 60 and pn.get('who') and pn.get('line') is not None:
            d['_id'] = os.path.basename(fn)[:-5]; plays.append(d)
    cache, events, res = {}, {}, {}
    print(f'RECHECK {len(plays)} unstarted play(s) in next {hours}h at {now:%H:%M}Z')
    for p in sorted(plays, key=lambda x: x['start']):
        sport = SPORT.get(p.get('sport')); pn = p['pinn']
        mk = MKT.get(str(pn.get('mkt', '')).lower().replace('prop:', '').strip())
        if not (sport and mk):
            continue
        if sport not in events:
            events[sport] = get(f'https://api.the-odds-api.com/v4/sports/{sport}/events?apiKey={KEY}') or []
        cands = [e for e in events[sport] if abs((_t(e['commence_time']) - _t(p['start'])).total_seconds()) <= 600]
        who, line = nrm(pn['who']), float(pn['line'])
        row = None
        for e in cands:
            k = (e['id'], mk)
            if k not in cache:
                cache[k] = get(f"https://api.the-odds-api.com/v4/sports/{sport}/events/{e['id']}/odds?apiKey={KEY}&bookmakers=pinnacle,novig&markets={mk}&oddsFormat=american") or {}
            px = {}
            for b in cache[k].get('bookmakers', []):
                for m in b.get('markets', []):
                    for o in m['outcomes']:
                        if nrm(o.get('description', '')) == who and o.get('point') is not None:
                            px.setdefault(b['key'], {}).setdefault(float(o['point']), {})[o['name']] = o['price']
            if px:
                row = px; break
        side = 'Over' if p.get('side') == 'YES' else 'Under'
        pl, nv = (row or {}).get('pinnacle', {}), (row or {}).get('novig', {})
        q, n = pl.get(line, {}), nv.get(line, {})
        if 'Over' not in q or 'Under' not in q:
            r = dict(status='DEAD', why=f"Pinnacle no longer quotes {line} (has {sorted(pl)})" if pl else 'no live Pinnacle quote')
        elif side not in n:
            r = dict(status='DEAD', why=f'Novig has no {side} price at {line}')
        else:
            po, pu = american_to_prob(q['Over']), american_to_prob(q['Under'])
            fo = devig_power(po, pu); fair = fo if side == 'Over' else 1 - fo; cost = american_to_prob(n[side])
            edge = round((fair - cost) * 100, 1)
            bar = 3.0 if str(p.get('tier')) == 'B' or 'Track B' in str(p.get('label')) and 'B2' not in str(p.get('label')) else 2.0
            r = dict(edge=edge, fair=round(fair * 100, 1), cost=round(cost * 100, 1), margin=round((po + pu - 1) * 100, 1),
                     status='HOLDS' if edge >= bar else ('WEAKER' if edge > 0 else 'DEAD'))
        r['logged_edge'] = p.get('edge'); r['checked'] = now.strftime('%Y-%m-%dT%H:%MZ'); res[p['_id']] = r
        print(f"  {r['status']:6s} {p['player']} {p['market']}: logged +{p.get('edge')} -> now {r.get('edge', 'n/a')}" + (f" ({r['why']})" if 'why' in r else f" [fair {r['fair']} vs novig {r['cost']}]"))
    if out:
        json.dump(res, open(out, 'w'))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    a = sys.argv
    main(a[1], float(a[a.index('--hours') + 1]) if '--hours' in a else 12, a[a.index('--json') + 1] if '--json' in a else None)
