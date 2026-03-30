const { getSharpRules, calcProjection, calcTrueProb, calcRedFlags, cl, r2, avgArr } = require('../lib/engine');

describe('utility functions', () => {
  test('cl clamps values within range', () => {
    expect(cl(5, 1, 10)).toBe(5);
    expect(cl(-1, 0, 10)).toBe(0);
    expect(cl(15, 0, 10)).toBe(10);
  });

  test('r2 rounds to 2 decimal places', () => {
    expect(r2(1.555)).toBe(1.56);
    expect(r2(1.001)).toBe(1);
    expect(r2(3.14159)).toBe(3.14);
  });

  test('avgArr computes average of valid numbers', () => {
    expect(avgArr([2, 4, 6])).toBe(4);
    expect(avgArr([null, 2, null, 4])).toBe(3);
    expect(avgArr([])).toBeNull();
    expect(avgArr([null, undefined])).toBeNull();
  });
});

describe('getSharpRules', () => {
  test('NHL SOG under B2B triggers fatigue rule', () => {
    const rules = getSharpRules({ sp: 'NHL', mkt: 'SOG', side: 'under', b2b: 'yes' });
    expect(rules).toEqual(expect.arrayContaining([
      expect.objectContaining({ boost: 0.03 }),
    ]));
  });

  test('NHL SOG under with low ICF triggers elite under rule', () => {
    const rules = getSharpRules({ sp: 'NHL', mkt: 'SOG', side: 'under', icf: 10 });
    expect(rules.some((r) => r.boost === 0.02 && r.t.includes('ICF/60'))).toBe(true);
  });

  test('NHL SOG over with high ICF triggers over rule', () => {
    const rules = getSharpRules({ sp: 'NHL', mkt: 'SOG', side: 'over', icf: 22 });
    expect(rules.some((r) => r.boost === 0.02)).toBe(true);
  });

  test('NHL SOG under with No PP role', () => {
    const rules = getSharpRules({ sp: 'NHL', mkt: 'SOG', side: 'under', pp: 'None' });
    expect(rules.some((r) => r.boost === 0.01)).toBe(true);
  });

  test('NHL SOG under with elite defense', () => {
    const rules = getSharpRules({ sp: 'NHL', mkt: 'SOG', side: 'under', oppRk: 5 });
    expect(rules.some((r) => r.boost === 0.02 && r.t.includes('defensive'))).toBe(true);
  });

  test('no rules for NHL SOG over with low ICF', () => {
    const rules = getSharpRules({ sp: 'NHL', mkt: 'SOG', side: 'over', icf: 10 });
    expect(rules.length).toBe(0);
  });

  test('NBA fatigue rule triggers with 4+ games in 7 days', () => {
    const rules = getSharpRules({ sp: 'NBA', side: 'under', g7: 4 });
    expect(rules[0].boost).toBe(0.03);
  });

  test('NBA blowout risk under gets positive boost', () => {
    const rules = getSharpRules({ sp: 'NBA', side: 'under', blowout: 8 });
    expect(rules[0].boost).toBe(0.02);
  });

  test('MLB day-after-night K under rule', () => {
    const rules = getSharpRules({ sp: 'MLB', mkt: 'KS', side: 'under', dan: 'yes' });
    expect(rules[0].boost).toBe(0.03);
  });

  test('Tennis same-day turnaround penalizes', () => {
    const rules = getSharpRules({ sp: 'TENNIS', rest: 0 });
    expect(rules[0].boost).toBe(-0.05);
  });

  test('returns empty array when no rules match', () => {
    const rules = getSharpRules({ sp: 'NHL', mkt: 'SOG', side: 'under' });
    expect(rules.length).toBe(0);
  });
});

describe('calcProjection', () => {
  test('NHL SOG projection uses weighted average', () => {
    const pr = calcProjection({ sp: 'NHL', mkt: 'SOG', avg: 2.0, med: 1, sog: 1.5, att: 3.0, pp: 'None', side: 'under' });
    expect(pr).toBeGreaterThan(0);
    expect(pr).toBeLessThan(5);
  });

  test('NHL SOG B2B reduces projection', () => {
    const base = calcProjection({ sp: 'NHL', mkt: 'SOG', avg: 2.0, med: 1, pp: 'None', side: 'under', b2b: 'no' });
    const b2b = calcProjection({ sp: 'NHL', mkt: 'SOG', avg: 2.0, med: 1, pp: 'None', side: 'under', b2b: 'yes' });
    expect(b2b).toBeLessThan(base);
  });

  test('NHL SOG PP1 increases projection', () => {
    const none = calcProjection({ sp: 'NHL', mkt: 'SOG', avg: 2.0, med: 1, pp: 'None', side: 'under' });
    const pp1 = calcProjection({ sp: 'NHL', mkt: 'SOG', avg: 2.0, med: 1, pp: 'PP1', side: 'under' });
    expect(pp1).toBeGreaterThan(none);
  });

  test('NHL SOG high ICF boosts projection', () => {
    const low = calcProjection({ sp: 'NHL', mkt: 'SOG', avg: 2.0, med: 1, pp: 'None', icf: 10 });
    const high = calcProjection({ sp: 'NHL', mkt: 'SOG', avg: 2.0, med: 1, pp: 'None', icf: 22 });
    expect(high).toBeGreaterThan(low);
  });

  test('NBA projection uses avg/med', () => {
    const pr = calcProjection({ sp: 'NBA', avg: 20, med: 19, side: 'under' });
    expect(pr).toBeCloseTo(19.5, 0);
  });

  test('NBA fatigue reduces projection', () => {
    const base = calcProjection({ sp: 'NBA', avg: 20, med: 19, side: 'under' });
    const tired = calcProjection({ sp: 'NBA', avg: 20, med: 19, side: 'under', g7: 5 });
    expect(tired).toBeLessThan(base);
  });

  test('MLB KS projection', () => {
    const pr = calcProjection({ sp: 'MLB', mkt: 'KS', kr: 1.0, inn: 6.0 });
    expect(pr).toBeGreaterThan(0);
  });

  test('Tennis projection is between 0.05 and 0.95', () => {
    const pr = calcProjection({ sp: 'TENNIS', hold: 0.75, t1st: 0.70, tRet: 0.42, tBrk: 0.30, tElo: 1800, tForm: 0.80 });
    expect(pr).toBeGreaterThanOrEqual(0.05);
    expect(pr).toBeLessThanOrEqual(0.95);
  });

  test('projection never goes negative', () => {
    const pr = calcProjection({ sp: 'NHL', mkt: 'SOG', avg: 0.1, med: 0, pp: 'None', b2b: 'yes', icf: 5, bad: true, side: 'under' });
    expect(pr).toBeGreaterThanOrEqual(0);
  });
});

