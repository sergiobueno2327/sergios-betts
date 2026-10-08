"""Sergio's Betts -- College football GAME-LINE Track B scan (added 2026-10-08, Sergio's request).

What it compares: Pinnacle's power-de-vigged fair (two-sided, SAME line, Pinnacle margin <= 8%) vs the Kalshi
price at that same line, BOTH sides of every Kalshi market (Yes and No), price zone 35-75c, pregame only.
Edge = Pinnacle fair - Kalshi cost, in probability points (taker cost = ask; a maker-at-bid+1c edge is
printed as info). LIVE bar >= 3.0 pts (track "B", stake 0 until Sergio places it); B2 = 2.0 to <3.0 pts is
paper-only and auto-logged (track "B2", stake 0). Same bars as every other Track B scan.

Why game lines only: neither The Odds API nor OddsPapi carries Pinnacle (or Novig) COLLEGE player props, so
college player props can only be FD-ONLY paper (dfs_scan.py). OddsPapi (sportId 14, tournamentId 27653
'NCAA, Regular Season', bookmaker 'pinnacle+30') DOES carry Pinnacle college spreads, totals, team totals and
moneyline, and Kalshi lists college game markets: KXNCAAFGAME (moneyline), KXNCAAFSPREAD ('<Team> wins by over
X.5' ladders for BOTH teams), KXNCAAFTOTAL ('Over X.5', No = Under). Team totals have no Kalshi market and are
not scanned. Only half-point lines are compared (whole-number Pinnacle lines can push; Kalshi has none).

Data flow (OddsPapi rate-limits with 429: calls are spaced ~6 s apart and the /v4/markets catalog is fetched
exactly once and reused, via collect_odds.spread_catalog() -- the signed-spread-line fix; bookmakerOutcomeId
signs are unreliable for alt lines):
  1. Kalshi events for the three series -> teams per game (event suffix) -> matched to OddsPapi fixtures by name.
  2. OddsPapi pinnacle+30 odds per matched fixture (full game, period 0): moneyline, main + alt spreads, totals.
  3. FRESHNESS CROSS-CHECK: The Odds API Pinnacle game lines for the same game (h2h, spreads, totals,
     alternate_spreads, alternate_totals; same call also returns FanDuel for the INFO consensus flag). Per line:
       both feeds agree (<= 1.0 pt on fair)  -> verified, OddsPapi value used
       feeds disagree by more than 1.0 pt   -> STALE/CONFLICT, shown but never logged (Odds API value shown)
       only The Odds API has the line       -> used (fetched live), verified
       only OddsPapi has the line           -> UNVERIFIED, shown but never logged
     A main spread/total line that differs between the feeds prints a 'MAIN LINE MISMATCH' warning.
  4. Edge test per Kalshi market, both sides; INFO consensus flag (consensus_flag.check) vs FanDuel two-sided
     fair at the same line (ref_books settings: BETTS_REF_BOOKS, MAX_REF_VIG). INFO only, never rejects a play.
No roster Gate 1 (team markets). Correlated ladder lines: the printout shows every qualifying line, the ledger
doc output keeps ONE line per game + market + direction (the highest edge; same-game ladder lines are one play).

Run (keys as env vars for the shell only, never in files):
  THEODDSAPI_KEY=... ODDSPAPI_KEY=... python3 collector/ncaaf_game_scan.py [--hours 48] [--write out.json]
NOTE grade_plays.py can grade NCAAF totals and moneyline CLV; SPREAD plays carry a `pinn` spec with mkt 'spread'
that the grader does not support yet -> close/CLV for spread plays is a manual backfill.
"""
import os, sys, re, json, time, datetime, argparse, unicodedata, urllib.request, urllib.error
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_odds as co
from nfl_scan import get, devig_power, KALSHI_BASE, ZONE, MAX_PIN_VIG
import consensus_flag
from ref_books import REF_BOOKS, MAX_REF_VIG

OA_KEY = os.environ.get('THEODDSAPI_KEY', '')
OP_KEY = os.environ.get('ODDSPAPI_KEY', '')
OA = 'https://api.the-odds-api.com/v4'
OA_SPORT = 'americanfootball_ncaaf'
OP_BASE = 'https://api.oddspapi.io/v4'
SPORT_ID, TOURNAMENT_ID = 14, 27653
EDGE_LIVE, EDGE_B2 = 0.03, 0.02
XCHECK_TOL = 0.010          # max fair disagreement (prob) between OddsPapi and The Odds API Pinnacle
OP_GAP = 6.0                # seconds between OddsPapi calls (429s otherwise)
PT, ET = ZoneInfo('America/Los_Angeles'), ZoneInfo('America/New_York')
KALSHI_SERIES = ('KXNCAAFGAME', 'KXNCAAFSPREAD', 'KXNCAAFTOTAL')

