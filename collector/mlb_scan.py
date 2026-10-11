"""Sergio's Betts -- MLB Track B scan (added 2026-10-06). Pinnacle (The Odds API) vs Novig, same line, pregame.
Markets with real Pinnacle rows on The Odds API: pitcher_strikeouts, batter_total_bases, pitcher_hits_allowed.
(Earned runs / pitcher outs / batter hits have NO Pinnacle on the feed: use a Sergio screenshot per the rulebook.
 OddsPapi returns 403 for MLB on this plan, so there is no second Pinnacle route.)
Bars (rulebook): edge = Pinnacle power-de-vigged fair - Novig cost; live >= 3.0 pts, B2 paper 2.0-<3.0; zone 35-75c;
Pinnacle margin <= 8%. Both sides scanned. Novig voids if the player does not play (confirmed). FanDuel two-sided fair is an
INFO consensus flag only. Run: python3 mlb_scan.py   (needs THEODDSAPI_KEY; source .env)
"""
import os, sys, datetime, collections
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, nrm, devig_power, american_to_prob, MAX_PIN_VIG
import consensus_flag, player_conflict, ref_books, make_order

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
    return plays


def report(plays):
    for p in plays:
        print(f"{p['label']}: {p['player']} {p['stat']} {p['side']} {p['line']} | {p['game']} start {p['start']} | novig {p['price']}c vs Pinnacle fair {p['fair']}% | edge +{p['edge']} | margin {p['pin_vig']*100:.1f}%")


_ABBR = {'pitcher_strikeouts': 'k', 'batter_total_bases': 'tb', 'pitcher_hits_allowed': 'hitsallowed'}
_UNIT = {'pitcher_strikeouts': 'Strikeouts', 'batter_total_bases': 'Bases', 'pitcher_hits_allowed': 'Hits Allowed'}


def to_ledger_docs(plays):
    """MLB is PAPER ONLY (rulebook): stake 0 always. >=3.0 -> track 'MLB-paper', 2.0-<3.0 -> 'B2'. Best edge per doc id kept (>=)."""
    docs = {}
    for p in plays:
        b2 = p['edge'] < 3.0
        date = datetime.datetime.fromisoformat(p['start'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        slug = p['player'].split()[-1].lower()
        doc_id = f"{date}-{'b2-' if b2 else ''}mlb-{slug}-{_ABBR[p['market']]}{str(p['line']).replace('.', '')}-{'o' if p['side'] == 'Over' else 'u'}"
        if doc_id in docs and docs[doc_id]['data']['edge'] >= p['edge']:
            continue
        docs[doc_id] = dict(id=doc_id, data=dict(
            date=date, game=p['game'], player=p['player'], market=f"{p['stat']} {p['side']} {p['line']}",
            entry=p['price'], fair=p['fair'], fairSource='Pinnacle no-vig live (The Odds API, mlb_scan.py) vs novig',
            edge=p['edge'], side='YES' if p['side'] == 'Over' else 'NO', sport='MLB', track='B2' if b2 else 'MLB-paper',
            stake=0, fee=0, label=p.get('label'), orderType='taker', kalshi_ticker=None, start=p['start'],
            pinn=dict(who=p['player'], mkt=f"prop:{_UNIT[p['market']]}", line=p['line']),
            status=f"Paper — {p.get('label')}", note=f"MLB scan {date}. Edge +{p['edge']}pts vs novig. Pinnacle fair {p['fair']}c. Paper only; DNP voids on Novig.",
            close=None, result=None, zone='35–75', pinMove=None))
    return list(docs.values())


def run(hours=30):
    import json
    plays = main(hours)
    for p in plays:
        p['label'] = None  # label is re-derived after the live gate
    try:
        import live_pin_verify; plays = live_pin_verify.verify(plays, 'baseball_mlb', lambda p: p['market'])
    except Exception as e:
        print('live_pin_verify FAILED -> no plays passed:', e); plays = []
    for p in plays:
        p['label'] = 'TRACK B (live-eligible, MLB paper)' if p['edge'] >= 3.0 - 1e-9 else 'B2 paper'
    try:
        plays = player_conflict.resolve(plays)
    except Exception as ex:
        print('player_conflict skipped:', ex)
    consensus_flag.annotate_plays(plays)  # info only
    make_order.annotate_plays(plays)
    report(plays)
    if '--write' in sys.argv:
        out = sys.argv[sys.argv.index('--write') + 1]
        docs = to_ledger_docs(plays)
        json.dump(docs, open(out, 'w'), indent=2)
        print(f'\nWrote {len(docs)} ledger-ready docs to {out}\nNot written to the ledger yet -- the calling session still does the ArtifactData batch write.')
    print(f"\n{sum(p['edge'] >= 3 for p in plays)} live-eligible (paper) and {sum(p['edge'] < 3 for p in plays)} B2 MLB plays. Reminder: DNP voids on Novig; confirm lineup status if posted.")


if __name__ == '__main__':
    run()
