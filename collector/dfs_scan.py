"""Sergio's Betts -- PrizePicks / Underdog scan (added 2026-10-06; FanDuel column added same day).
Each DFS leg (The Odds API, books prizepicks + underdog) is compared with Pinnacle's two-sided de-vigged fair at the SAME line
(margin <= 8%) and with FanDuel's two-sided fair (margin <= 14%, reference only). Bars unchanged (Pinnacle decides):
  PrizePicks 5/6-leg Flex break-even 54.3%: LIVE needs Pinnacle fair >= 57.3%, B2 paper 56.3-57.3.
  Underdog 2-pick Standard 3.5x break-even 53.5%: LIVE >= 56.5%, B2 paper 55.5-56.5 (legs from different games).
Labels: LIVE / B2 (Pinnacle clears), "+FD agrees" when FanDuel fair also clears the same bar, FD-ONLY (paper, never live) when only
FanDuel clears and Pinnacle has no price/does not clear. NHL SOG Overs are paper-only. Printed NEAR lines are >= 54.5% on either book.
LINE-SHIFT (added 2026-10-06, Sergio-approved): a DFS leg whose line differs from Pinnacle's gets a shifted fair estimate
(Pinnacle fair at its own line + median Over-prob delta across >= 3 OTHER books posting both lines, same method as ladder_scan.py).
These print as "LINE-SHIFT (paper)" when the estimate reaches the B2 floor; they never go live (shifted estimates carry more error).
Run: set -a && source .env && set +a && python3 collector/dfs_scan.py [hours=72]
"""
import os, sys, datetime, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, devig_power, american_to_prob as a, MAX_PIN_VIG

KEY = os.environ.get('THEODDSAPI_KEY', '')
if not KEY:
    raise SystemExit('THEODDSAPI_KEY env var is required.')
B = 'https://api.the-odds-api.com/v4'
S = {
 'icehockey_nhl': 'player_shots_on_goal,player_points,player_goals,player_assists',
 'baseball_mlb': 'pitcher_strikeouts,batter_total_bases,batter_hits,pitcher_outs,pitcher_hits_allowed,pitcher_earned_runs',
 'americanfootball_nfl': 'player_receptions,player_reception_yds,player_rush_yds,player_rush_attempts,player_pass_yds,player_pass_attempts,player_pass_completions,player_pass_tds,player_rush_reception_yds',
 'basketball_wnba': 'player_points,player_rebounds,player_assists,player_points_rebounds_assists,player_threes',
 'basketball_nba': 'player_points,player_rebounds,player_assists,player_points_rebounds_assists,player_threes',
}
TH = {'prizepicks': (0.573, 0.563), 'underdog': (0.565, 0.555)}
MAX_FD_VIG = 0.14
MAX_SHIFT = {'icehockey_nhl': 1.0, 'baseball_mlb': 1.0, 'americanfootball_nfl': 8.0, 'basketball_wnba': 3.0, 'basketball_nba': 3.0}
MIN_REF = 3
REF_EXCLUDE = {'pinnacle', 'prizepicks', 'underdog', 'betr_us_dfs'}
import statistics


def fair_pair(prices, cap):
    if not prices or 'Over' not in prices or 'Under' not in prices:
        return None, None
    po, pu = a(prices['Over']), a(prices['Under'])
    if po + pu - 1 > cap:
        return None, None
    fo = devig_power(po, pu)
    return fo, po + pu - 1


