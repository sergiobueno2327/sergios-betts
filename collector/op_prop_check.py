"""Live Pinnacle prop re-check via OddsPapi (for screenshot plays The Odds API can't price). Added 2026-10-06.
Usage: python3 op_prop_check.py NHL 2026-10-06 MIN BUF Thompson assists 0.5 [cost_pct]
  sport NHL only for now; market keyword matched against OddsPapi marketType (assists, shotsongoal, points, saves ...).
Prints Pinnacle Over/Under decimal prices, the price's last-update time (STALE if > 3h old), de-vigged fair for BOTH sides,
and the edge vs cost_pct (cost for the side you'd bet, in %, e.g. 54.3 for PrizePicks Flex) if given.
Needs ODDSPAPI_KEY in the environment (source .env). Never prints or writes the key.
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, devig_power
import nhl_scan as N

def main():
    sport, day, away, home, who, mkt, line = sys.argv[1:8]
    cost = float(sys.argv[8]) if len(sys.argv) > 8 else None
    K = N.ODDSPAPI_KEY
    look = {}
    for c in get(f"https://api.oddspapi.io/v4/markets?apiKey={K}") or []:
        if c.get('sportId') == 15 and c.get('playerProp') and mkt.lower() in (c.get('marketType') or '').lower() and abs(float(c['handicap']) - float(line)) < 1e-6:
            oids = {o['outcomeName']: o['outcomeId'] for o in c.get('outcomes', [])}
            look[str(c['marketId'])] = (oids.get('Over'), oids.get('Under'))
    fx = [f for f in N.oddspapi_fixtures_for_day(datetime.date.fromisoformat(day)) if (f.get('away'), f.get('home')) == (away, home)]
    if not fx: print('no OddsPapi fixture for', away, home); return
    j = get(f"https://api.oddspapi.io/v4/odds?apiKey={K}&fixtureId={fx[0]['fixtureId']}") or {}
    for mid, m in j.get('bookmakerOdds', {}).get('pinnacle+30', {}).get('markets', {}).items():
        if mid not in look: continue
        oi, ui = look[mid]; oc = m.get('outcomes', {})
        ov, un = oc.get(str(oi), {}).get('players', {}), oc.get(str(ui), {}).get('players', {})
        for pid, po in ov.items():
            if who.lower() in (po.get('playerName') or '').lower() and pid in un:
                pu = un[pid]; a, b = 1 / po['price'], 1 / pu['price']; fo = devig_power(a, b)
                ts = po.get('changedAt') or po.get('updatedAt') or ''
                age = ''
                try:
                    t = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')); h = (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() / 3600
                    age = f"{h:.1f}h old{' -- STALE, do not trust' if h > 3 else ''}"
                except Exception: pass
                print(f"{po['playerName']} {mkt} {line}: Over dec {po['price']} / Under dec {pu['price']} | updated {ts} ({age})")
                print(f"  de-vigged fair Over {fo*100:.2f}% Under {(1-fo)*100:.2f}% | margin {(a+b-1)*100:.2f}%")
                if cost is not None: print(f"  edge vs cost {cost}: Over {fo*100-cost:+.2f}, Under {(1-fo)*100-cost:+.2f}")
                return
    print('player/market/line not found on Pinnacle in OddsPapi')

if __name__ == '__main__':
    main()
