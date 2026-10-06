"""CLV split by order type (added 2026-10-06, adverse-selection check). Usage:
  python3 collector/clv_by_order_type.py <dir with exported bets/*.json>   (export via ArtifactData list out_dir)
Groups graded docs (clvPinn present) by orderType: maker (resting Make orders) vs taker (instant fills) vs unknown (paper / not recorded),
split into REAL (stake > 0) and PAPER (stake 0). Prints n, avg CLV vs Pinnacle (pts), % beating the close.
If makers show clearly worse CLV than takers at n >= 20 each, resting orders are being adversely selected (fills mostly when news hits):
tighten the Make price (stay within ~3c of the ask) or take instead.
"""
import json, os, sys, glob, collections

def main(d):
    files = glob.glob(os.path.join(d, '**', '*.json'), recursive=True)
    G = collections.defaultdict(list)
    for f in files:
        try: x = json.load(open(f))
        except Exception: continue
        x = x.get('data', x)
        if x.get('clvPinn') is None or 'INVALIDATED' in str(x.get('status', '')).upper(): continue
        kind = 'REAL' if (x.get('stake') or 0) > 0 else 'PAPER'
        ot = x.get('orderType') or 'unknown'
        G[(kind, ot)].append(float(x['clvPinn']))
    print(f"{'group':<18}{'n':>4}{'avg CLV':>9}{'beat close':>12}")
    for k in sorted(G):
        v = G[k]
        print(f"{k[0]+' '+k[1]:<18}{len(v):>4}{sum(v)/len(v):>+9.2f}{sum(c > 0 for c in v)/len(v)*100:>11.0f}%")
    tot = [c for v in G.values() for c in v]
    if tot: print(f"{'ALL':<18}{len(tot):>4}{sum(tot)/len(tot):>+9.2f}{sum(c > 0 for c in tot)/len(tot)*100:>11.0f}%")

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
