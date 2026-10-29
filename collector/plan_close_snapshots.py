"""Close-snapshot planner (added 2026-10-18). Fixes the gap where one-shot close-snapshot tasks only existed for start times known when they were created
(e.g. the 3:10 PM PT NHL games had no snapshot on 2026-10-18, so those plays have no verified close).
Usage: python3 plan_close_snapshots.py LEDGER_BETS_DIR [--hours 36] [--json out.json]
Lists every DISTINCT start time of an unstarted, ungraded logged play (real, paper, B2 alike) within --hours, and the one-shot fire time = start - 5 min (was 8 until 2026-10-28).
Each line is a task named 'Close snapshot <fire date> <HH:MMZ> (<label>)'. The scheduled scan (and any chat session) then creates a one-shot scheduled task
for every line that has no existing task of that name (list_triggers), copying the standard close-snapshot prompt. Starts within 12 min of an existing fire time
are covered by close_snapshot.py's 15-minute window, so they are merged into the earlier fire time (tolerance --merge 10 min)."""
import os, sys, json, glob, datetime

UTC = datetime.timezone.utc
PT = datetime.timezone(datetime.timedelta(hours=-7))
t = lambda s: datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))


def plan(ledger, hours=36, merge_min=1, lead_min=5):  # 2026-10-28 (Sergio: 'six minutes is a lot'): fire at start-5 (snapshot lands ~3-4 min out), one task per distinct start
    now = datetime.datetime.now(UTC)
    starts = {}
    for fn in glob.glob(os.path.join(ledger, '*.json')):
        d = json.load(open(fn)); d = d.get('data', d)
        if d.get('graded') or d.get('filled') is False and float(d.get('stake') or 0) > 0 or not d.get('start'):
            continue
        s = t(d['start'])
        if not (now < s <= now + datetime.timedelta(hours=hours)):
            continue
        starts.setdefault(s.replace(second=0, microsecond=0), []).append(d.get('player'))
    out, last = [], None
    for s in sorted(starts):
        n = len(starts[s])
        if last and (s - last['start']).total_seconds() / 60 <= merge_min:
            last['n'] += n; last['covers'].append(s.strftime('%H:%MZ')); continue  # fire at the earliest start: later ones are within the 15-min window? only if <= 10 min apart after -8
        fire = s - datetime.timedelta(minutes=lead_min)
        last = dict(start=s, fire_at=fire.strftime('%Y-%m-%dT%H:%M:00Z'), n=n, covers=[s.strftime('%H:%MZ')],
                    name=f"Close snapshot {fire:%Y-%m-%d %H:%MZ} ({s.astimezone(PT):%a %-I:%M %p} PT start)",
                    late=fire <= now + datetime.timedelta(minutes=2))
        out.append(last)
    return out


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    a = sys.argv
    res = plan(a[1], float(a[a.index('--hours') + 1]) if '--hours' in a else 36)
    for r in res:
        print(f"{'LATE ' if r['late'] else ''}{r['fire_at']}  {r['n']:3d} play(s)  covers {','.join(r['covers'])}  -> {r['name']}")
    if not res:
        print('no unstarted logged plays in window')
    if '--json' in a:
        json.dump([{k: (v.isoformat() if k == 'start' else v) for k, v in r.items()} for r in res], open(a[a.index('--json') + 1], 'w'))
