"""Over vs Under CLV report (added 2026-10-14). Usage: python3 side_report.py LEDGER_BETS_DIR
Splits graded plays (excl. duplicates) by Over/Under and by sport; shows all vs verified-close-only
(closeIndependent). Verified-close numbers are the ones that count for the all-paper gate."""
import sys, glob, json, statistics as S

def side(d):
    m = (d.get('market') or '').lower(); s = d.get('side')
    if 'under' in m: return 'U' if s == 'YES' else 'O'
    if 'over' in m: return 'O' if s == 'YES' else 'U'
    return 'U' if s == 'NO' else 'O'

def line(name, L):
    if not L: return
    c = [float(d['clvPinn']) for d in L]
    v = [float(d['clvPinn']) for d in L if d.get('closeIndependent')]
    print(f"{name:10s} all n={len(c):3d} avg={S.mean(c):+.2f} beat={100*sum(x>0 for x in c)/len(c):.0f}% | "
          f"VERIFIED n={len(v):3d} avg={(S.mean(v) if v else 0):+.2f} beat={100*sum(x>0 for x in v)/max(1,len(v)):.0f}%")

def main(ledger):
    D = []
    for f in glob.glob(ledger + '/*.json'):
        d = json.load(open(f)); d = d.get('data', d)
        if d.get('graded') and d.get('clvPinn') is not None and not d.get('duplicate'):
            D.append(d)
    print('OVER vs UNDER CLV (graded, no duplicates)')
    for sp in ['ALL'] + sorted({d['sport'] for d in D}):
        for sd, n in (('O', 'Over'), ('U', 'Under')):
            line(f'{sp} {n}', [d for d in D if (sp == 'ALL' or d['sport'] == sp) and side(d) == sd])

if __name__ == '__main__':
    main(sys.argv[1])
