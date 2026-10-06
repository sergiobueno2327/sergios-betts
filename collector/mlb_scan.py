"""Sergio's Betts -- MLB Track B scan (added 2026-10-06). Pinnacle (The Odds API) vs Novig, same line, pregame.
Markets with real Pinnacle rows on The Odds API: pitcher_strikeouts, batter_total_bases, pitcher_hits_allowed.
(Earned runs / pitcher outs / batter hits have NO Pinnacle on the feed: use a Sergio screenshot per the rulebook.
 OddsPapi returns 403 for MLB on this plan, so there is no second Pinnacle route.)
Bars (rulebook): edge = Pinnacle power-de-vigged fair - Novig cost; live >= 3.0 pts, B2 paper 2.0-<3.0; zone 35-75c;
Pinnacle margin <= 8%. Both sides scanned. Novig voids if the player does not play (confirmed). FanDuel two-sided fair is an
INFO consensus flag only. Run: python3 mlb_scan.py   (needs THEODDSAPI_KEY; source .env)
"""
import os, sys, datetime, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, nrm, devig_power, american_to_prob, MAX_PIN_VIG
import consensus_flag, player_conflict, ref_books

KEY = os.environ.get('THEODDSAPI_KEY', '')
if not KEY:
    raise SystemExit('THEODDSAPI_KEY env var is required.')
B = 'https://api.the-odds-api.com/v4/sports/baseball_mlb'
MARKETS = {'pitcher_strikeouts': 'Strikeouts', 'batter_total_bases': 'Total bases', 'pitcher_hits_allowed': 'Hits allowed'}
ZONE = (0.35, 0.75)
LIVE, B2 = 0.03, 0.02


def main(hours=30):
    now = datetime.datetime.now(datetime.timezone.utc)
    evs = [e for e in (get(f"{B}/events?apiKey={KEY}") or [])
           if now < datetime.datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00')) < now + datetime.timedelta(hours=hours)]
    print(f"{len(evs)} MLB games in next {hours}h")
    plays = []
    for e in evs:
        j = get(f"{B}/events/{e['id']}/odds?apiKey={KEY}&bookmakers=pinnacle,novig&markets={','.join(MARKETS)}&oddsFormat=american") or {}
        book = collections.defaultdict(lambda: collections.defaultdict(dict))  # (mkt,player,line) -> book -> side -> price
        for bm in j.get('bookmakers', []):
            for m in bm.get('markets', []):
                for o in m.get('outcomes', []):
                    if o.get('price') is None or o.get('point') is None: continue
                    book[(m['key'], o['description'], float(o['point']))][bm['key']][o['name']] = o['price']
        ref = ref_books.ref_fairs('baseball_mlb', e['id'], list(MARKETS))
        for (mk, pl, line), bks in book.items():
            pin, nov = bks.get('pinnacle', {}), bks.get('novig', {})
            if 'Over' not in pin or 'Under' not in pin: continue
            po, pu = american_to_prob(pin['Over']), american_to_prob(pin['Under'])
            vig = po + pu - 1
            if vig > MAX_PIN_VIG: continue
            fo = devig_power(po, pu)
            for side, fair in (('Over', fo), ('Under', 1 - fo)):
                if side not in nov: continue
                cost = american_to_prob(nov[side])
                if not (ZONE[0] <= cost <= ZONE[1]): continue
                edge = fair - cost
                if edge < B2 - 1e-9: continue
                rf = ref.get((mk, nrm(pl), line), {})
                alt = {b: round((v if side == 'Over' else 1 - v) * 100, 1) for b, v in rf.items()}
                plays.append(dict(game=f"{e['away_team']}@{e['home_team']}", start=e['commence_time'], player=pl, stat=MARKETS[mk], market=mk,
                                  line=line, side=side, venue='novig', fair=round(fair * 100, 1), price=round(cost * 100, 1),
                                  edge=round(edge * 100, 1), pin_vig=round(vig, 4), alt_fairs=alt,
                                  label='TRACK B (live)' if edge >= LIVE - 1e-9 else 'B2 (paper, stake 0)'))
    plays.sort(key=lambda p: -p['edge'])
    for p in plays:
        print(f"{p['label']}: {p['player']} {p['stat']} {p['side']} {p['line']} | {p['game']} start {p['start']} | novig {p['price']}c vs Pinnacle fair {p['fair']}% | edge +{p['edge']} | margin {p['pin_vig']*100:.1f}%")
    try:
        plays = player_conflict.resolve(plays)
    except Exception as ex:
        print('player_conflict skipped:', ex)
    consensus_flag.annotate_plays(plays)
    print(f"\n{sum(p['edge'] >= 3 for p in plays)} live and {sum(p['edge'] < 3 for p in plays)} B2 MLB plays. Reminder: DNP voids on Novig; confirm lineup status if posted.")


if __name__ == '__main__':
    main()