_last_op = [0.0]


# ---------------------------------------------------------------- helpers
def op_get(url, tries=6):
    """OddsPapi GET: >= OP_GAP seconds between calls, backoff on 429 / error bodies."""
    for a in range(tries):
        wait = OP_GAP - (time.time() - _last_op[0])
        if wait > 0:
            time.sleep(wait)
        _last_op[0] = time.time()
        try:
            j = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'curl/8'}), timeout=90))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(OP_GAP * (a + 1)); continue
            print(f'  [oddspapi HTTP {e.code}] {url.split("?")[0]}'); return None
        except Exception:
            time.sleep(2 * (a + 1)); continue
        if isinstance(j, dict) and 'error' in j:
            time.sleep(OP_GAP * (a + 1)); continue
        return j
    return None


co.get = lambda url, headers=None, tries=4: op_get(url)   # collect_odds helpers go through the paced getter


def alias(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower().replace('&', ' and ')
    toks = [('state' if t in ('st', 'st.') else t) for t in re.split(r'[\s]+', s) if t]
    return re.sub(r'[^a-z0-9]', '', ''.join(toks))


def name_score(kname, names):
    """3 = exact (after alias), 2 = Kalshi school name is a prefix of the full OddsPapi/Odds-API name."""
    k, best = alias(kname), 0
    if not k:
        return 0
    for n in names:
        a = alias(n)
        if not a:
            continue
        if k == a:
            best = max(best, 3)
        elif a.startswith(k) and len(k) >= 4:
            best = max(best, 2)
    return best


def prob_dec(p):
    return 1.0 / p if p and p > 1 else None


def pair_fair(pa, pb, max_vig=MAX_PIN_VIG):
    """(fair_a, margin) or (None, margin) when the two-sided margin is too wide."""
    m = pa + pb - 1
    if m > max_vig or pa <= 0 or pb <= 0:
        return None, m
    return devig_power(pa, pb), m


def american_prob(a):
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def parse_iso(s):
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))


# ---------------------------------------------------------------- Kalshi
def kalshi_events(series):
    out, cur = [], None
    for _ in range(12):
        u = f"{KALSHI_BASE}/events?series_ticker={series}&status=open&limit=200&with_nested_markets=true" + (f"&cursor={cur}" if cur else '')
        d = get(u) or {}
        out += d.get('events', [])
        cur = d.get('cursor')
        if not cur or not d.get('events'):
            break
        time.sleep(0.3)
    return out


def tag_date(suffix):
    m = re.match(r'(\d\d)([A-Z]{3})(\d\d)', suffix)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(f'20{m.group(1)}{m.group(2)}{m.group(3)}', '%Y%b%d').date()
    except ValueError:
        return None


SPREAD_RE = re.compile(r'(.+?)\s+wins by over\s+([\d.]+)', re.I)


def load_kalshi(max_days):
    """suffix -> dict(date, teams:set, series:{series:[(event, market)]})"""
    games, today = {}, datetime.date.today()
    for ser in KALSHI_SERIES:
        for e in kalshi_events(ser):
            suf = e['event_ticker'].split('-', 1)[1] if '-' in e['event_ticker'] else ''
            d = tag_date(suf)
            if not d or not (today - datetime.timedelta(days=1) <= d <= today + datetime.timedelta(days=max_days + 1)):
                continue
            g = games.setdefault(suf, dict(date=d, teams=set(), markets={s: [] for s in KALSHI_SERIES}))
            for m in e.get('markets', []):
                if m.get('status') not in ('active', 'open'):
                    continue
                g['markets'][ser].append(m)
                if ser == 'KXNCAAFGAME':
                    g['teams'].add(m.get('yes_sub_title') or '')
                elif ser == 'KXNCAAFSPREAD':
                    mt = SPREAD_RE.match(m.get('yes_sub_title') or '')
                    if mt:
                        g['teams'].add(mt.group(1).strip())
    return games


