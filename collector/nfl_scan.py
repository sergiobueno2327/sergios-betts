"""Sergio's Betts — NFL Track B scan (rulebook: Track B — Pure +EV, paper only; no model needed,
NFL's own model is "not ready" per the receptions-model note — this is pure market-mispricing
scanning, same idea as softness_scan.py but for NFL player props).

Run in chat: python3 nfl_scan.py [--edge 3] [--write out.json]

FAIR PRICE + EXECUTION VENUES — real, tested 2026-09-27, not guessed:
  Primary path (works today, dies with the SGO trial on 2026-10-04): SportsGameOdds Pro trial
  (SGO_KEY). ONE API call per game returns Pinnacle (fair) AND all 5 rulebook venues — Kalshi,
  Novig, Fliff, ProphetX (slug "prophetexchange" on SGO), PrizePicks — in the same payload
  (byBookmaker), confirmed live against real games this week (e.g. TB@MIN had pinnacle:90,
  kalshi:106, novig:100, fliff:127, prophetexchange:79, prizepicks:250 odds entries). Real oddID
  pattern per stat: {statPrefix}-{PLAYER_NAME}_1_NFL-game-ou-{over|under}, each with its own
  byBookmaker.{slug}.overUnder (books can quote different lines for the same player — this script
  only compares a venue's price against Pinnacle when they're quoting the SAME line, not just
  matched by player+market).

  Fallback path for after the SGO trial dies (STUBBED, not wired — the pieces below are each
  individually confirmed real, just not assembled into one path yet, and OddsPapi's own NFL
  market IDs remain undiscovered, so there's a real gap here to close before Oct 4):
    - Kalshi direct API (no key needed) — confirmed real schema 2026-09-27: series tickers
      KXNFLPASSINT / KXNFLPASSYDS / KXNFLPASSCOMP / KXNFLPASSATT / KXNFLPASSTDS / KXNFLRECYDS /
      KXNFLREC / KXNFLRSHYDS / KXNFLRSHATT (see STAT_MARKETS below). GET /events?series_ticker=X
      (no status filter needed — status=open returned nothing even for real open events, unclear
      why, don't assume it'll work), then /markets?series_ticker=X&event_ticker=Y gives per-player
      per-strike markets with real fields yes_bid_dollars / yes_ask_dollars / no_bid_dollars /
      no_ask_dollars / floor_strike / status. Ticker suffix format confirmed:
      {TEAM}{FIRSTINITIAL}{LASTNAME}{JERSEY}-{strikeNum}, e.g. KXNFLPASSINT-26SEP27MINTB-
      MINKMURRAY1-1 (Kyler Murray, Minnesota, jersey 1, "1+" strike). Match by team+lastname
      substring (softness_scan.py's kalshi_hit_ticker pattern), not by reconstructing the jersey
      number.
    - The Odds API (THEODDSAPI_KEY) — confirmed real market keys 2026-09-27: player_pass_yds,
      player_pass_interceptions, player_pass_completions, player_pass_attempts, player_pass_tds,
      player_reception_yds, player_receptions, player_rush_yds, player_rush_attempts,
      player_anytime_td. Decimal odds (convert: prob = 1/decimal), outcome has 'point' for the
      line and 'description' for the player name. CONFIRMED Kalshi is NOT reliably present on
      this API for NFL player props (tested 3 events x 4 markets, zero Kalshi results) even though
      it appears for game-lines (h2h) — use Kalshi's own API above instead, don't rely on this
      API for Kalshi prop prices. Novig/Fliff/ProphetX/PrizePicks all confirmed present.
    - Pinnacle fair price after SGO dies: UNSOLVED. OddsPapi (ODDSPAPI_KEY) is the intended source
      per the rulebook's paid-plan decision but has 429'd on every attempt all session — see
      discover_oddspapi_nfl_ids() below, same pattern as nhl_scan.py. Don't hardcode a guessed
      sportId/marketId.

Gate 1 mandatory check (rulebook, added 2026-09-27 after the Kyler Murray/Arizona mix-up): every
player's CURRENT team is verified against a live ESPN roster pull before a play is logged — never
from memory, never from just trusting the data provider's own team label.
"""
import json, math, os, sys, time, re, unicodedata, datetime, urllib.request

