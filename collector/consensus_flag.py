"""Second-source consensus INFO flag + no-Pinnacle paper tracking. Added 2026-10-05.

Pinnacle stays the ONLY anchor: it sets fair and the bar (Track B / B2 unchanged). A second de-vigged
source (Kalshi two-sided, another sharp book) is used only as a cross-check:
  1. Pinnacle fair and each alt fair within 1.5 pts  -> "[consensus OK]"
  2. disagree by more than 1.5 pts                  -> "[INFO consensus gap]" (non-blocking, never rejects)
  3. NO Pinnacle price (walks/outs/CFB etc.)        -> never live, stake 0; if >=2 alt sources agree within
     1.5 pts and the edge vs cost is >= 3.0 pts, log to data/consensus_paper.jsonl as a PAPER candidate
     (own track 'C-paper'; review after 50 graded: avg CLV >= +1 and >=55% beat close else drop).
CLI: python3 consensus_flag.py --pin 55.4 --alt kalshi:54.0 [--alt novig:55.0]
     python3 consensus_flag.py --nopin --cost 53.5 --alt kalshi:58.0 --alt novig:57.2 --label "Player stat O x"
"""
import sys, os, json, datetime

TOL = 1.5          # pts
PAPER_EDGE = 3.0   # pts vs cost, no-Pinnacle paper candidates
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'consensus_paper.jsonl')


def _c(x):
    return None if x is None else (x * 100 if x <= 1 else x)


def check(pin_fair=None, alts=None):
    """alts: {name: fair_pct}. Returns dict(status, msg). Never raises, never blocks."""
    alts = {k: _c(v) for k, v in (alts or {}).items() if v is not None}
    if pin_fair is None:
        return dict(status='no_pin', msg='[INFO no Pinnacle] not live-eligible; paper-only if alts agree.')
    pin_fair = _c(pin_fair)
    if not alts:
        return dict(status='none', msg='[info] no second source available; Pinnacle only.')
    gaps = {k: round(v - pin_fair, 1) for k, v in alts.items()}
    worst = max(abs(g) for g in gaps.values())
    txt = ', '.join(f"{k} {alts[k]:.1f}% ({gaps[k]:+.1f})" for k in alts)
    if worst > TOL:
        return dict(status='gap', msg=f"[INFO consensus gap] Pinnacle {pin_fair:.1f}% vs {txt} -- >{TOL} pts apart. Non-blocking; Pinnacle still decides.")
    return dict(status='ok', msg=f"[consensus OK] Pinnacle {pin_fair:.1f}% vs {txt}.")


def nopin_candidate(cost, alts, label=''):
    """Return dict if >=2 alts agree within TOL and mean alt fair - cost >= PAPER_EDGE, else None."""
    vals = [_c(v) for v in (alts or {}).values() if v is not None]
    if len(vals) < 2 or max(vals) - min(vals) > TOL:
        return None
    fair = sum(vals) / len(vals)
    edge = round(fair - _c(cost), 1)
    if edge < PAPER_EDGE:
        return None
    return dict(label=label, fair=round(fair, 1), cost=_c(cost), edge=edge, alts=alts, track='C-paper', stake=0,
                ts=datetime.datetime.utcnow().isoformat() + 'Z')


def log_paper(rec):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a') as fh:
        fh.write(json.dumps(rec) + '\n')


def annotate_plays(plays):
    """Prints a consensus line for plays carrying p['alt_fairs'] ({name: fair%}); sets p['consensus']."""
    n = 0
    for p in plays:
        r = check(p.get('fair'), p.get('alt_fairs'))
        p['consensus'] = r['msg']
        if r['status'] in ('ok', 'gap'):
            if n == 0:
                print('\nConsensus cross-check (info only, never rejects a play):')
            n += 1
            print(f"  {p.get('player') or p.get('game')}: {r['msg']}")
    return plays


if __name__ == '__main__':
    a = sys.argv
    alts = {}
    for i, t in enumerate(a):
        if t == '--alt':
            k, v = a[i + 1].split(':'); alts[k] = float(v)
    if '--nopin' in a:
        cost = float(a[a.index('--cost') + 1])
        lab = a[a.index('--label') + 1] if '--label' in a else ''
        rec = nopin_candidate(cost, alts, lab)
        print(rec if rec else 'No paper candidate (need >=2 alts within 1.5 pts and >=3.0 pts over cost).')
        if rec and '--log' in a:
            log_paper(rec); print('logged to', LOG)
    else:
        print(check(float(a[a.index('--pin') + 1]), alts)['msg'])
