# Timing Gate addendum (2026-10-23, Sergio agreed) - overrides the live/paper split in Sergios_Betts_System.md for NBA and NFL

## Why (evidence, ledger of 451 graded plays with an independent Pinnacle close)
- Pinnacle fair on our side drifted AGAINST the logged edge on 81% of plays (avg -2.2 pts vs avg logged edge +2.7). CLV overall +0.5, beat close 64%.
- Drift grows with lead time. Plays logged 0-3h before start: CLV +2.0, 94% beat close. 6-12h before: CLV +0.4, 65%. NBA 6-12h: CLV -0.8, 46% beat close. NBA 0-3h: CLV +1.9, 90%.
- NFL overall CLV -0.5, 49% beat close (n=70). NHL holds up: CLV +1.2, 74% (n=183).
- Bigger early edges were not more reliable (edge >= 4: avg Pinnacle drift -3.6).
- Open question: how much is Pinnacle moving vs Novig catching up. close_snapshot.py now records novig_close_price / novig_close_cost (commit 29209d3). Review after about a week of snapshots.
- Caveats: early vs late logged plays are different samples (selection); scan and close devig method not verified identical.

## Rules
1. NBA: a play logged more than 4h before start is B2 PAPER only, even if edge >= 3.0 (label 'B2 paper', tier B2, note "timing gate: >4h to start"). Live Track B = edge >= 3.0 AND start within 4h (best within 3h) AND recheck HOLDS.
2. NFL: scans earlier in the week are paper-only for data. A play is live only on game day (Sunday morning recheck, or Thursday/Monday game day) and only if the fresh recheck HOLDS >= 3.0.
3. NHL (Unders live, SOG Overs paper), WNBA: unchanged.
4. Before ANY bet, run a fresh recheck at that moment (edges can vanish within ~40 min, e.g. Sarr Reb O6.5 +4.0 -> -2.1 on 2026-10-23).
5. Early scans (7:41 / 11:41) keep logging everything for data; the report lists live plays only when the timing gate passes. The 15:41 run is the main live window for same-evening NBA.
6. Review: once Novig close data exists for >= 30 early plays, report whether early edges die from Pinnacle drift or Novig catch-up, and whether the gate can be relaxed.