SGO_KEY = os.environ.get('SGO_KEY', 'bb9cfaece214108ec95bde6bd1b910a5')  # trial — dead 2026-10-04
SGO_EXPIRES = datetime.date(2026, 10, 4)
THEODDSAPI_KEY = os.environ.get('THEODDSAPI_KEY', '9417cf57c29dbc7c2eca39dfa9a68334')
ODDSPAPI_KEY = os.environ.get('ODDSPAPI_KEY', '15490352-5f73-404d-9964-353ab0783e01')

EDGE_MIN_PIN, EDGE_MIN_EXCH = 0.03, 0.04  # Track B bars: Pinnacle-based >=3pts, exchange-based >=4pts
ZONE = (0.35, 0.75)
EXEC_VENUES = ('kalshi', 'novig', 'fliff', 'prophetexchange', 'prizepicks')

# stat -> (SGO statID prefix, The Odds API market key, Kalshi series ticker, Pinnacle-unit label
# for the rulebook's grader `pinn.mkt` field). All confirmed real 2026-09-27, none guessed.
STAT_MARKETS = {
    'passing_interceptions': ('passing_interceptions', 'player_pass_interceptions', 'KXNFLPASSINT', 'Interceptions'),
    'passing_yards':         ('passing_yards',         'player_pass_yds',           'KXNFLPASSYDS', 'Passing Yards'),
    'passing_completions':   ('passing_completions',   'player_pass_completions',   'KXNFLPASSCOMP', 'Pass Completions'),
    'passing_attempts':      ('passing_attempts',       'player_pass_attempts',      'KXNFLPASSATT', 'Pass Attempts'),
    'passing_touchdowns':    ('passing_touchdowns',     'player_pass_tds',           'KXNFLPASSTDS', 'Touchdown Passes'),
    'receiving_yards':       ('receiving_yards',        'player_reception_yds',      'KXNFLRECYDS', 'Receiving Yards'),
    'receptions':            ('receiving_receptions',   'player_receptions',         'KXNFLREC', 'Receptions'),
    'rushing_yards':         ('rushing_yards',           'player_rush_yds',           'KXNFLRSHYDS', 'Rushing Yards'),
    'rushing_attempts':      ('rushing_attempts',        'player_rush_attempts',      'KXNFLRSHATT', 'Rush Attempts'),
}

# Static team map — team abbreviations don't change season to season, unlike player rosters, so
# this is safe to hardcode (contrast with player-team pairing, which must always be verified live).
NFL_TEAMS = {
    'ARI': 'ari', 'ATL': 'atl', 'BAL': 'bal', 'BUF': 'buf', 'CAR': 'car', 'CHI': 'chi', 'CIN': 'cin',
    'CLE': 'cle', 'DAL': 'dal', 'DEN': 'den', 'DET': 'det', 'GB': 'gb', 'HOU': 'hou', 'IND': 'ind',
    'JAX': 'jax', 'KC': 'kc', 'LAC': 'lac', 'LAR': 'lar', 'LV': 'lv', 'MIA': 'mia', 'MIN': 'min',
    'NE': 'ne', 'NO': 'no', 'NYG': 'nyg', 'NYJ': 'nyj', 'PHI': 'phi', 'PIT': 'pit', 'SEA': 'sea',
    'SF': 'sf', 'TB': 'tb', 'TEN': 'ten', 'WAS': 'wsh',
}


def get(url, headers=None, tries=3):
    """SportsGameOdds also blocks Python's default User-Agent with a 403 (confirmed 2026-09-27,
    same issue as Pinnapi and the NHL API) -- always send curl/8. ESPN's NFL endpoints don't need
    this (confirmed working with the default UA), but sending curl/8 doesn't hurt them either."""
    h = {'User-Agent': 'curl/8'}
    h.update(headers or {})
    for _ in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30))
        except Exception:
            time.sleep(1.5)
    return None


