"""Live Pinnacle verification gate (added 2026-10-11 12:40 PM PT).
Every scan hit is re-checked against LIVE Pinnacle via The Odds API before it can print/log:
same player, SAME line, both sides posted, margin <= 8%. Edge is recomputed from live prices;
< 2.0 pts -> dropped, 2.0-<3.0 -> 'B2 paper', >= 3.0 stays live. Cost ~1 credit per event per market.
Usage in a scan's __main__:  plays = live_pin_verify.verify(plays, 'americanfootball_nfl', market_of)
market_of(play) -> Odds API market key (e.g. 'player_reception_yds'). Never rejects on API error
silently: if Pinnacle can't be reached the play is DROPPED (fail closed)."""
import os, json, datetime, unicodedata, re, urllib.request

KEY = os.environ.get('THEODDSAPI_KEY', '')
MAX_VIG = 0.08

def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.load(r)
    except Exception as e:
        print('  live_pin_verify API error:', e)
        return None

def _nrm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z ]', '', s).replace(' jr', '').replace(' sr', '').replace(' iii', '').replace(' ii', '').strip()

def _prob(am):
    return 100 / (am + 100) if am > 0 else -am / (-am + 100)

def _devig(po, pu):
    from nfl_scan import devig_power
    return devig_power(po, pu)

def verify(plays, sport, market_of):
    if not KEY or not plays:
        return plays if plays else []
    base = f'https://api.the-odds-api.com/v4/sports/{sport}'
    events = _get(f'{base}/events?apiKey={KEY}') or []
    cache = {}
    def live(play):
        mk = market_of(play)
        st = datetime.datetime.fromisoformat(play['start'].replace('Z', '+00:00'))
        out = {}
        for e in events:
            et = datetime.datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00'))
            if abs((et - st).total_seconds()) > 600:
                continue
            ck = (e['id'], mk)
            if ck not in cache:
                cache[ck] = _get(f"{base}/events/{e['id']}/odds?apiKey={KEY}&regions=eu&bookmakers=pinnacle&markets={mk}&oddsFormat=american")
            r = cache[ck] or {}
            for b in r.get('bookmakers', []):
                for m in b.get('markets', []):
                    for o in m['outcomes']:
                        out[(_nrm(o['description']), o.get('point'), o['name'])] = o['price']
        return out
    kept = []
    for p in plays:
        if p.get('venue') == 'prizepicks' and not p.get('start'):
            kept.append(p); continue
        lv = live(p)
        side = p.get('side', 'Under')
        nm = _nrm(p['player'])
        ov, un = lv.get((nm, p['line'], 'Over')), lv.get((nm, p['line'], 'Under'))
        if ov is None or un is None:
            print(f"  LIVE-PIN DROP: {p['player']} {side} {p['line']} -- Pinnacle not posted / line moved")
            continue
        po, pu = _prob(ov), _prob(un)
        if po + pu - 1 > MAX_VIG:
            print(f"  LIVE-PIN DROP: {p['player']} margin {(po+pu-1)*100:.1f}% > 8%")
            continue
        fo = _devig(po, pu)
        fair = (fo if side == 'Over' else 1 - fo) * 100
        pk = 'price' if 'price' in p else 'price_under'
        fk = 'fair' if 'fair' in p else 'fair_under'
        edge = round(fair - p[pk], 1)
        try:
            import make_order; _floor = make_order.MAKE_FLOOR * 100
        except Exception:
            _floor = 2.0
        if edge < _floor - 1e-9:
            print(f"  LIVE-PIN DROP: {p['player']} {side} {p['line']} live edge {edge} < {_floor}")
            continue
        p[fk] = round(fair, 1); p['edge'] = edge; p['pin_vig'] = round(po + pu - 1, 4); p['live_verified'] = True
        if edge < 2.0:
            p['label'] = 'MAKE candidate'
        elif edge < 3.0:
            p['label'] = 'B2 paper' if p.get('label') in (None, 'MAKE candidate') else p['label']
        elif p.get('label') in ('B2 paper', 'MAKE candidate'):
            p['label'] = None
        kept.append(p)
    kept.sort(key=lambda p: -p['edge'])
    print(f"\nLIVE-PIN verified: {len(kept)}/{len(plays)} plays survive the live Pinnacle check")
    for p in kept:
        print('  VERIFIED', p['player'], p.get('side', 'Under'), p['line'], p['venue'], 'edge', p['edge'], p.get('label') or 'LIVE')
    return kept
