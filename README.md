# Sergio's Betts — odds history (data branch)

Snapshots of Pinnacle (via Pinnapi) and Kalshi prices, saved by a scheduled task ~8×/day.
Used to build our own open-to-close history for backtests and CLV grading.

- Collector: `collector/collect_odds.py` (no API keys stored; pass `PINNAPI_KEY` env var)
- Data: `data/YYYY-MM-DD/HHMMZ_{pinnacle,kalshi}.jsonl.gz` (UTC)
- Run log: `data/log.txt`
- Deploys are disabled on this branch (`vercel.json`).
