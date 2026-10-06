"""Fliff + PrizePicks execution math (rule set added 2026-10-05 at Sergio's request).

PrizePicks: fixed multipliers, no shifting odds. Every leg is assumed to go into a 5- or 6-leg FLEX slip
(5-leg 10x/2x/0.4x -> break-even 54.25%/leg; 6-leg 25x/2x/0.4x -> 54.21%/leg, both solved from the payout
tables; locked at 54.3% = -119). ASSUMPTION: payout tables are PrizePicks' standard Flex payouts as of
2026-10 -- they change, re-verify in the app. Legs are treated as independent (correlated legs in one game
break that).
  Track B target : Pinnacle de-vigged fair >= 57.3%  (54.3 + the 3.0 pt bar)
  Track B2 (paper): 56.3% <= fair < 57.3%            (2.0-3.0 pt band, cost basis 54.3)
Fliff: ordinary retail book -> raw American price vs Pinnacle de-vigged fair, same 3.0 pt Track B bar and the
2.0-3.0 pt B2 paper band as every other venue. NOTE the "10-12 cents lag at -110/-125" rule of thumb is
~2.4-2.8 pts of probability, i.e. the B2 band, NOT Track B -- bars in probability points are not changed.
All of this only labels/adds candidates. Nothing changes the 3.0 pt rulebook bar for Track B.
"""
PP_BREAKEVEN = 0.543
PP_BAR = PP_BREAKEVEN + 0.03      # 0.573
PP_B2_LO = PP_BREAKEVEN + 0.02    # 0.563
FLIFF_B2_LO = 0.02
ZONE = (0.35, 0.75)
PP_LABEL = "PRIZEPICKS TRACK B TARGET - READY TO SLIP"
PP_B2_LABEL = "PRIZEPICKS B2 (paper, 54.3c basis)"


def extra_candidates(info, fair_over, zone=ZONE):
    """[(side, edge, price, venue)] for PrizePicks legs (fixed 54.3c cost) and Fliff (raw price) that clear
    the Track B bar OR sit in the 2-3 pt B2 band. info = merge_best dict carrying pp_over/pp_under (bool) and
    fliff_over/fliff_under (prob). Independent of which book is 'best price'."""
    out = []
    for side, fair in (('Over', fair_over), ('Under', 1 - fair_over)):
        k = side.lower()
        if info.get('pp_' + k) and PP_B2_LO - 1e-9 <= fair <= zone[1]:
            out.append((side, fair - PP_BREAKEVEN, PP_BREAKEVEN, 'prizepicks'))
        fp = info.get('fliff_' + k)
        if fp is not None and zone[0] <= fp <= zone[1] and fair - fp >= FLIFF_B2_LO:
            out.append((side, fair - fp, fp, 'fliff'))
    return out


def label(venue, edge):
    """edge in probability (0.03 = 3 pts)."""
    e = round(edge, 4)
    if venue == 'prizepicks':
        return PP_LABEL if e >= 0.03 - 1e-9 else PP_B2_LABEL
    if venue == 'fliff':
        return "FLIFF Track B" if e >= 0.03 - 1e-9 else "FLIFF B2 (paper)"
    return None


def is_b2(lbl):
    return bool(lbl) and 'B2' in lbl


# ---- Underdog Fantasy (added 2026-10-05): MANUAL screenshot checks only, not in any API feed or scan ----
UD_BREAKEVEN = 0.535              # LOCKED 2026-10-05: 2-Pick STANDARD only, 3.5x (Sergio-confirmed); sqrt(1/3.5)=53.45%, used as 53.5% (~-115 American). Both legs different games.
UD_BAR = UD_BREAKEVEN + 0.03      # 0.565: live Track B needs Pinnacle de-vigged fair >= 56.5%
UD_B2_LO = UD_BREAKEVEN + 0.02    # 0.555: paper B2 for 55.5% <= fair < 56.5%


def underdog_class(fair):
    """fair = Pinnacle de-vigged fair prob (0-1) of the leg's side. Returns (edge_pts, label)."""
    e = round(fair - UD_BREAKEVEN, 4)
    if fair >= UD_BAR - 1e-9:
        return e, "UNDERDOG TRACK B TARGET (manual)"
    if fair >= UD_B2_LO - 1e-9:
        return e, "UNDERDOG B2 (paper, 53.5c basis)"
    return e, "PASS"


if __name__ == '__main__':
    import sys
    from nfl_scan import devig_power, american_to_prob
    # python3 pp_fliff.py ud <pin_side_american> <pin_other_side_american>   e.g.  ud -142 107
    if len(sys.argv) == 4 and sys.argv[1] == 'ud':
        f = devig_power(american_to_prob(sys.argv[2]), american_to_prob(sys.argv[3]))
        print(round(f * 100, 1), underdog_class(f))
