"""Sergio's Betts — WNBA Track B scan (rulebook: Track B — Pure +EV, paper only; no model needed,
same idea as nfl_scan.py/softness_scan.py but for WNBA player props). Added 2026-10-04 after a
screenshot cross-check (Jonquel Jones rebounds) showed real, cleanly comparable Pinnacle vs
execution-venue prices for WNBA on infrastructure Sergio already pays for -- confirmed live same
day: The Odds API carries real Pinnacle WNBA player props (points/rebounds/assists; threes has
no live Pinnacle rows yet, so left out of STAT_MARKETS for now, same as NFL stats get added only
once confirmed real) AND all 4 other execution venues (novig/fliff/prophetx/prizepicks). No
OddsPapi catalog lookup needed here (unlike NFL) -- The Odds API's own outcome schema already
carries player name + line + price directly, same simpler pattern as the MLB pitcher-strikeouts
check from earlier today.

Kalshi: confirmed live via /series?category=Sports -- real player-prop series exist
(KXWNBAPTS, KXWNBAREB, KXWNBAAST; no KXWNBA3PT player-prop series matching Pinnacle's missing
threes market). Same market shape as NFL: title "{Player}: {strike+1}+ {stat}", floor_strike ==
the line -- kalshi_series_markets()/kalshi_price()/kalshi_ticker() are fully generic over
(series_ticker, dtag, away, home) and are REUSED UNCHANGED from nfl_scan.py (imported directly,
not copy-pasted) since the Kalshi API schema is identical across sports.

Run in chat: python3 wnba_scan.py [--write out.json]

Gate 1 mandatory check (same rulebook rule as NFL/NBA/NHL): every player's CURRENT team is
verified against a live ESPN roster pull before a play is logged -- important here specifically
because WNBA rosters shift during the playoffs (trades don't happen, but injury/COVID replacement
players can appear who aren't on a cached roster).

Season context: WNBA is in its playoffs as of 2026-10-04 -- a short remaining window (Finals by
end of October most years), not a season-long track like NFL/NBA/NHL. Worth re-evaluating after
the Finals whether to keep this running into the WNBA offseason (no games = scan trivially finds
nothing, cheap to leave wired up) or explicitly pause it until next season.
"""
import pin_move
import json, os, sys, time, datetime, collections
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pp_fliff
from nfl_scan import MAX_PIN_VIG, merge_best, side_candidates  # market-width filter + best-price helpers
from nfl_scan import (  # reuse generic, sport-agnostic pieces unchanged
    get, nrm, devig_power, kalshi_date_tag, kalshi_series_markets, kalshi_price, kalshi_ticker,
    _not_kicked_off,
)

THEODDSAPI_KEY = os.environ.get('THEODDSAPI_KEY', '')
if not THEODDSAPI_KEY:
    raise SystemExit('THEODDSAPI_KEY env var is required (no default key baked in).')

EDGE_MIN_PIN = 0.03  # Track B bar: Pinnacle-based >=3pts (same as NFL)
ZONE = (0.35, 0.75)
from nfl_scan import ODDSAPI_VENUES  # shared, env-overridable (BETTS_BOOKS)

# stat -> (The Odds API market key, Kalshi series ticker, Pinnacle-unit label for the rulebook's
# grader `pinn.mkt` field). Confirmed live 2026-10-04 against NY@ATL (event
# b9425fe89f25de73a858e88be2dd535e) -- threes intentionally excluded, no live Pinnacle rows yet.
STAT_MARKETS = {
    'points':   ('player_points', 'KXWNBAPTS', 'Points'),
    'rebounds': ('player_rebounds', 'KXWNBAREB', 'Rebounds'),
    'assists':  ('player_assists', 'KXWNBAAST', 'Assists'),
}

# ESPN abbrev (lowercase, for the roster URL) -- confirmed live 2026-10-04 via
# site.api.espn.com/.../basketball/wnba/teams (15 teams incl. 2026 expansion POR/TOR).
WNBA_TEAMS = {
    'ATL': 'atl', 'CHI': 'chi', 'CON': 'conn', 'DAL': 'dal', 'GS': 'gs', 'IND': 'ind', 'LV': 'lv',
    'LA': 'la', 'MIN': 'min', 'NY': 'ny', 'PHX': 'phx', 'POR': 'por', 'SEA': 'sea', 'TOR': 'tor',
    'WSH': 'wsh',
}

