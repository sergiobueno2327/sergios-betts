"""Close snapshot (added 2026-10-14). Records LIVE Pinnacle for every logged play that is about to start, so the
nightly grader has an independent, timestamped closing line (CLV is the verdict metric; unverified closes don't count).

Usage: python3 close_snapshot.py LEDGER_BETS_DIR [--window 15] [--out collector/data/close_snapshots.jsonl]
  LEDGER_BETS_DIR = folder of exported bets/*.json docs.
Takes every doc (real, paper, B2, any stake) not yet graded whose start is within --window minutes (default 15, and not
more than 2 min past). For each it stores the Pinnacle two-sided price at the play's own line AND the full ladder
(every line Pinnacle quotes for that player/market), so a moved closing line can still be analysed later.
Record: id, snap_ts, start, mins_to_start, line, found, over, under (american), margin, fair_over, fair_side, age_s,
ladder [{line, over, under}], note. One record per play per run; the grader uses the newest one that is <= 15 min
before start and whose quote is <= 10 min old.
Credits: ~1 Odds API credit per player-market per event (cached per event+market)."""
import os, sys, json, glob, datetime, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_pin_verify import _nrm, _prob, MAX_VIG, MAX_AGE_S
from nfl_scan import devig_power
from order_watch import SPORT, MKT, _get, KEY

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'close_snapshots.jsonl')
UTC = datetime.timezone.utc


def _t(s):
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))


def load_plays(ledger, window):
    now = datetime.datetime.now(UTC)
    out = []
    for fn in glob.glob(os.path.join(ledger, '*.json')):
        d = json.load(open(fn))
        d = d.get('data', d)
        if d.get('graded') or not d.get('start'):
            continue
        mins = (_t(d['start']) - now).total_seconds() / 60
        if -2 <= mins <= window:
            d['_id'] = os.path.basename(fn)[:-5]
            d['_mins'] = mins
            out.append(d)
    return out


def fetch_ladder(sport, eid, mk, who):
    """Pinnacle Over/Under for one player+market: {line: (over, under)} plus quote age seconds."""
    r = _get(f"https://api.the-odds-api.com/v4/sports/{sport}/events/{eid}/odds?apiKey={KEY}&regions=us,eu"
             f"&bookmakers=pinnacle,novig&markets={mk}&oddsFormat=american")
    if not r:
        return None, None
    lad, age, nov = {}, None, {}
    now = datetime.datetime.now(UTC)
    for b in r.get('bookmakers', []):
        if b['key'] == 'novig':  # Novig close price on the same line (added 2026-10-23 to separate 'Pinnacle moved' vs 'Novig caught up')
            for m in b.get('markets', []):
                for x in m['outcomes']:
                    if _nrm(x['description']) == who and x.get('point') is not None:
                        nov.setdefault(x['point'], {})[x['name']] = x['price']
            continue
        if b['key'] != 'pinnacle':
            continue
        for m in b.get('markets', []):
            lu = m.get('last_update') or b.get('last_update')
            if lu:
                age = (now - _t(lu)).total_seconds()
            for x in m['outcomes']:
                if _nrm(x['description']) == who and x.get('point') is not None:
                    lad.setdefault(x['point'], {})[x['name']] = x['price']
    lad = lad or {}
    lad['__novig__'] = nov
    return lad, age


def snapshot(p, events, cache):
    base = dict(id=p['_id'], snap_ts=datetime.datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'), start=p['start'],
                mins_to_start=round(p['_mins'], 1), sport=p.get('sport'), player=p.get('player'), side=p.get('side'))
    sport = SPORT.get(p.get('sport'))
    pn = p.get('pinn') or {}
    mk = MKT.get(str(pn.get('mkt', '')).lower().replace('prop:', '').strip())
    if not (sport and mk and pn.get('who') and pn.get('line') is not None):
        return dict(base, found=False, note='unsupported sport/market for auto snapshot')
    if sport not in events:
        events[sport] = _get(f'https://api.the-odds-api.com/v4/sports/{sport}/events?apiKey={KEY}') or []
    st = _t(p['start'])
    cands = [e for e in events[sport] if abs((_t(e['commence_time']) - st).total_seconds()) <= 600]
    who, line = _nrm(pn['who']), pn['line']
    for e in cands:
        key = (e['id'], mk, who)
        if key not in cache:
            cache[key] = fetch_ladder(sport, e['id'], mk, who)
        lad, age = cache[key]
        if not lad:
            continue
        lad = dict(lad); nov = lad.pop('__novig__', {})
        if not lad:
            continue
        ladder = [dict(line=l, over=v.get('Over'), under=v.get('Under')) for l, v in sorted(lad.items())]
        rec = dict(base, line=line, age_s=None if age is None else round(age), ladder=ladder)
        nq = nov.get(line, {})
        _ns = 'Over' if p.get('side') == 'YES' else 'Under'
        if _ns in nq:
            rec.update(novig_close_price=nq[_ns], novig_close_cost=round(_prob(nq[_ns]) * 100, 2))
        q = lad.get(line, {})
        if 'Over' in q and 'Under' in q:
            po, pu = _prob(q['Over']), _prob(q['Under'])
            rec.update(over=q['Over'], under=q['Under'], margin=round((po + pu - 1) * 100, 2))
            fo = devig_power(po, pu)
            rec.update(fair_over=round(fo * 100, 2), fair_side=round((fo if p.get('side') == 'YES' else 1 - fo) * 100, 2))
            if age is not None and age > MAX_AGE_S:
                return dict(rec, found=False, note=f'quote stale ({age/60:.0f} min old)')
            if po + pu - 1 > MAX_VIG:
                return dict(rec, found=False, note='margin > 8%')
            return dict(rec, found=True, note='same line, two-sided')
        return dict(rec, found=False, note=f'play line {line} not quoted at close (ladder has {sorted(lad)})')
    return dict(base, found=False, note='no Pinnacle quote for this player/market')


def main(ledger, window, out):
    plays = load_plays(ledger, window)
    print(f'CLOSE SNAPSHOT: {len(plays)} play(s) starting within {window} min')
    events, cache, n_ok = {}, {}, 0
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'a') as fh:
        for p in plays:
            rec = snapshot(p, events, cache)
            fh.write(json.dumps(rec) + '\n')
            n_ok += bool(rec.get('found'))
            print(f"  {'OK ' if rec.get('found') else 'MISS'} {p['_id']}: fair_side={rec.get('fair_side')} {rec.get('note')}")
    print(f'  {n_ok}/{len(plays)} verified snapshots written to {out}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    w, o = 15, DEFAULT_OUT
    a = sys.argv
    if '--window' in a:
        w = float(a[a.index('--window') + 1])
    if '--out' in a:
        o = a[a.index('--out') + 1]
    main(a[1], w, o)