def nrm(s):
    return re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower())


def american_to_prob(american):
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def devig_power(p_a, p_b):
    """Power method — same implementation as softness_scan.py / nba_scan.py / nhl_scan.py."""
    lo, hi = 0.01, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        s = p_a ** (1 / mid) + p_b ** (1 / mid)
        if s > 1:
            hi = mid
        else:
            lo = mid
    k = (lo + hi) / 2
    fa, fb = p_a ** (1 / k), p_b ** (1 / k)
    return fa / (fa + fb)


# ---------- Gate 1 mandatory check: live team verification ----------
_roster_cache = {}


def verify_player_team(player_name, team_abbrev):
    """True/False/None (None = couldn't check, e.g. bad team code). Pulls the live ESPN roster —
    never trust memory or the odds provider's own team label for this (rulebook rule, added
    2026-09-27 after the Kyler Murray/Arizona mix-up)."""
    espn_abbr = NFL_TEAMS.get(team_abbrev.upper())
    if not espn_abbr:
        return None
    if espn_abbr not in _roster_cache:
        j = get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{espn_abbr}/roster") or {}
        names = set()
        for grp in j.get('athletes', []):
            for p in grp.get('items', []):
                names.add(nrm(p.get('fullName', '')))
        _roster_cache[espn_abbr] = names
    return nrm(player_name) in _roster_cache[espn_abbr]


# ---------- SGO: Pinnacle fair + all 5 execution venues in one call ----------
def sgo_events_this_week(days=5):
    today = datetime.date.today()
    d0, d1 = today.isoformat(), (today + datetime.timedelta(days=days)).isoformat()
    j = get(f"https://api.sportsgameodds.com/v2/events?leagueID=NFL&startsAfter={d0}&startsBefore={d1}&limit=50",
            headers={'x-api-key': SGO_KEY}) or {}
    out = []
    if not j.get('success'):
        return out
    for e in j.get('data', []):
        th, ta = e['teams']['home'], e['teams']['away']
        out.append(dict(id=e['eventID'], home=th['names']['short'], away=ta['names']['short'],
                         start=e['status']['startsAt'], started=e['status'].get('started', False)))
    return out


def sgo_stat_odds(event_id, stat_prefix):
    """{norm_player_name: {'name': str, slug: (line, prob_this_side_over)}} for one stat market.
    Pinnacle's entry is de-vigged (it's the fair source); every other venue's entry is the RAW
    one-sided price at that line (what we'd actually pay) -- NOT de-vigged, since de-vigging an
    execution venue would erase the very mispricing Track B is trying to find (matches
    softness_scan.py's approach exactly)."""
    j = get(f"https://api.sportsgameodds.com/v2/events?eventID={event_id}&oddsAvailable=true",
            headers={'x-api-key': SGO_KEY}) or {}
    if not j.get('success') or not j.get('data'):
        return {}
    odds = j['data'][0].get('odds', {})
    out = {}
    for k, v in odds.items():
        if not (k.startswith(stat_prefix + '-') and k.endswith('-game-ou-over')):
            continue
        v_u = odds.get(k.replace('-over', '-under'))
        if not v_u:
            continue
        raw = v.get('statEntityID', '')
        name = raw.rsplit('_', 2)[0].replace('_', ' ').title() if raw.endswith('_NFL') else raw
        entry = out.setdefault(nrm(name), {'name': name})
        bb_o, bb_u = v.get('byBookmaker', {}), v_u.get('byBookmaker', {})
        for slug in set(bb_o) & set(bb_u):
            o, u = bb_o[slug], bb_u[slug]
            line_o, line_u = o.get('overUnder'), u.get('overUnder')
            if line_o is None or line_o != line_u:
                continue  # this book's own over/under aren't quoting the same line -- skip, don't guess
            po, pu = american_to_prob(o.get('odds')), american_to_prob(u.get('odds'))
            if po is None or pu is None:
                continue
            if slug == 'pinnacle':
                entry['pinnacle'] = (float(line_o), devig_power(po, pu))
            else:
                entry[slug] = (float(line_o), po, pu)  # raw one-sided prices, both kept for O and U
    return out


