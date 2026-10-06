"""Reference-only books (NOT execution venues, NOT bars). Added 2026-10-05.

Fetches a second sharp-ish book's TWO-SIDED prop prices from The Odds API and power-de-vigs them,
so scans can attach alt_fairs to plays for consensus_flag.py. One-sided lines are skipped (can't be
de-vigged without assuming a vig, which overstates edge). FanDuel is the default reference book.
Pinnacle still anchors fair and every bar; this only feeds the non-blocking consensus INFO flag.
"""
import os, collections
from nfl_scan import get, nrm, devig_power, american_to_prob

KEY = os.environ.get('THEODDSAPI_KEY', '')
REF_BOOKS = tuple(b for b in os.environ.get('BETTS_REF_BOOKS', 'fanduel').split(',') if b)
MAX_REF_VIG = 0.14  # skip absurdly wide reference markets


def ref_fairs(sport_key, event_id, market_keys, books=REF_BOOKS):
    """{(market_key, norm_name, line): {book: fair_over_prob}} for two-sided Over/Under lines only."""
    if not event_id or not market_keys or not books:
        return {}
    url = (f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/odds"
           f"?apiKey={KEY}&bookmakers={','.join(books)}&markets={','.join(market_keys)}&oddsFormat=american")
    j = get(url) or {}
    out = {}
    for bm in j.get('bookmakers', []):
        for mkt in bm.get('markets', []):
            by_pl = collections.defaultdict(dict)
            for o in mkt.get('outcomes', []):
                if o.get('price') is None or o.get('point') is None or not o.get('description'):
                    continue
                by_pl[(nrm(o['description']), float(o['point']))][o['name']] = o['price']
            for (nn, line), sides in by_pl.items():
                if 'Over' not in sides or 'Under' not in sides:
                    continue
                po, pu = american_to_prob(sides['Over']), american_to_prob(sides['Under'])
                if po is None or pu is None or po + pu - 1 > MAX_REF_VIG:
                    continue
                out.setdefault((mkt['key'], nn, line), {})[bm['key']] = devig_power(po, pu)
    return out
