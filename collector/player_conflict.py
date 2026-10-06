"""One-market-per-player / no-opposite-sides guard. Added 2026-10-05 (Juwan Johnson was logged Under and Over in one game).

resolve(plays, ledger_dir=None) -> plays with CONFLICTS REMOVED:
  * opposite directions on the same player + stat family (receiving, rushing, passing, shots, ...) in one game:
    keep the higher-edge play, drop the rest, print a CONFLICT line.
  * with ledger_dir (or env BETTS_LEDGER_DIR = an ArtifactData 'bets' export folder): a new play opposite to an
    OPEN (ungraded, not invalidated) ledger play on the same player + family + game date is dropped too.
  * same direction on several markets for one player: kept, printed as INFO (live staking still max one market/player/game).
"""
import os, re, glob, json, unicodedata

def _n(s): return re.sub(r'[^a-z]', '', unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower())

def family(text):
    t = _n(text)
    if 'rec' in t or 'catch' in t: return 'receiving'
    if 'rush' in t: return 'rushing'
    if any(k in t for k in ('pass', 'comp', 'interception', 'attempts')): return 'passing'
    if 'shot' in t or 'sog' in t: return 'shots'
    if 'assist' in t: return 'assists'
    if 'point' in t: return 'points'
    if 'rebound' in t: return 'rebounds'
    return t or 'other'

def direction(side):
    s = str(side or '').strip().lower()
    return 1 if s in ('over', 'yes', 'o') else (-1 if s in ('under', 'no', 'u') else 0)

def _key(p):
    return (_n(p.get('player')), family(p.get('stat') or p.get('market')))

def resolve(plays, ledger_dir=None):
    ledger_dir = ledger_dir or os.environ.get('BETTS_LEDGER_DIR')
    keep = list(plays)
    drop = set()
    groups = {}
    for i, p in enumerate(plays):
        groups.setdefault((_n(p.get('game')),) + _key(p), []).append(i)
    for k, idx in groups.items():
        dirs = {direction(plays[i].get('side')) for i in idx}
        if 1 in dirs and -1 in dirs:
            best = max(idx, key=lambda i: plays[i].get('edge', 0))
            bd = direction(plays[best].get('side'))
            for i in idx:
                if direction(plays[i].get('side')) != bd:
                    drop.add(i)
                    print(f"  CONFLICT dropped (opposite side, same player/family in one game): {plays[i].get('player')} "
                          f"{plays[i].get('stat') or plays[i].get('market')} {plays[i].get('side')} edge {plays[i].get('edge')} "
                          f"-- kept {plays[best].get('side')} edge {plays[best].get('edge')}")
    if ledger_dir:
        led = []
        for f in glob.glob(os.path.join(ledger_dir, '*.json')):
            try: d = json.load(open(f))
            except Exception: continue
            if d.get('graded') or d.get('result') or 'INVALIDATED' in str(d.get('status', '')) or 'cancel' in str(d.get('status', '')).lower(): continue
            led.append(d)
        for i, p in enumerate(plays):
            if i in drop: continue
            for d in led:
                if (_n(d.get('player')) == _n(p.get('player')) and family(d.get('market')) == family(p.get('stat') or p.get('market'))
                        and str(d.get('date')) == str(p.get('date')) and direction(d.get('side')) * direction(p.get('side')) < 0):
                    drop.add(i)
                    print(f"  CONFLICT dropped (opposes open ledger play {d.get('market')} {d.get('side')}): {p.get('player')} {p.get('stat') or p.get('market')} {p.get('side')}")
                    break
    keep = [p for i, p in enumerate(plays) if i not in drop]
    multi = {}
    for p in keep:
        multi.setdefault((_n(p.get('game')), _n(p.get('player'))), []).append(p)
    for (g, pl), ps in multi.items():
        if len(ps) > 1:
            print(f"  INFO multiple markets for {ps[0].get('player')} in one game ({len(ps)}): live staking = best ONE only (max one market/player/game).")
    return keep

if __name__ == '__main__':
    t = [dict(game='ATL@NO', player='Juwan Johnson', stat='receptions', side='Under', edge=5.0, date='2026-10-05'),
         dict(game='ATL@NO', player='Juwan Johnson', stat='receiving_yards', side='Over', edge=3.1, date='2026-10-05'),
         dict(game='ATL@NO', player='Alvin Kamara', stat='receptions', side='Over', edge=3.2, date='2026-10-05'),
         dict(game='ATL@NO', player='Alvin Kamara', stat='receiving_yards', side='Over', edge=3.0, date='2026-10-05')]
    out = resolve(t)
    assert [p['edge'] for p in out] == [5.0, 3.2, 3.0], out
    print('selftest ok')