def match_fixture(kgame, fixtures):
    """Best OddsPapi fixture for a Kalshi game by team names; returns (fixture, {kalshi_name: 'home'|'away'})."""
    teams = [t for t in kgame['teams'] if t]
    if len(teams) < 2:
        return None, {}
    best, tie = (0, None, None), False
    for fx in fixtures:
        fd = parse_iso(fx['startTime']).astimezone(ET).date()
        if abs((fd - kgame['date']).days) > 1:
            continue
        hn = [fx.get('participant1Name'), fx.get('participant1ShortName'), fx.get('participant1Abbr')]
        an = [fx.get('participant2Name'), fx.get('participant2ShortName'), fx.get('participant2Abbr')]
        # assign each Kalshi team name to the side it scores higher on; need both sides covered
        sides, total = {}, 0
        for t in teams:
            sh, sa = name_score(t, hn), name_score(t, an)
            if sh == 0 and sa == 0:
                continue
            side, sc = ('home', sh) if sh > sa else ('away', sa) if sa > sh else (None, 0)
            if side and side not in sides.values():
                sides[t] = side
                total += sc
        if len(set(sides.values())) == 2 and total >= 4:
            if total > best[0]:
                best, tie = (total, fx, sides), False
            elif total == best[0]:
                tie = True
    if tie or best[1] is None:
        return None, {}
    return best[1], best[2]


# ---------------------------------------------------------------- Pinnacle: OddsPapi
def op_fairs(fx):
    """{('ml',None)|('spread',home_line)|('total',line): dict(p={...}, margin, main, changed)} + stats."""
    d = op_get(f"{OP_BASE}/odds?apiKey={OP_KEY}&fixtureId={fx['fixtureId']}")   # no bookmakers= (see collect_odds)
    out, wide = {}, 0
    if not d:
        return None, 0
    pin = (d.get('bookmakerOdds') or {}).get('pinnacle+30', {}).get('markets', {})
    scat = co.spread_catalog()
    for mid, m in pin.items():
        parts = (m.get('bookmakerMarketId') or '').split('/')
        mtype, period = (parts[-1] if parts else ''), (parts[-2] if len(parts) > 1 else '')
        if period != '0' or mtype not in ('moneyline', 'spreads', 'totals'):
            continue
        rows = []
        for oid, o in (m.get('outcomes') or {}).items():
            p = (o.get('players') or {}).get('0')
            if not p or not p.get('active') or not p.get('price'):
                continue
            rows.append((oid, p))
        if len(rows) != 2:
            continue
        probs, line, key = {}, None, None
        changed = max((p.get('changedAt') or '') for _, p in rows)
        main = any(p.get('mainLine') for _, p in rows)
        if mtype == 'moneyline':
            for oid, p in rows:
                probs[p.get('bookmakerOutcomeId')] = 1.0 / p['price']
            if set(probs) != {'home', 'away'}:
                continue
            key = ('ml', None)
        elif mtype == 'spreads':
            for oid, p in rows:
                got = scat.get((mid, int(oid)))
                if not got:
                    break
                side, ln = got
                probs[side] = 1.0 / p['price']
                if side == 'home':
                    line = ln
            if set(probs) != {'home', 'away'} or line is None:
                continue
            key = ('spread', float(line))
        else:
            for oid, p in rows:
                ls, _, sel = (p.get('bookmakerOutcomeId') or '').rpartition('/')
                try:
                    line = float(ls)
                except ValueError:
                    line = None
                probs[sel] = 1.0 / p['price']
            if set(probs) != {'over', 'under'} or line is None:
                continue
            key = ('total', float(line))
        a, b = sorted(probs)
        fa, margin = pair_fair(probs[a], probs[b])
        if fa is None:
            wide += 1
            continue
        if key in out and not main:
            continue
        out[key] = dict(p={a: fa, b: 1 - fa}, margin=margin, main=main, changed=changed, src='oddspapi')
    # OddsPapi can keep an OLD main-line market alive next to the current one (seen: Air Force @ NIU had main
    # +10 and +7.5). Only the most recently changed main per kind stays 'main'; older ones are demoted.
    for kind in ('spread', 'total'):
        mains = [(v['changed'], k) for k, v in out.items() if k[0] == kind and v['main']]
        for _, k in sorted(mains)[:-1]:
            out[k]['main'] = False
            out[k]['demoted'] = True
    return out, wide