# The Odds API full team name -> our short abbrev (for matching an event to Kalshi/ESPN codes).
ODDSAPI_TEAM_ABBR = {
    'Atlanta Dream': 'ATL', 'Chicago Sky': 'CHI', 'Connecticut Sun': 'CON', 'Dallas Wings': 'DAL',
    'Golden State Valkyries': 'GS', 'Indiana Fever': 'IND', 'Las Vegas Aces': 'LV',
    'Los Angeles Sparks': 'LA', 'Minnesota Lynx': 'MIN', 'New York Liberty': 'NY',
    'Phoenix Mercury': 'PHX', 'Portland Fire': 'POR', 'Seattle Storm': 'SEA',
    'Toronto Tempo': 'TOR', 'Washington Mystics': 'WSH',
}

_roster_cache = {}


def verify_player_team(player_name, team_abbrev):
    """Same Gate 1 pattern as nfl_scan.py, WNBA roster endpoint."""
    espn_abbr = WNBA_TEAMS.get(team_abbrev.upper())
    if not espn_abbr:
        return None
    if espn_abbr not in _roster_cache:
        j = get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams/{espn_abbr}/roster") or {}
        names = set()
        for p in j.get('athletes', []):
            names.add(nrm(p.get('fullName', '')))
        _roster_cache[espn_abbr] = names
    return nrm(player_name) in _roster_cache[espn_abbr]


def oddsapi_events_this_week():
    return get(f"https://api.the-odds-api.com/v4/sports/basketball_wnba/events?apiKey={THEODDSAPI_KEY}") or []


def pinnacle_and_venue_odds(event_id):
    """One call per stat-group covers Pinnacle + all 4 exec venues at once (unlike NFL, which
    splits OddsPapi/Pinnacle from The Odds API/venues across two providers). Returns
    (pin_by_stat, venue_by_stat) in the same shapes nfl_scan.py's scan() loop expects."""
    mkts = ','.join(v[0] for v in STAT_MARKETS.values())
    bms = 'pinnacle,' + ','.join(ODDSAPI_VENUES)
    url = (f"https://api.the-odds-api.com/v4/sports/basketball_wnba/events/{event_id}/odds"
           f"?apiKey={THEODDSAPI_KEY}&bookmakers={bms}&markets={mkts}&oddsFormat=american")
    j = get(url) or {}
    mkt_to_stat = {v[0]: k for k, v in STAT_MARKETS.items()}

    def american_to_decimal(a):
        return 1 + (100 / abs(a) if a < 0 else a / 100)

    pin_by_stat = collections.defaultdict(dict)
    venue_by_stat = collections.defaultdict(lambda: collections.defaultdict(dict))
    for bm in j.get('bookmakers', []):
        venue = bm['key']
        for mkt in bm.get('markets', []):
            stat = mkt_to_stat.get(mkt['key'])
            if not stat:
                continue
            by_pl = collections.defaultdict(dict)
            for o in mkt.get('outcomes', []):
                if o.get('price') is None or o.get('point') is None or not o.get('description'):
                    continue
                by_pl[(nrm(o['description']), float(o['point']), o['description'])][o['name']] = o['price']
            for (norm_name, line, raw_name), sides in by_pl.items():
                if venue == 'pinnacle' and ('Over' not in sides or 'Under' not in sides):
                    continue
                if venue == 'pinnacle':
                    p_over = 1 / american_to_decimal(sides['Over'])
                    p_under = 1 / american_to_decimal(sides['Under'])
                    fair_over = devig_power(p_over, p_under)
                    pin_by_stat[stat][norm_name] = dict(name=raw_name, line=line, fair_over=fair_over, vig=round(p_over + p_under - 1, 4))
                else:
                    # execution venues: raw one-sided implied prob, NOT de-vigged (de-vigging an
                    # execution venue would erase the mispricing Track B looks for).
                    over_p = 1 / american_to_decimal(sides['Over']) if 'Over' in sides else None
                    under_p = 1 / american_to_decimal(sides['Under']) if 'Under' in sides else None
                    if venue == 'prizepicks':  # fixed-payout DFS, see pp_fliff.py
                        cur = venue_by_stat[stat][norm_name].get(line) or dict(venue=venue, over=None, under=None, over_venue=None, under_venue=None)
                        cur['pp_over'], cur['pp_under'] = 'Over' in sides, 'Under' in sides
                        venue_by_stat[stat][norm_name][line] = cur
                        continue
                    cur = merge_best(venue_by_stat[stat][norm_name].get(line), venue, over_p, under_p)
                    venue_by_stat[stat][norm_name][line] = cur
    return pin_by_stat, venue_by_stat


