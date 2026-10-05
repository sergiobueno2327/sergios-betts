"""Pinnacle fair-price history for the 'pin_move' signal (added 2026-10-04, paper-tracked only).

Every scan run records Pinnacle's no-vig fair Over prob for each prop it sees. move() returns how many
probability points the fair Over price has shifted since the earliest record in the last 24h
(positive = Pinnacle moved toward Over). Signal does NOT affect what gets logged. Review at ~50 graded
plays: do plays where Pinnacle moved toward our side beat the close more often?
History is sparse (only when a scan runs); None means no prior record. File: data/pin_fair_history.jsonl
"""
import json, os, time

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pin_fair_history.jsonl')
_cache = None

def _load():
    global _cache
    if _cache is None:
        _cache = {}
        if os.path.exists(PATH):
            for ln in open(PATH):
                try:
                    r = json.loads(ln)
                    _cache.setdefault(r['k'], []).append((r['t'], r['f']))
                except Exception:
                    pass
    return _cache

def key(sport, game, player, stat, line):
    return f"{sport}|{game}|{player}|{stat}|{line}"

def move(k, fair_over, now=None):
    """Points moved toward Over since earliest record within 24h, or None."""
    now = now or time.time()
    prior = [(t, f) for t, f in _load().get(k, []) if now - 86400 <= t < now - 600]
    if not prior:
        return None
    return round((fair_over - min(prior)[1]) * 100, 1)

def record(k, fair_over, now=None):
    now = now or time.time()
    h = _load().setdefault(k, [])
    if h and now - h[-1][0] < 600:
        return
    h.append((now, fair_over))
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, 'a') as f:
        f.write(json.dumps(dict(k=k, t=now, f=round(fair_over, 4))) + '\n')
