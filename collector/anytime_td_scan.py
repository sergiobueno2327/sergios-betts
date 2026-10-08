"""Sergio's Betts -- Anytime-TD UNDER scan for PrizePicks / Underdog (added 2026-10-07, Sergio-approved).
Idea (X post Alex Monahan 2026-10-07): the "Under 0.5 Anytime TDs" leg on PrizePicks is often priced as if the public
clicks Over. The Odds API carries NO PrizePicks/Underdog line for this market, so the DFS side is a manual check: this
scan lists every player whose PINNACLE two-sided anytime-TD price (power de-vig, margin <= 8%) makes the NO (Under 0.5)
side clear the DFS bars. Sergio then confirms in the app that the Under 0.5 leg is posted and at what payout.
  PrizePicks 5/6-leg Flex: LIVE needs Pinnacle fair No >= 57.3%, B2 paper 56.3-57.3.
  Underdog 2-pick Standard 3.5x: LIVE >= 56.5%, B2 55.5-56.5 (legs from different games).
  PROMO partner (PP 2-pick Power 3x, one discounted promo leg est. ~85%): partner break-even = 1/(3 x promo hit rate);
  printed as PARTNER when fair No >= 45% (39.2% break-even + 3 cushion at an 85% promo leg). Different games only.
Everything from this scan is PAPER (track "PP-anytime-TD") until 20 graded legs (hit rate >= fair - 3 pts), then Sergio decides.
Caveat: Pinnacle's anytime-TD market is rushing/receiving TDs (QB passing TDs do not count); confirm the DFS leg's definition.
Also prints Novig No price for the exchange angle. Run: set -a && source .env && set +a && python3 collector/anytime_td_scan.py [hours=72] [sport=nfl|ncaaf|all]
"""
import os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_scan import get, devig_power, american_to_prob as a, MAX_PIN_VIG

KEY = os.environ.get('THEODDSAPI_KEY', '')
if not KEY:
    raise SystemExit('THEODDSAPI_KEY env var is required.')
B = 'https://api.the-odds-api.com/v4'
SPORTS = {'nfl': 'americanfootball_nfl', 'ncaaf': 'americanfootball_ncaaf'}
PP = (0.573, 0.563); UD = (0.565, 0.555); PARTNER = 0.45


def main(hours=72, which='nfl'):
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = []
    for name, sp in SPORTS.items():
        if which not in ('all', name):
            continue
        for e in get(f'{B}/sports/{sp}/events?apiKey={KEY}') or []:
            t = datetime.datetime.fromisoformat(e['commence_time'].replace('Z', '+00:00'))
            if not (now < t < now + datetime.timedelta(hours=hours)):
                continue
            j = get(f"{B}/sports/{sp}/events/{e['id']}/odds?apiKey={KEY}&markets=player_anytime_td&bookmakers=pinnacle,novig&oddsFormat=american") or {}
            bk = {b['key']: {} for b in j.get('bookmakers', [])}
            for b in j.get('bookmakers', []):
                for m in b.get('markets', []):
                    for o in m.get('outcomes', []):
                        bk[b['key']].setdefault(o['description'], {})[o['name']] = o['price']
            nov = bk.get('novig', {})
            for pl, px in bk.get('pinnacle', {}).items():
                if 'Yes' not in px or 'No' not in px:
                    continue
                py, pn = a(px['Yes']), a(px['No'])
                vig = py + pn - 1
                if vig > MAX_PIN_VIG:
                    continue
                f_yes = devig_power(py, pn)
                f_no = 1 - f_yes
                nv = nov.get(pl, {}).get('No')
                rows.append((f_no, pl, f"{e['away_team']} @ {e['home_team']}", t, vig, px['Yes'], px['No'], nv))
    rows.sort(reverse=True)
    def lab(f):
        tags = []
        tags.append('PP-LIVE' if f >= PP[0] else 'PP-B2' if f >= PP[1] else '')
        tags.append('UD-LIVE' if f >= UD[0] else 'UD-B2' if f >= UD[1] else '')
        if f >= PARTNER: tags.append('PARTNER')
        return ' '.join(x for x in tags if x)
    print(f'Anytime-TD UNDER 0.5 (Pinnacle fair No, margin<={MAX_PIN_VIG:.0%}) -- PAPER ONLY, confirm leg is posted in app')
    n = 0
    for f_no, pl, g, t, vig, y, no, nv in rows:
        L = lab(f_no)
        if not L: continue
        n += 1
        print(f"{f_no*100:5.1f}%  {pl:24s} {g:42s} {t:%a %H:%MZ} Pin Y{y:+d}/N{no:+d} vig {vig*100:.1f}%  Novig No {('%+d' % nv) if nv else '--'}  [{L}]")
    print(f'{n} players clear a bar of {len(rows)} priced')


if __name__ == '__main__':
    h = int(sys.argv[1]) if len(sys.argv) > 1 else 72
    w = sys.argv[2] if len(sys.argv) > 2 else 'nfl'
    main(h, w)