# ---------------------------------------------------------------- The Odds API (Pinnacle cross-check + FanDuel INFO)
def oa_events():
    return get(f'{OA}/sports/{OA_SPORT}/events?apiKey={OA_KEY}') or []


def match_oa_event(fx, events):
    st = parse_iso(fx['startTime'])
    hn = [fx.get('participant1Name'), fx.get('participant1ShortName')]
    an = [fx.get('participant2Name'), fx.get('participant2ShortName')]
    best = None
    for e in events:
        if abs((parse_iso(e['commence_time']) - st).total_seconds()) > 3 * 3600:
            continue
        sc = min(name_score(e['home_team'], hn) or 0, name_score(e['away_team'], an) or 0)
        # Odds API uses full names ("Liberty Flames") = OddsPapi participantName -> score 3 each
        sc = name_score(e['home_team'], hn) + name_score(e['away_team'], an)
        if sc >= 5 and (best is None or sc > best[0]):
            best = (sc, e)
    return best[1] if best else None


def oa_book_fairs(bm, home_name, away_name, max_vig):
    """{key: dict(p, margin, main)} for one bookmaker's two-sided h2h / spreads / totals (+ alternates)."""
    side_of = lambda n: 'home' if alias(n) == alias(home_name) else 'away' if alias(n) == alias(away_name) else None
    out, wide = {}, 0
    mk = {m['key']: m for m in bm.get('markets', [])}
    if 'h2h' in mk:
        pr = {side_of(o['name']): american_prob(o['price']) for o in mk['h2h']['outcomes']}
        if set(pr) == {'home', 'away'} and None not in pr.values():
            fa, mg = pair_fair(pr['home'], pr['away'], max_vig)
            if fa is not None:
                out[('ml', None)] = dict(p={'home': fa, 'away': 1 - fa}, margin=mg, main=True)
            else:
                wide += 1
    for mkey, main_key in (('spreads', 'spreads'), ('alternate_spreads', 'spreads')):
        if mkey not in mk:
            continue
        px = {}
        for o in mk[mkey]['outcomes']:
            s = side_of(o['name'])
            if s and o.get('point') is not None:
                px[(s, float(o['point']))] = american_prob(o['price'])
        for (s, pt), ph in list(px.items()):
            if s != 'home':
                continue
            pa = px.get(('away', -pt))
            if ph is None or pa is None:
                continue
            fa, mg = pair_fair(ph, pa, max_vig)
            if fa is None:
                wide += 1
                continue
            key = ('spread', pt)
            if key not in out or mkey == 'spreads':
                out[key] = dict(p={'home': fa, 'away': 1 - fa}, margin=mg, main=(mkey == 'spreads'))
    for mkey in ('totals', 'alternate_totals'):
        if mkey not in mk:
            continue
        px = {}
        for o in mk[mkey]['outcomes']:
            if o.get('point') is not None:
                px[(o['name'].lower(), float(o['point']))] = american_prob(o['price'])
        for (s, pt), po in list(px.items()):
            if s != 'over':
                continue
            pu = px.get(('under', pt))
            if po is None or pu is None:
                continue
            fa, mg = pair_fair(po, pu, max_vig)
            if fa is None:
                wide += 1
                continue
            key = ('total', pt)
            if key not in out or mkey == 'totals':
                out[key] = dict(p={'over': fa, 'under': 1 - fa}, margin=mg, main=(mkey == 'totals'))
    return out, wide


def oa_game(ev):
    """(pinnacle_fairs, {book: fairs}) for one Odds API event, or (None, {})."""
    books = ','.join(('pinnacle',) + tuple(b for b in REF_BOOKS if b != 'pinnacle'))
    mkts = 'h2h,spreads,totals,alternate_spreads,alternate_totals'
    j = get(f"{OA}/sports/{OA_SPORT}/events/{ev['id']}/odds?apiKey={OA_KEY}&bookmakers={books}&markets={mkts}&oddsFormat=american")
    if not j or 'bookmakers' not in j:
        return None, {}
    pin, refs = None, {}
    for bm in j['bookmakers']:
        if bm['key'] == 'pinnacle':
            pin, _ = oa_book_fairs(bm, ev['home_team'], ev['away_team'], MAX_PIN_VIG)
        else:
            refs[bm['key']], _ = oa_book_fairs(bm, ev['home_team'], ev['away_team'], MAX_REF_VIG)
    return pin, refs


