"""Make-order helper (added 2026-10-11, Sergio): for plays whose edge sits just under the 3.0 bar (B2, 2.0-<3.0), compute the
resting-limit (Make) price that WOULD clear 3.0 vs Pinnacle fair. Pinnacle stays the anchor; this never changes fair/edge/tier.
  ceiling = fair - 3.0          worst price that still clears the live bar (rulebook: fill ceiling, never go past it)
  target  = ceiling - 1.0       where to rest the order first (1c cushion; step up to the ceiling per "Fill-aware order pricing")
  gap     = cost - target       cents below the current ask the target sits
Fill-odds read is on the CEILING (the price you will end up resting at): cost - ceiling = 3.0 - edge. <= 2c plausible
(edge >= 1.0); <= 4c low; else very low. Scans keep lines with edge >= MAKE_FLOOR (1.0 pts) so a Make price can be shown even
when the posted ask is nowhere near 3.0: "we don't have to take the price the book offers". Lines under 2.0 pts are labelled
'MAKE candidate': printed, NOT logged (only B2 2.0-<3.0 auto-logs); they become plays only if Sergio places the order. Only exchange venues (novig, kalshi)
take resting orders; DFS/other venues get nothing. A filled Make order is a live play ONLY if Sergio places it; until then the
play stays paper (B2) and the Make price is information. Logged on the ledger doc as makeCeiling / makeTarget / makeGap.
"""
MAKE_FLOOR = float(__import__("os").environ.get("BETTS_MAKE_FLOOR", "0.01"))  # scans keep lines with edge >= 1.0 pts as MAKE candidates
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
    gc = cost - ceiling
    odds = 'plausible' if gc <= 2 else ('low' if gc <= 4 else 'very low')
    return dict(ceiling=ceiling, target=target, gap=gap, fill_odds=odds)


def label(edge, edge_min):
    """Label for a candidate with edge (fraction) given the live bar edge_min: None live, 'B2 paper' 2.0-<3.0, 'MAKE candidate' below 2.0."""
    if round(edge, 4) >= edge_min - 1e-9:
        return None
    return 'B2 paper' if round(edge, 4) >= 0.02 - 1e-9 else 'MAKE candidate'


def eligible(p, m):
    """MAKE-ELIGIBLE (Sergio, 2026-10-11): borderline edge (2.0-<3.0) AND every reference book agrees with Pinnacle
    (consensus_status == 'confirmed') AND the Make price is plausibly fillable (ask - ceiling <= 2c) on an exchange."""
    return bool(m) and 2.0 - 1e-9 <= p.get('edge', 0) < 3.0 and p.get('consensus_status') == 'confirmed' and m['fill_odds'] == 'plausible'


def annotate_plays(plays):
    """Run AFTER consensus_flag sets p['consensus_status']. Sets p['make'] and p['make_eligible'].
    Only MAKE-ELIGIBLE plays are recommended; other under-3.0 plays get a one-line info price, nothing more."""
    elig, info = [], []
    for p in plays:
        m = compute(p)
        p['make'] = m
        p['make_eligible'] = eligible(p, m)
        if m and p.get('edge', 0) < 3.0 and m['fill_odds'] != 'very low':
            (elig if p['make_eligible'] else info).append((p, m))
    def line(p, m):
        return (f"{p.get('player') or p.get('game')} {p.get('side', 'Under')} {p.get('line', '')} ({p.get('venue')}, edge +{p.get('edge')}): "
                f"Make target {m['target']}c, ceiling {m['ceiling']}c vs ask {p.get('price', p.get('price_under'))}c "
                f"({m['gap']}c below ask, fill odds {m['fill_odds']}, consensus {p.get('consensus_status')})")
    if elig:
        print('\nMAKE-ELIGIBLE (borderline edge + all reference books agree; rest at target, never past ceiling, cancel before start; paper until Sergio places it):')
        for p, m in elig: print('  ' + line(p, m))
    if info:
        print('\nMake price info only (not eligible: edge <2.0 or books not all agreeing):')
        for p, m in info: print('  ' + line(p, m))
    return plays