def main(hours=72):
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    shifts = []
    n = collections.Counter()
    for sp, mk in S.items():
        for e in get(f'{B}/sports/{sp}/events?apiKey={KEY}') or []:
            t = datetime.datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00'))
            if not (now < t < now + datetime.timedelta(hours=hours)):
                continue
            j = get(f"{B}/sports/{sp}/events/{e['id']}/odds?apiKey={KEY}&markets={mk}&bookmakers=prizepicks,underdog,pinnacle,fanduel&oddsFormat=american") or {}
            pin, fd, dfs = collections.defaultdict(dict), collections.defaultdict(dict), {}
            for bm in j.get('bookmakers', []):
                for m in bm.get('markets', []):
                    for o in m.get('outcomes', []):
                        if o.get('point') is None or o.get('price') is None:
                            continue
                        k = (m['key'], o['description'], float(o['point']))
                        if bm['key'] == 'pinnacle': pin[k][o['name']] = o['price']
                        elif bm['key'] == 'fanduel': fd[k][o['name']] = o['price']
                        else: dfs[(bm['key'],) + k] = True
            for (bk, mkk, pl, line) in dfs:
                n[(sp, bk)] += 1
                fp, mp = fair_pair(pin.get((mkk, pl, line)), MAX_PIN_VIG)
                ff, _ = fair_pair(fd.get((mkk, pl, line)), MAX_FD_VIG)
                if fp is None and ff is None:
                    cand = [(abs(l - line), l) for (m2, p2, l) in pin if m2 == mkk and p2 == pl and 0 < abs(l - line) <= MAX_SHIFT[sp]
                            and fair_pair(pin[(m2, p2, l)], MAX_PIN_VIG)[0] is not None]
                    if cand:
                        lp = min(cand)[1]
                        shifts.append((sp, e, bk, mkk, pl, line, lp, fair_pair(pin[(mkk, pl, lp)], MAX_PIN_VIG)[0]))
                    continue
                live, b2 = TH[bk]
                for side in ('Over', 'Under'):
                    f1 = None if fp is None else (fp if side == 'Over' else 1 - fp)
                    f2 = None if ff is None else (ff if side == 'Over' else 1 - ff)
                    best = max(x for x in (f1, f2) if x is not None)
                    if best < 0.545:
                        continue
                    if f1 is not None and f1 >= b2 - 1e-9:
                        cls = 'LIVE' if f1 >= live - 1e-9 else 'B2'
                        if f2 is not None and f2 >= b2 - 1e-9: cls += ' +FD agrees'
                    elif f2 is not None and f2 >= b2 - 1e-9:
                        cls = 'FD-ONLY (paper, never live)'
                    else:
                        cls = 'near'
                    if sp == 'icehockey_nhl' and mkk == 'player_shots_on_goal' and side == 'Over':
                        cls += ' [NHL Over: paper only]'
                    out.append((best, bk, f"{e['away_team']}@{e['home_team']}", e['commence_time'], pl, mkk, side, line,
                                None if f1 is None else round(f1 * 100, 1), None if f2 is None else round(f2 * 100, 1), cls))
    # ---- line-shift pass ----
    by_ev = collections.defaultdict(list)
    for sh in shifts:
        by_ev[(sh[0], sh[1]['id'])].append(sh)
    for (sp, eid), items in by_ev.items():
        mks = sorted({it[3] for it in items})
        mk2 = ','.join(mks + [m + '_alternate' for m in mks])
        j = get(f"{B}/sports/{sp}/events/{eid}/odds?apiKey={KEY}&regions=us,us2,us_ex,eu&markets={mk2}&oddsFormat=american") or {}
        lad = collections.defaultdict(lambda: collections.defaultdict(dict))  # (mkt,player) -> book -> line -> Over prob
        for bm in j.get('bookmakers', []):
            if bm['key'] in REF_EXCLUDE: continue
            for m in bm.get('markets', []):
                base = m['key'].replace('_alternate', '')
                for o in m.get('outcomes', []):
                    if o.get('name') == 'Over' and o.get('point') is not None and o.get('price') is not None and o.get('description'):
                        lad[(base, o['description'])][bm['key']][float(o['point'])] = a(o['price'])
        for (sp_, e, bk, mkk, pl, line, lp, fo) in items:
            deltas = [bl[line] - bl[lp] for bl in lad.get((mkk, pl), {}).values() if line in bl and lp in bl]
            if len(deltas) < MIN_REF: continue
            est_over = fo + statistics.median(deltas)
            live, b2 = TH[bk]
            for side in ('Over', 'Under'):
                f = est_over if side == 'Over' else 1 - est_over
                if f >= b2 - 1e-9 and f <= 0.80:
                    out.append((f, bk, f"{e['away_team']}@{e['home_team']}", e['commence_time'], pl, mkk, side, line,
                                round(f * 100, 1), None, f"LINE-SHIFT (paper) from Pinnacle line {lp}, {len(deltas)} ref books"))
    out.sort(key=lambda r: -r[0])
    for r in out:
        print(f"{r[1]} | {r[2]} {r[3]} | {r[4]} {r[5]} {r[6]} {r[7]} | Pinnacle {r[8]}% | FanDuel {r[9]}% | {r[10]}")
    print(f"{len(out)} legs >= 54.5% on either book. DFS legs seen: {sum(n.values())}")


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 72)