describe('calcTrueProb', () => {
  test('under prob increases when projection is below line', () => {
    const tp = calcTrueProb({ side: 'under', line: 2.5, sp: 'NHL' }, 1.5, 0);
    expect(tp).toBeGreaterThan(0.50);
  });

  test('under prob decreases when projection is above line', () => {
    const tp = calcTrueProb({ side: 'under', line: 1.5, sp: 'NHL' }, 3.0, 0);
    expect(tp).toBeLessThan(0.50);
  });

  test('median below line adds bonus for under', () => {
    const noMed = calcTrueProb({ side: 'under', line: 2.5, sp: 'NHL' }, 2.0, 0);
    const withMed = calcTrueProb({ side: 'under', line: 2.5, sp: 'NHL', med: 1 }, 2.0, 0);
    expect(withMed).toBeGreaterThan(noMed);
  });

  test('boost adds to true probability', () => {
    const noBoost = calcTrueProb({ side: 'under', line: 2.5, sp: 'NHL' }, 2.0, 0);
    const withBoost = calcTrueProb({ side: 'under', line: 2.5, sp: 'NHL' }, 2.0, 0.05);
    expect(withBoost).toBeGreaterThan(noBoost);
  });

  test('red flags reduce probability', () => {
    const clean = calcTrueProb({ side: 'under', line: 2.5, sp: 'NHL' }, 1.5, 0);
    const flagged = calcTrueProb({ side: 'under', line: 2.5, sp: 'NHL', rfRole: true, rfProf: true }, 1.5, 0);
    expect(flagged).toBeLessThan(clean);
  });

  test('result is clamped between 0.05 and 0.95', () => {
    const high = calcTrueProb({ side: 'under', line: 10, sp: 'NHL', med: 0 }, 0.1, 0.5);
    const low = calcTrueProb({ side: 'under', line: 0.5, sp: 'NHL', rfRole: true, rfProf: true, rfInj: true }, 10, -0.5);
    expect(high).toBeLessThanOrEqual(0.95);
    expect(low).toBeGreaterThanOrEqual(0.05);
  });

  test('tennis returns projection + boost directly', () => {
    const tp = calcTrueProb({ sp: 'TENNIS' }, 0.65, 0.02);
    expect(tp).toBeCloseTo(0.67, 2);
  });

  test('returns 0.50 when line is null', () => {
    const tp = calcTrueProb({ side: 'under', line: null, sp: 'NHL' }, 2.0, 0);
    expect(tp).toBe(0.50);
  });
});

describe('calcRedFlags', () => {
  test('no flags = Clean', () => {
    const rf = calcRedFlags({});
    expect(rf.pts).toBe(0);
    expect(rf.lvl).toBe('Clean');
    expect(rf.flags).toHaveLength(0);
  });

  test('single spike = 1pt, still Clean', () => {
    const rf = calcRedFlags({ spike: true });
    expect(rf.pts).toBe(1);
    expect(rf.lvl).toBe('Clean');
  });

  test('unstable minutes = Caution', () => {
    const rf = calcRedFlags({ min: true });
    expect(rf.pts).toBe(2);
    expect(rf.lvl).toBe('Caution');
  });

  test('role change = Severe (3pts)', () => {
    const rf = calcRedFlags({ role: true });
    expect(rf.pts).toBe(3);
    expect(rf.lvl).toBe('Caution');
  });

  test('multiple flags accumulate correctly', () => {
    const rf = calcRedFlags({ role: true, inj: true, spike: true });
    expect(rf.pts).toBe(6); // 3 + 2 + 1
    expect(rf.lvl).toBe('Severe');
    expect(rf.flags).toHaveLength(3);
  });

  test('all flags is maximum severity', () => {
    const rf = calcRedFlags({ spike: true, min: true, role: true, inj: true, prof: true, trap: true, early: true, lineMov: true, juice: true, pub: true });
    expect(rf.pts).toBe(20);
    expect(rf.lvl).toBe('Severe');
    expect(rf.flags).toHaveLength(10);
  });
});
