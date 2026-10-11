"""Make-order helper (added 2026-10-11, Sergio): for plays whose edge sits just under the 3.0 bar (B2, 2.0-<3.0), compute the
resting-limit (Make) price that WOULD clear 3.0 vs Pinnacle fair. Pinnacle stays the anchor; this never changes fair/edge/tier.
  ceiling = fair - 3.0          worst price that still clears the live bar (rulebook: fill ceiling, never go past it)
  target  = ceiling - 1.0       where to rest the order (1c cushion)
  gap     = cost - target       how many cents below the current ask the target sits
Fill-odds read: gap <= 3c plausible; 3-5c low; >= 5c very low (adverse_flag threshold). Only exchange venues (novig, kalshi)
take resting orders; DFS/other venues get nothing. A filled Make order is a live play ONLY if Sergio places it; until then the
play stays paper (B2) and the Make price is information. Logged on the ledger doc as makeCeiling / makeTarget / makeGap.
"""
EX = ('novig', 'kalshi', 'prophetx')


def compute(p):
    if p.get('venue') not in EX:
        return None
    cost = p.get('price', p.get('price_under'))
    fair = p.get('fair', p.get('fair_under'))
    if cost is None or fair is None:
        return None
    ceiling = round(fair - 3.0, 1)
    if ceiling < 35.0:   # below the 35c zone floor: no valid Make price
        return None
    target = round(max(min(ceiling - 1.0, cost), 35.0), 1)
    gap = round(cost - target, 1)
    odds = 'plausible' if gap <= 3 else ('low' if gap < 5 else 'very low')
    return dict(ceiling=ceiling, target=target, gap=gap, fill_odds=odds)


def annotate_plays(plays):
    n = 0
    for p in plays:
        m = compute(p)
        p['make'] = m
        if m and p.get('edge', 0) < 3.0:
            if n == 0:
                print('\nMake-order helper (B2 plays: resting price that would clear 3.0 pts; info only, paper until Sergio places it):')
            n += 1
            print(f"  {p.get('player') or p.get('game')} {p.get('side', 'Under')} {p.get('line', '')}: Make target {m['target']}c "
                  f"(ceiling {m['ceiling']}c) vs ask {p.get('price', p.get('price_under'))}c -> {m['gap']}c below ask, fill odds {m['fill_odds']}")
    return plays
