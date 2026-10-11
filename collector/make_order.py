"""Make-price line for B2 plays (simplified 2026-10-11). Info only: changes no fair, edge, tier, label or stake.
For a B2 play (2.0-<3.0 pts) on an exchange venue, prints the resting price that WOULD clear the live 3.0 bar:
ceiling = Pinnacle fair - 3.0 (never rest above it). Target/step-up/cancel rules are in the rulebook ("Fill-aware order pricing")."""
EX = ('novig', 'kalshi', 'prophetx')


def annotate_plays(plays):
    n = 0
    for p in plays:
        if p.get('venue') not in EX:
            continue
        e = p.get('edge', 0)
        cost = p.get('price', p.get('price_under'))
        fair = p.get('fair', p.get('fair_under'))
        if cost is None or fair is None or not (2.0 - 1e-9 <= e < 3.0):
            continue
        ceiling = round(fair - 3.0, 1)
        if ceiling < 35.0:
            continue
        if n == 0:
            print('\nMake ceiling for B2 plays (resting price that would clear 3.0 pts; info only, paper until Sergio places it):')
        n += 1
        print(f"  {p.get('player') or p.get('game')} {p.get('side', 'Under')} {p.get('line', '')} ({p['venue']}, edge +{e}): ceiling {ceiling}c vs ask {cost}c ({round(cost - ceiling, 1)}c below ask)")
    return plays
