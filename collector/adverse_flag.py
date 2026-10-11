"""Adverse-selection INFO flag for resting limit orders on exchanges (Novig etc.). Added 2026-10-05.

INFORMATIONAL ONLY. Never rejects, filters or blocks a play, and never changes any bar in
Sergios_Betts_System.md (Track B / B2 stay at 3.0 pts). It only prints a notice.

Rule: flag when the resting limit price sits >= 5.0 cents BELOW the active market ask
(a limit that far under the ask only fills if the market moves against you). Also reports
the gap to Pinnacle de-vigged fair for context. All prices in cents (0-100).

CLI:  python3 adverse_flag.py --limit 53.0 --ask 56.5 [--fair 55.6]
Scans: call annotate_plays(plays) after scan(); pass --limit N (cents) to evaluate a resting limit
       on every exchange-venue play; without --limit it just prints the 5c flag line for each.
"""
import sys

THRESH_C = 5.0
EXCHANGES = {'novig', 'novig.us', 'prophetx'}


def flag(limit, ask, fair=None):
    """Return dict(flag: bool, gap_ask, gap_fair, msg). Never raises, never blocks."""
    gap_ask = round(ask - limit, 1)
    gap_fair = None if fair is None else round(fair - limit, 1)
    hit = gap_ask >= THRESH_C
    msg = (f"limit {limit:.1f}c vs ask {ask:.1f}c = {gap_ask:+.1f}c below ask"
           + (f"; {gap_fair:+.1f}c below Pinnacle fair {fair:.1f}c" if gap_fair is not None else ""))
    if hit:
        msg = f"[INFO adverse-selection >=5c] {msg} -- fills only if the market moves against you. Non-blocking."
    else:
        msg = f"[info] {msg} -- under the 5c line, no flag."
    return dict(flag=hit, gap_ask=gap_ask, gap_fair=gap_fair, msg=msg)


def _cents(x):
    return None if x is None else (x * 100 if x <= 1 else x)


def annotate_plays(plays, limit=None):
    """Print an informational line per exchange-venue play. Mutates nothing that gates a play;
    adds p['adv_sel'] (string) which doc builders may append to a note."""
    if limit is None and '--limit' in sys.argv:
        limit = float(sys.argv[sys.argv.index('--limit') + 1])
    shown = 0
    for p in plays:
        if str(p.get('venue', '')).lower() not in EXCHANGES:
            continue
        ask = _cents(p.get('price', p.get('price_under')))
        fair = _cents(p.get('fair', p.get('fair_under')))
        if ask is None:
            continue
        if limit is not None:
            r = flag(limit, ask, fair)
            p['adv_sel'] = r['msg']
        else:
            p['adv_sel'] = f"[info] exchange ask {ask:.1f}c: a resting limit <= {ask - THRESH_C:.1f}c would trigger the 5c adverse-selection flag (non-blocking)."
        if shown == 0:
            print("\nAdverse-selection info (non-blocking, never rejects a play):")
        shown += 1
        print(f"  {p.get('player') or p.get('game')}: {p['adv_sel']}")
    return plays


if __name__ == '__main__':
    a = sys.argv
    lim, ask = float(a[a.index('--limit') + 1]), float(a[a.index('--ask') + 1])
    fair = float(a[a.index('--fair') + 1]) if '--fair' in a else None
    print(flag(lim, ask, fair)['msg'])