def combine(op, oa):
    """Per-key Pinnacle fair with a freshness verdict. Returns {key: dict(p, margin, main, status, note)}."""
    out = {}
    for key in set(op or {}) | set(oa or {}):
        a, b = (op or {}).get(key), (oa or {}).get(key)
        if a and b:
            side = sorted(a['p'])[0]
            diff = abs(a['p'][side] - b['p'][side])
            if diff <= XCHECK_TOL:
                out[key] = dict(a, status='verified', note=f'x-check OK (diff {diff*100:.1f})')
            else:
                out[key] = dict(b, status='stale', note=f'CONFLICT: OddsPapi vs Odds API differ {diff*100:.1f} pts (OddsPapi changed {a.get("changed","?")[11:16]}Z)')
        elif b:
            out[key] = dict(b, status='verified', src='oddsapi', note='Odds API only (live)')
        else:
            out[key] = dict(a, status='unverified', note='OddsPapi only, not in The Odds API')
    return out


# ---------------------------------------------------------------- scan
def fmt_sel(kind, side, line, home, away):
    if kind == 'ml':
        return f'{home if side == "home" else away} ML'
    if kind == 'total':
        return f'{side.capitalize()} {line:g}'
    ln = line if side == 'home' else -line
    return f'{home if side == "home" else away} {ln:+g}'


def evaluate(kg, fx, sides, fairs, refs):
    """All Kalshi candidates (both sides of every market) with fair/cost/edge. Returns list of dicts."""
    home, away = fx['participant1ShortName'], fx['participant2ShortName']
    side_of_k = lambda name: next((s for k, s in sides.items() if alias(k) == alias(name)), None)
    cands = []

    def add(kind, mk, ksid, sel_side, line, fair_key, fair_side, price, bid, label):
        f = fairs.get(fair_key)
        if not f or price is None:
            return
        fair = f['p'].get(fair_side)
        if fair is None:
            return
        edge = fair - price
        refp = None
        for bk, rf in refs.items():
            r = rf.get(fair_key)
            if r and fair_side in r['p']:
                refp = (bk, r['p'][fair_side])
        cands.append(dict(kind=kind, ticker=mk['ticker'], ksid=ksid, sel_side=sel_side, line=line, label=label,
                          fair=fair, cost=price, edge=edge, bid=bid, margin=f['margin'], main=f.get('main'),
                          status=f['status'], fnote=f['note'], src=f.get('src', 'oddspapi'), ref=refp,
                          vol=float(mk.get('volume_fp') or 0), oi=float(mk.get('open_interest_fp') or 0)))

    num = lambda m, k: float(m.get(k) or 0) or None
    for mk in kg['markets']['KXNCAAFGAME']:
        s = side_of_k(mk.get('yes_sub_title') or '')
        if not s:
            continue
        o = 'away' if s == 'home' else 'home'
        ya, na = num(mk, 'yes_ask_dollars'), num(mk, 'no_ask_dollars')
        add('ml', mk, 'YES', s, None, ('ml', None), s, ya, num(mk, 'yes_bid_dollars'), fmt_sel('ml', s, None, home, away))
        add('ml', mk, 'NO', o, None, ('ml', None), o, na, num(mk, 'no_bid_dollars'), fmt_sel('ml', o, None, home, away))
    for mk in kg['markets']['KXNCAAFSPREAD']:
        mt = SPREAD_RE.match(mk.get('yes_sub_title') or '')
        s = side_of_k(mt.group(1).strip()) if mt else None
        L = mk.get('floor_strike')
        if not s or L is None:
            continue
        L = float(L)
        o = 'away' if s == 'home' else 'home'
        h_yes = -L if s == 'home' else L          # home's signed line of the pair where team s is -L
        h_no = h_yes                              # NO (opponent +L) is the other side of the SAME pair
        ya, na = num(mk, 'yes_ask_dollars'), num(mk, 'no_ask_dollars')
        add('spread', mk, 'YES', s, -L, ('spread', h_yes), s, ya, num(mk, 'yes_bid_dollars'),
            f'{home if s == "home" else away} {-L:+g}')
        add('spread', mk, 'NO', o, L, ('spread', h_no), o, na, num(mk, 'no_bid_dollars'),
            f'{home if o == "home" else away} {L:+g}')
    for mk in kg['markets']['KXNCAAFTOTAL']:
        L = mk.get('floor_strike')
        if L is None:
            continue
        L = float(L)
        ya, na = num(mk, 'yes_ask_dollars'), num(mk, 'no_ask_dollars')
        add('total', mk, 'YES', 'over', L, ('total', L), 'over', ya, num(mk, 'yes_bid_dollars'), f'Over {L:g}')
        add('total', mk, 'NO', 'under', L, ('total', L), 'under', na, num(mk, 'no_bid_dollars'), f'Under {L:g}')
    # the same selection can come from two Kalshi markets (ML Yes on one team vs No on the other): keep cheapest
    best = {}
    for c in cands:
        k = (c['kind'], c['sel_side'], c['line'])
        if k not in best or c['cost'] < best[k]['cost']:
            best[k] = c
    return list(best.values())