def discover_oddspapi_nfl_ids():
    """Same pattern as nhl_scan.py -- run once OddsPapi's paid tier is confirmed live, then
    hardcode the real NFL sportId/marketIds here instead of guessing them."""
    if not ODDSPAPI_KEY:
        print('No ODDSPAPI_KEY set.')
        return None
    sports = get(f"https://api.oddspapi.io/v4/sports?apiKey={ODDSPAPI_KEY}")
    print('sports response (look for NFL):')
    print(json.dumps(sports, indent=2)[:3000] if sports else '(no response / still 429)')
    return sports


# ---------- scan ----------
def scan(edge_min_override=None):
    if datetime.date.today() > SGO_EXPIRES:
        print(f"WARNING: SGO trial expired {SGO_EXPIRES} — this script's only working fair-price "
              f"path is dead. See discover_oddspapi_nfl_ids() / module docstring for the unfinished "
              f"fallback. Nothing will be found.")
        return []

    games = sgo_events_this_week()
    upcoming = [g for g in games if not g['started']]
    print(f"{len(games)} NFL games in SGO this week, {len(upcoming)} upcoming")
    plays = []
    team_check_cache = {}
    for g in upcoming:
        for stat, (sgo_prefix, oddsapi_mkt, kalshi_series, pinn_unit) in STAT_MARKETS.items():
            odds = sgo_stat_odds(g['id'], sgo_prefix)
            for norm_name, entry in odds.items():
                if 'pinnacle' not in entry:
                    continue
                pin_line, pin_fair_over = entry['pinnacle']
                candidate_edges = []
                for venue in EXEC_VENUES:
                    if venue not in entry:
                        continue
                    v_line, v_over_price, v_under_price = entry[venue]
                    if v_line != pin_line:
                        continue  # different line than Pinnacle's -- not a clean comparison, skip
                    edge_over = pin_fair_over - v_over_price
                    edge_under = (1 - pin_fair_over) - v_under_price
                    for side, edge, price in (('Over', edge_over, v_over_price), ('Under', edge_under, v_under_price)):
                        if edge < EDGE_MIN_PIN or not (ZONE[0] <= price <= ZONE[1]):
                            continue
                        candidate_edges.append((side, edge, price, venue))
                if not candidate_edges:
                    continue
                # Gate 1 mandatory check -- resolve which team this player is actually on, live,
                # before logging anything (rulebook rule, added 2026-09-27, Kyler Murray case).
                if entry['name'] not in team_check_cache:
                    on_home = verify_player_team(entry['name'], g['home'])
                    on_away = verify_player_team(entry['name'], g['away'])
                    if on_home:
                        team_check_cache[entry['name']] = g['home']
                    elif on_away:
                        team_check_cache[entry['name']] = g['away']
                    else:
                        team_check_cache[entry['name']] = None  # hard fail -- not on either live roster
                        print(f"  GATE 1 FAIL: {entry['name']} not found on {g['home']} or {g['away']}'s "
                              f"live ESPN roster -- skipping all plays for this player, not guessing which team.")
                team = team_check_cache[entry['name']]
                if team is None:
                    continue
                for side, edge, price, venue in candidate_edges:
                    plays.append(dict(game=f"{g['away']}@{g['home']}", team=team, start=g['start'], stat=stat,
                                       player=entry['name'], line=pin_line, side=side, venue=venue,
                                       fair=round((pin_fair_over if side == 'Over' else 1 - pin_fair_over) * 100, 1),
                                       price=round(price * 100, 1), edge=round(edge * 100, 1),
                                       pinn_unit=pinn_unit))
    plays.sort(key=lambda p: -p['edge'])
    print(f"\n{len(plays)} Track B plays clear the bar (>= {EDGE_MIN_PIN*100:.0f}pts Pinnacle-based, zone 35-75c):")
    for p in plays:
        print(p)
    return plays


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--discover-oddspapi':
        discover_oddspapi_nfl_ids()
    else:
        scan()
