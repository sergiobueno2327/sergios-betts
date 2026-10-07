"""Sergio's Betts -- feed-agnostic Pinnacle-vs-Novig scan on The Odds API only (added 2026-10-07).
Why: OddsPapi Pinnacle quotes can be hours stale or sit at a moved line (Egbuka U30.5 on 2026-10-06/07: OddsPapi 30.5, live Pinnacle 34.5), and
nhl_scan.py skips any game OddsPapi has no fixture for (EDM@ANA 2026-10-07). This scan reads Pinnacle LIVE from The Odds API for NHL, NFL, WNBA, NBA,
for every prop market with real Pinnacle rows, and compares to Novig's ask on the SAME line, both sides.
Bars (rulebook): edge = Pinnacle power-de-vigged fair - Novig cost; live >= 3.0 pts, B2 2.0-<3.0 (paper); zone 35-75c; Pinnacle margin <= 8%.
NHL SOG Overs are paper-only (NHL-Over-paper). FanDuel two-sided fair is an INFO flag. Novig voids on DNP.
Usage: python3 oddsapi_pin_scan.py [nhl|nfl|wnba|nba|all] [hours=36]     (needs THEODDSAPI_KEY; source .env)
"""
import os, sys, datetime, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, nrm, devig_power, american_to_prob, MAX_PIN_VIG
import consensus_flag, player_conflict, ref_books

KEY = os.environ.get('THEODDSAPI_KEY', '')
if not KEY:
    raise SystemExit('THEODDSAPI_KEY env var is required.')
SPORTS = {
    'nhl': ('icehockey_nhl', {'player_shots_on_goal': 'SOG', 'player_assists': 'Assists', 'player_points': 'Points', 'player_goals': 'Goals'}),
    'nfl': ('americanfootball_nfl', {'player_reception_yds': 'Rec yds', 'player_receptions': 'Receptions', 'player_rush_yds': 'Rush yds',
                                     'player_rush_attempts': 'Rush att', 'player_pass_yds': 'Pass yds', 'player_pass_attempts': 'Pass att',
                                     'player_pass_completions': 'Pass comp', 'player_pass_tds': 'Pass TD', 'player_pass_interceptions': 'INT'}),
    'wnba': ('basketball_wnba', {'player_points': 'Points', 'player_rebounds': 'Rebounds', 'player_assists': 'Assists', 'player_threes': 'Threes'}),
    'nba': ('basketball_nba', {'player_points': 'Points', 'player_rebounds': 'Rebounds', 'player_assists': 'Assists', 'player_threes': 'Threes'}),
}
ZONE = (0.35, 0.75)
LIVE, B2 = 0.03, 0.02


def scan(key, hours):
    sport, MK = SPORTS[key]
    B = f'https://api.the-odds-api.com/v4/sports/{sport}'
    now = datetime.datetime.now(datetime.timezone.utc)
    evs = [e for e in (get(f"{B}/events?apiKey={KEY}") or [])
           if now < datetime.datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00')) < now + datetime.timedelta(hours=hours)]
    print(f"\n== {key.upper()}: {len(evs)} games in next {hours}h")
    plays = []
    for e in evs:
        j = get(f"{B}/events/{e['id']}/odds?apiKey={KEY}&bookmakers=pinnacle,novig&markets={','.join(MK)}&oddsFormat=american") or {}
        book = collections.defaultdict(lambda: collections.defaultdict(dict))
        for bm in j.get('bookmakers', []):
            for m in bm.get('markets', []):
                for o in m.get('outcomes', []):
                    if o.get('price') is None or o.get('point') is None: continue
                    book[(m['key'], o['description'], float(o['point']))][bm['key']][o['name']] = o['price']
        npin = sum(1 for v in book.values() if 'Over' in v.get('pinnacle', {}) and 'Under' in v.get('pinnacle', {}))
        print(f"  {e['away_team']}@{e['home_team']} {e['commence_time']}: {npin} two-sided Pinnacle lines")
        ref = ref_books.ref_fairs(sport, e['id'], list(MK))
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
                paper = key == 'nhl' and mk == 'player_shots_on_goal' and side == 'Over'
                lab = 'NHL-Over-paper' if paper else ('TRACK B (live)' if edge >= LIVE - 1e-9 else 'B2 (paper, stake 0)')
                plays.append(dict(game=f"{e['away_team']}@{e['home_team']}", start=e['commence_time'], player=pl, stat=MK[mk], market=mk, line=line,
                                  side=side, venue='novig', fair=round(fair * 100, 1), price=round(cost * 100, 1), edge=round(edge * 100, 1),
                                  pin_vig=round(vig, 4), alt_fairs=alt, label=lab))
    try:
        plays = player_conflict.resolve(plays)
    except Exception as ex:
        print('player_conflict skipped:', ex)
    plays.sort(key=lambda p: -p['edge'])
    for p in plays:
        print(f"{p['label']}: {p['player']} {p['stat']} {p['side']} {p['line']} | {p['game']} {p['start']} | novig {p['price']}c vs Pinnacle fair {p['fair']}% | edge +{p['edge']} | margin {p['pin_vig']*100:.1f}%")
    consensus_flag.annotate_plays(plays)
    print(f"  -> {sum(p['edge'] >= 3 and p['label'].startswith('TRACK') for p in plays)} live, {sum(p['edge'] < 3 for p in plays)} B2, "
          f"{sum(p['label'] == 'NHL-Over-paper' for p in plays)} NHL Over paper")
    return plays


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    hrs = int(sys.argv[2]) if len(sys.argv) > 2 else 36
    for k in (SPORTS if which == 'all' else [which]):
        scan(k, hrs)