def classify(c):
    in_zone = ZONE[0] <= c['cost'] <= ZONE[1]
    if not in_zone or c['edge'] < EDGE_B2:
        return None
    return 'B' if c['edge'] >= EDGE_LIVE else 'B2'


def scan(hours=48):
    if not (OA_KEY and OP_KEY):
        sys.exit('THEODDSAPI_KEY and ODDSPAPI_KEY env vars are required (shell only, never in files)')
    now = datetime.datetime.now(datetime.timezone.utc)
    end = now + datetime.timedelta(hours=hours)
    print(f"College football game-line scan  {now:%Y-%m-%d %H:%M}Z  window {hours}h (to {end:%m-%d %H:%M}Z)")
    kal = load_kalshi(hours / 24.0 + 1)
    print(f"Kalshi college game events in range: {len(kal)}")
    # OddsPapi: catalog once (spread signs), then fixtures
    co.prop_catalog()
    for _ in range(3):
        if co.spread_catalog():
            break
        co._prop_catalog_cache = None
        co.prop_catalog()
    if not co.spread_catalog():
        sys.exit('OddsPapi /v4/markets catalog unavailable (429?) -- spread signs cannot be resolved; try again')
    fx_all = op_get(f"{OP_BASE}/fixtures?apiKey={OP_KEY}&sportId={SPORT_ID}&tournamentId={TOURNAMENT_ID}"
                    f"&from={now.date()}&to={(end.date() + datetime.timedelta(days=1))}") or []
    fixtures = []
    for f in fx_all:
        if not f.get('hasOdds') or f.get('statusName') in ('Cancelled', 'Postponed'):
            continue
        st = parse_iso(f['startTime'])
        if now < st <= end:
            fixtures.append(f)
    print(f"OddsPapi NCAA fixtures with odds in window: {len(fixtures)}")
    oa_evs = oa_events()
    plays, rejects, misses, notes, matched_fx = [], [], [], [], set()
    for suf, kg in sorted(kal.items(), key=lambda kv: kv[1]['date']):
        fx, sides = match_fixture(kg, fixtures)
        if not fx:
            continue
        matched_fx.add(fx['fixtureId'])
        home, away = fx['participant1ShortName'], fx['participant2ShortName']
        st = parse_iso(fx['startTime'])
        hrs = (st - now).total_seconds() / 3600
        head = f"{away} @ {home}  {st.astimezone(PT):%a %-I:%M %p} PT ({hrs:.1f}h)"
        op, wide_op = op_fairs(fx)
        ev = match_oa_event(fx, oa_evs)
        oa, refs = (oa_game(ev) if ev else (None, {}))
        if op is None and oa is None:
            notes.append(f"{head}: no Pinnacle data from either feed"); continue
        fairs = combine(op, oa)
        # main-line mismatch warnings
        warn = []
        for kind in ('spread', 'total'):
            m1 = [k[1] for k, v in (op or {}).items() if k[0] == kind and v.get('main')]
            m2 = [k[1] for k, v in (oa or {}).items() if k[0] == kind and v.get('main')]
            if m1 and m2 and set(m1) != set(m2):
                warn.append(f"MAIN LINE MISMATCH {kind}: OddsPapi {m1} vs Odds API {m2}")
        if not ev:
            warn.append('no Odds API Pinnacle match -> all OddsPapi lines UNVERIFIED (not loggable)')
        mains = []
        for k, v in sorted(fairs.items(), key=lambda kv: str(kv[0])):
            if v.get('main'):
                if k[0] == 'ml':
                    mains.append(f"ML {home} {v['p']['home']*100:.1f}%")
                elif k[0] == 'spread':
                    mains.append(f"spread {home} {k[1]:+g} ({v['p']['home']*100:.1f}%)")
                else:
                    mains.append(f"total {k[1]:g} (O {v['p']['over']*100:.1f}%)")
        print(f"\n{head}  [{suf}]")
        latest = max([v.get('changed') or '' for v in (op or {}).values()] or [''])
        print(f"  Pinnacle main: {' | '.join(mains) or 'none'}  | verified lines: "
              f"{sum(1 for v in fairs.values() if v['status']=='verified')}/{len(fairs)}  | OddsPapi last change {latest[:16]}Z")
        for w in warn:
            print(f"  ! {w}")
        cands = evaluate(kg, fx, sides, fairs, refs)
        for c in cands:
            c.update(game=f"{away} @ {home}", home=home, away=away, start=fx['startTime'], fid=fx['fixtureId'],
                     hours=hrs, suffix=suf, home_full=fx['participant1Name'], oa_id=(ev or {}).get('id'))
            cls = classify(c)
            c['cls'] = cls
            if cls:
                (plays if c['status'] == 'verified' else rejects).append(c)
            elif ZONE[0] <= c['cost'] <= ZONE[1]:
                misses.append(c)
    # report
    unm = [f"{f['participant2ShortName']} @ {f['participant1ShortName']}" for f in fixtures if f['fixtureId'] not in matched_fx]
    print(f"\nOddsPapi fixtures with no Kalshi match (no Kalshi college market, or name mismatch): {len(unm)}")
    if unm:
        print('  ' + '; '.join(unm))
    for n in notes:
        print('  ' + n)
    plays.sort(key=lambda c: -c['edge'])
    for p in plays + rejects:
        r = consensus_flag.check(p['fair'], {p['ref'][0]: p['ref'][1]} if p['ref'] else None)
        p['consensus'] = r['msg']
    print(f"\n=== {len(plays)} play(s) clear the bar (B >= {EDGE_LIVE*100:.0f} pts live / B2 {EDGE_B2*100:.0f}-<{EDGE_LIVE*100:.0f} paper; zone 35-75c) ===")
    for p in plays:
        tag = 'LIVE-BAR Track B' if p['cls'] == 'B' else 'B2 paper'
        ok = ''
        bid = p['bid']
        mk_px = f", maker@{min(bid + 0.01, p['cost'] - 0.01)*100:.0f}c edge {(p['fair']-min(bid+0.01,p['cost']-0.01))*100:+.1f}" if bid else ''
        print(f"[{tag}] {p['game']} | {p['kind']} {p['label']} (Kalshi {p['ksid']}) | cost {p['cost']*100:.0f}c fair {p['fair']*100:.1f}c "
              f"edge {p['edge']*100:+.1f}pts{mk_px} | pin margin {p['margin']*100:.1f}% {'main' if p['main'] else 'alt'} | "
              f"vol {p['vol']:.0f} | {p['hours']:.1f}h out | {p['fnote']}{ok}")
        if 'gap' in p['consensus'] or 'OK' in p['consensus']:
            print(f"     {p['consensus']}" + ('' if p['main'] else '  (alt line: FanDuel alt ladders carry heavy juice -> weak cross-check)'))
    rejects.sort(key=lambda c: -c['edge'])
    print(f"\n=== {len(rejects)} would-be play(s) REJECTED by the Pinnacle freshness check (shown for transparency, never logged) ===")
    print("    (OddsPapi keeps ghost lines at the edge of its ladder from older Pinnacle snapshots -- e.g. Air Force @ NIU -10.5 with a stale +10 main; The Odds API's live Pinnacle does not list them.)")
    for p in rejects:
        print(f"  {p['game']} | {p['kind']} {p['label']} (Kalshi {p['ksid']}) | cost {p['cost']*100:.0f}c fair {p['fair']*100:.1f}c edge {p['edge']*100:+.1f} | {p['status'].upper()}: {p['fnote']}"
              + (f" | FanDuel {p['ref'][1]*100:.1f}c" if p['ref'] else ''))
    misses.sort(key=lambda c: -c['edge'])
    print("\nClosest in-zone misses (info):")
    for p in misses[:8]:
        print(f"  {p['game']} | {p['kind']} {p['label']} | cost {p['cost']*100:.0f}c fair {p['fair']*100:.1f}c edge {p['edge']*100:+.1f} | {p['status']}")
    return plays


