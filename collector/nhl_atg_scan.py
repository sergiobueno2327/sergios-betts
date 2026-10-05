"""Sergio's Betts -- NHL anytime goalscorer (ATG) scan. PAPER ONLY (track "B-ATG"), added 2026-10-04.

Why separate: Pinnacle's ATG market is ONE-SIDED (Yes only, ~14-16 top scorers per game), so the vig can't be removed
exactly like two-sided props. Method:
  1. Pinnacle raw Yes prob p_pin (from player_goal_scorer_anytime).
  2. Calibrate Pinnacle's margin per game: c = median over players of (Novig two-sided fair Over-0.5 prob / p_pin),
     where Novig's fair comes from its exchange Over 0.5 / Under 0.5 (player_goals) via power de-vig. Need >= MIN_CAL players.
     Pinnacle fair = p_pin * c. (If Novig can't calibrate, fall back to a flat c = 0.93 and DOUBLE the required edge.)
  3. Compare to every book's Yes / Over-0.5 price (excluding Novig itself when it was used for calibration of that player? --
     Novig is evaluated too, but only against Pinnacle * c where c is the game-median, so it is not circular per player).
  4. Bar: edge >= 4.0 pts (higher than Track B's 3.0 to cover the de-vig uncertainty), zone 35-75c ... NOTE ATG prices are
     usually 15-45c; plays below 35c are shown as "OUT OF ZONE (info)" and are NOT paper-logged unless --allow-low-zone.
Logged stake 0. Review at 50 graded plays (CLV vs Pinnacle close).

Usage: THEODDSAPI_KEY=... python3 collector/nhl_atg_scan.py [--min-edge 0.04] [--allow-low-zone] [--write out.json]
"""
import os, sys, json, time, datetime, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, nrm, devig_power, american_to_prob, ODDSAPI_VENUES, MAX_PIN_VIG

KEY = os.environ.get('THEODDSAPI_KEY', '')
OA = 'https://api.the-odds-api.com/v4'
MIN_CAL = 5
FALLBACK_C = 0.93
ZONE = (0.35, 0.75)