def scan(edge_min_override=None):
    edge_min = edge_min_override or EDGE_MIN_PIN
    events = [e for e in oddsapi_events_this_week() if _not_kicked_off(e['commence_time'])]
    print(f"{len(events)} upcoming WNBA games with live odds (The Odds API)")
    plays = []
    team_check_cache = {}

    for ev in events:
        away_full, home_full = ev['away_team'], ev['home_team']
        away = ODDSAPI_TEAM_ABBR.get(away_full)
        home = ODDSAPI_TEAM_ABBR.get(home_full)
        if not away or not home:
            print(f"  {away_full} @ {home_full}: unrecognized team name -- skip")
            continue
        pin_by_stat, venue_by_stat = pinnacle_and_venue_odds(ev['id'])
        try:
            import ref_books
            _ref = ref_books.ref_fairs('basketball_wnba', ev['id'], [v[0] for v in STAT_MARKETS.values()])
        except Exception as e:
            print('ref_books skipped:', e); _ref = {}
        if not any(pin_by_stat.values()):
            print(f"  {away}@{home}: no Pinnacle player-prop odds yet -- skip")
            continue
        dtag = kalshi_date_tag(ev['commence_time'])

        for stat, (oa_key, kalshi_series, pinn_unit) in STAT_MARKETS.items():
            for norm_name, pin in pin_by_stat.get(stat, {}).items():
                _pk = pin_move.key('WNBA', f"{away}@{home}", pin['name'], stat, pin['line'])
                _pm = pin_move.move(_pk, pin['fair_over'])
                pin_move.record(_pk, pin['fair_over'])
                candidates = []
                for line, info in venue_by_stat.get(stat, {}).get(norm_name, {}).items():
                    if abs(line - pin['line']) > 0.01:
                        continue
                    candidates.extend(side_candidates(info, pin['fair_over'], edge_min, ZONE))
                    candidates.extend(c for c in pp_fliff.extra_candidates(info, pin['fair_over']) if c not in candidates)
                kp = kalshi_price(kalshi_series, dtag, away, home, norm_name, pin['line'])
                if kp:
                    edge_over = pin['fair_over'] - kp['over']
                    edge_under = (1 - pin['fair_over']) - kp['under']
                    for side, edge, price in (('Over', edge_over, kp['over']), ('Under', edge_under, kp['under'])):
                        if edge >= edge_min and ZONE[0] <= price <= ZONE[1]:
                            candidates.append((side, edge, price, 'kalshi'))
                if not candidates:
                    continue

                if pin['name'] not in team_check_cache:
                    on_home = verify_player_team(pin['name'], home)
                    on_away = verify_player_team(pin['name'], away)
                    if on_home:
                        team_check_cache[pin['name']] = home
                    elif on_away:
                        team_check_cache[pin['name']] = away
                    else:
                        team_check_cache[pin['name']] = None
                        print(f"  GATE 1 FAIL: {pin['name']} not found on {home} or {away}'s "
                              f"live ESPN roster -- skipping all plays for this player.")
                team = team_check_cache[pin['name']]
                if team is None:
                    continue
                if pin.get('vig', 0) > MAX_PIN_VIG:
                    print(f"  WIDE MARKET skip: {pin['name']} {stat} {pin['line']} -- Pinnacle vig {pin['vig']*100:.1f}% > {MAX_PIN_VIG*100:.0f}%")
                    continue
                _alt = {_bk: round(_f * 100, 1) for _bk, _f in _ref.get((oa_key, norm_name, pin['line']), {}).items()}
                for side, edge, price, venue in candidates:
                    et_dt = datetime.datetime.fromisoformat(ev['commence_time'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York'))
                    plays.append(dict(game=f"{away}@{home}", team=team, start=ev['commence_time'], stat=stat,
                                       player=pin['name'], line=pin['line'], side=side, venue=venue,
                                       fair=round((pin['fair_over'] if side == 'Over' else 1 - pin['fair_over']) * 100, 1),
                                       price=round(price * 100, 1), edge=round(edge * 100, 1), label=pp_fliff.label(venue, edge), pinn_unit=pinn_unit,
                                       date=et_dt.date().isoformat(), kalshi_series=kalshi_series, dtag=dtag,
                                       away=away, home=home, pin_vig=pin.get('vig'),
                                       alt_fairs={k: (v if side == 'Over' else round(100 - v, 1)) for k, v in _alt.items()},
                                       pin_move=(None if _pm is None else round(_pm if side == 'Over' else -_pm, 1))))
        time.sleep(0.3)

    plays.sort(key=lambda p: -p['edge'])
    print(f"\n{len(plays)} Track B plays clear the bar (>= {edge_min*100:.0f}pts Pinnacle-based, zone 35-75c):")
    for p in plays:
        print(p)
    return plays


def to_ledger_docs(plays):
    """Same dedup-by-best-edge-per-player pattern as nfl_scan.py (bug there was found and fixed
    2026-10-04: the comparison was inverted, keeping the WORSE venue -- written correctly here
    from the start)."""
    docs = {}
    for p in plays:
        slug = p['player'].split()[-1].lower()
        line_tag = str(p['line']).replace('.', '')
        side_tag = 'o' if p['side'] == 'Over' else 'u'
        doc_id = f"{p['date']}-wnba-{slug}-{p['stat']}{line_tag}-{side_tag}"
        if doc_id in docs and docs[doc_id]['data']['edge'] >= p['edge']:
            continue
        side = 'YES' if p['side'] == 'Over' else 'NO'
        ticker = None
        if p['venue'] == 'kalshi':
            ticker = kalshi_ticker(p['kalshi_series'], p['dtag'], p['away'], p['home'], nrm(p['player']), p['line'])
        note = f"WNBA Track B scan {p['date']}. Edge +{p['edge']}pts vs {p['venue']}. Pinnacle fair {p['fair']}c. Pin move toward our side (24h): {p.get('pin_move')}pts."
        if p['venue'] != 'kalshi':
            note += f" Bet placed on {p['venue']}, not Kalshi -- needs manual {p['venue']} close for CLV, not a Kalshi close."
        elif not ticker:
            note += " Kalshi ticker lookup failed -- needs manual kalshi_ticker before grading."
        docs[doc_id] = dict(id=doc_id, data=dict(
            date=p['date'], game=p['game'], player=p['player'], market=f"{p['pinn_unit']} {p['side']} {p['line']}",
            entry=p['price'], fair=p['fair'],
            fairSource=f'Pinnacle no-vig (The Odds API, wnba_scan.py) vs {p["venue"]}',
            edge=p['edge'], side=side, sport='WNBA', track=('B2' if pp_fliff.is_b2(p.get('label')) else 'B'), stake=0, fee=0, label=p.get('label'),
            orderType='maker' if p['venue'] == 'kalshi' else 'taker',
            kalshi_ticker=ticker, start=p['start'],
            pinn=dict(who=p['player'], mkt=f"prop:{p['pinn_unit']}", line=p['line']),
            status=f"Paper — Track B scan, pending fill ({p['venue']})" if ticker or p['venue'] != 'kalshi' else 'Paper — needs kalshi_ticker',
            note=note, close=None, result=None, zone='35–75', pinMove=p.get('pin_move')))
    return list(docs.values())


if __name__ == '__main__':
    plays = scan()
    try:
        import player_conflict; plays = player_conflict.resolve(plays)  # no opposite sides / one-market-per-player guard
    except Exception as e:
        print('player_conflict skipped:', e)
    try:
        import adverse_flag; adverse_flag.annotate_plays(plays)  # info-only, non-blocking
    except Exception as e:
        print('adverse_flag skipped:', e)
    try:
        import consensus_flag; consensus_flag.annotate_plays(plays)  # info-only, non-blocking
    except Exception as e:
        print('consensus_flag skipped:', e)
    if '--write' in sys.argv:
        outpath = sys.argv[sys.argv.index('--write') + 1]
        docs = to_ledger_docs(plays)
        with open(outpath, 'w') as fh:
            json.dump(docs, fh, indent=2)
        print(f'\nWrote {len(docs)} ledger-ready docs to {outpath}')