# ---------------------------------------------------------------- ledger docs
def to_ledger_docs(plays):
    """One doc per game + market kind + direction (best-edge ladder line only); verified plays only."""
    best = {}
    for p in plays:
        if p['status'] != 'verified':
            continue
        direction = p['sel_side'] + ('-fav' if p['kind'] == 'spread' and p['line'] < 0 else '-dog' if p['kind'] == 'spread' else '')
        k = (p['fid'], p['kind'], direction)
        if k not in best or best[k]['edge'] < p['edge']:
            best[k] = p
    docs = []
    for p in best.values():
        st = parse_iso(p['start'])
        et = st.astimezone(ET)
        slug = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())[:6]
        ln = '' if p['line'] is None else str(p['line']).replace('.', '').replace('-', 'm').replace('+', 'p')
        selkey = slug(p['home'] if p['sel_side'] == 'home' else p['away']) if p['kind'] != 'total' else p['sel_side'][0]
        tr = p['cls']
        doc_id = f"{et.date()}-{'b2-' if tr == 'B2' else ''}ncaaf-{slug(p['away'])}-{slug(p['home'])}-{p['kind'][:3]}-{selkey}{ln}"
        mname = {'ml': 'Moneyline', 'spread': 'Spread', 'total': 'Total Points'}[p['kind']]
        label = p['label']
        team_for_who = p['home_full']
        # grader convention: pinn.team / line describe the Kalshi YES contract's team; play['side'] flips it for NO
        yes_team = p['sel_side'] if p['ksid'] == 'YES' else ('away' if p['sel_side'] == 'home' else 'home')
        if p['kind'] == 'total':
            pinn = dict(who=team_for_who, mkt='total', line=p['line'], per='0')
        elif p['kind'] == 'ml':
            pinn = dict(who=team_for_who, mkt='ml', per='0', team=yes_team)
        else:
            yl = p['line'] if p['ksid'] == 'YES' else -p['line']
            pinn = dict(who=team_for_who, mkt='spread', per='0', team=yes_team, line=yl)
        data = dict(
            date=str(et.date()), game=p['game'].replace(' @ ', ' at '), player=f"{p['away']}–{p['home']}",
            market=f"{mname} {label}" if p['kind'] != 'ml' else f"{mname} {label.replace(' ML', '')}",
            entry=round(p['cost'] * 100, 1), fair=round(p['fair'] * 100, 1), edge=round(p['edge'] * 100, 1),
            fairSource=f"Pinnacle no-vig (power de-vig, {p['margin']*100:.1f}% margin, {'main' if p['main'] else 'alt'} line; "
                       f"OddsPapi pinnacle+30 {p['fnote']}) vs Kalshi {p['ksid']} ask",
            side=p['ksid'], sport='NCAAF', track=tr, tier=('B2' if tr == 'B2' else None), stake=0, fee=0,
            orderType='taker', kalshi_ticker=p['ticker'], start=p['start'], pinn=pinn, close=None, result=None, zone='35–75',
            status=('Paper — B2 (2–3pt band), pending fill (kalshi)' if tr == 'B2' else 'Paper — Track B (live bar), stake 0 until Sergio places, pending fill (kalshi)'),
            note=(f"ncaaf_game_scan.py {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%MZ}: {p['hours']:.1f}h before kickoff. "
                  f"Kalshi {p['ksid']} ask {p['cost']*100:.0f}c vs Pinnacle fair {p['fair']*100:.1f}c = {p['edge']*100:+.1f} pts. "
                  f"{p['consensus']} Same-game ladder lines are one correlated play. "
                  + ("Spread CLV close is a manual backfill (grade_plays.py has no spread support). " if p['kind'] == 'spread' else '')
                  + "Re-check Pinnacle price/line before kickoff; paper only."),
        )
        data = {k: v for k, v in data.items() if v is not None}
        docs.append(dict(id=doc_id, data=data))
    return docs


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--hours', type=float, default=48)
    ap.add_argument('--write', help='write ledger-ready docs (JSON) here')
    a = ap.parse_args()
    pl = scan(a.hours)
    if a.write:
        d = to_ledger_docs(pl)
        json.dump(d, open(a.write, 'w'), indent=1)
        print(f"\nWrote {len(d)} ledger-ready docs to {a.write}")