def scan(edge_min=0.04, allow_low=False, days=2):
    if not KEY:
        sys.exit('THEODDSAPI_KEY env var is required')
    now = datetime.datetime.now(datetime.timezone.utc)
    evs = [e for e in get(f"{OA}/sports/icehockey_nhl/events?apiKey={KEY}") or []
           if now < datetime.datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00')) < now + datetime.timedelta(days=days)]
    print(f"NHL: {len(evs)} games in next {days}d")
    plays, info = [], []
    E = []   # per-game parsed data
    for ev in evs:
        d = get(f"{OA}/sports/icehockey_nhl/events/{ev['id']}/odds?apiKey={KEY}&regions=us,us2,us_ex,eu"
                f"&markets=player_goal_scorer_anytime,player_goals&oddsFormat=american")
        if not d:
            continue
        yes, two = {}, {}   # yes: book -> {norm: (name, prob)}; two: book -> {norm: {'Over':p,'Under':p}}
        for b in d.get('bookmakers', []):
            for m in b['markets']:
                for o in m['outcomes']:
                    nm = o.get('description') or ''
                    if not nm:
                        continue
                    if m['key'] == 'player_goal_scorer_anytime' and o['name'] == 'Yes':
                        yes.setdefault(b['key'], {})[nrm(nm)] = (nm, american_to_prob(o['price']))
                    elif m['key'] == 'player_goals' and o.get('point') in (0.5, '0.5'):
                        two.setdefault(b['key'], {}).setdefault(nrm(nm), {})[o['name']] = american_to_prob(o['price'])
                        if o['name'] == 'Over':
                            yes.setdefault(b['key'], {})[nrm(nm)] = (nm, american_to_prob(o['price']))
        if not yes.get('pinnacle'):
            print(f"  {ev['away_team']} @ {ev['home_team']}: no Pinnacle ATG yet -- skip")
            continue
        E.append((ev, yes, two))
        time.sleep(0.3)
    # calibrate Pinnacle's margin GLOBALLY across all games (its ATG margin should be consistent)
    ratios = []
    for ev, yes, two in E:
        for n, (nm, pp) in yes['pinnacle'].items():
            s2 = two.get('novig', {}).get(n)
            if s2 and 'Over' in s2 and 'Under' in s2 and pp:
                ratios.append(devig_power(s2['Over'], s2['Under']) / pp)
    if len(ratios) >= MIN_CAL:
        c, need = statistics.median(ratios), edge_min
        iqr = (sorted(ratios)[int(.75 * (len(ratios) - 1))] - sorted(ratios)[int(.25 * (len(ratios) - 1))])
        print(f"Pinnacle ATG margin calibrated on {len(ratios)} Novig two-sided players across {len(E)} games: c={c:.3f} (IQR {iqr:.3f})")
    else:
        c, need = FALLBACK_C, edge_min * 2
        print(f"FALLBACK c={c} (only {len(ratios)} Novig matches) -> bar doubled to {need*100:.0f}pts")
    for ev, yes, two in E:
        for n, (nm, pp) in yes['pinnacle'].items():
            fair = pp * c
            best = None
            for bk, rows in yes.items():
                if bk == 'pinnacle' or bk not in (set(ODDSAPI_VENUES) | {'novig'}):
                    continue
                r = rows.get(n)
                if r and r[1] and (best is None or r[1] < best[0]):
                    best = (r[1], bk)
            if not best:
                continue
            edge = fair - best[0]
            row = dict(game=f"{ev['away_team']} @ {ev['home_team']}", start=ev['commence_time'], player=nm, venue=best[1],
                       fair=round(fair * 100, 1), price=round(best[0] * 100, 1), edge=round(edge * 100, 1),
                       pin_raw=round(pp * 100, 1), c=round(c, 3), bar=round(need * 100, 1))
            if edge >= need:
                (plays if (ZONE[0] <= best[0] <= ZONE[1] or allow_low) else info).append(row)
    plays.sort(key=lambda p: -p['edge'])
    info.sort(key=lambda p: -p['edge'])
    print(f"\n{len(plays)} NHL ATG PAPER plays clear the bar:")
    for p in plays:
        print(p)
    print(f"\n{len(info)} more clear the edge bar but are OUT OF ZONE (price < 35c), info only:")
    for p in info[:10]:
        print(p)
    return plays


def to_ledger_docs(plays):
    docs = {}
    for p in plays:
        et = datetime.datetime.fromisoformat(p['start'].replace('Z', '+00:00')).astimezone(datetime.timezone(datetime.timedelta(hours=-4)))
        did = f"{et.date().isoformat()}-nhl-atg-{p['player'].split()[-1].lower()}"
        docs[did] = dict(id=did, data=dict(
            date=et.date().isoformat(), game=p['game'], player=p['player'], market='Anytime Goal Over 0.5', entry=p['price'], fair=p['fair'],
            fairSource=f"Pinnacle ATG one-sided x Novig-calibrated margin c={p['c']} (nhl_atg_scan.py) vs {p['venue']}", edge=p['edge'],
            side='YES', sport='NHL', track='B-ATG', stake=0, fee=0, orderType='taker', start=p['start'],
            status=f"Paper — B-ATG, pending fill ({p['venue']})",
            note=f"One-sided Pinnacle ATG; vig removed via Novig two-sided calibration. Bar {p['bar']}pts. Verify player is playing/lineup.",
            close=None, result=None, zone='35–75'))
    return list(docs.values())


if __name__ == '__main__':
    em = float(sys.argv[sys.argv.index('--min-edge') + 1]) if '--min-edge' in sys.argv else 0.04
    pl = scan(em, '--allow-low-zone' in sys.argv)
    if '--write' in sys.argv:
        out = sys.argv[sys.argv.index('--write') + 1]
        json.dump(to_ledger_docs(pl), open(out, 'w'), indent=1)
        print(f"Wrote ledger docs to {out}")
