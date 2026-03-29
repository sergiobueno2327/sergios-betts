const { impliedProb, toAmerican, calcPayout, kellyCalc } = require('../lib/odds');

describe('impliedProb', () => {
  test('converts -110 to ~52.4%', () => {
    expect(impliedProb(-110)).toBeCloseTo(0.524, 2);
  });

  test('converts +100 to 50%', () => {
    expect(impliedProb(100)).toBeCloseTo(0.50, 2);
  });

  test('converts -200 to ~66.7%', () => {
    expect(impliedProb(-200)).toBeCloseTo(0.667, 2);
  });

  test('converts +200 to ~33.3%', () => {
    expect(impliedProb(200)).toBeCloseTo(0.333, 2);
  });

  test('converts -150 to 60%', () => {
    expect(impliedProb(-150)).toBeCloseTo(0.60, 2);
  });

  test('converts +150 to 40%', () => {
    expect(impliedProb(150)).toBeCloseTo(0.40, 2);
  });
});

describe('toAmerican', () => {
  test('converts 50% to -100', () => {
    expect(toAmerican(0.50)).toBe(-100);
  });

  test('converts ~66.7% to approximately -200', () => {
    const result = toAmerican(2 / 3);
    expect(result).toBe(-200);
  });

  test('converts 40% to +150', () => {
    expect(toAmerican(0.40)).toBe(150);
  });

  test('converts 80% to -400', () => {
    expect(toAmerican(0.80)).toBe(-400);
  });

  test('converts 25% to +300', () => {
    expect(toAmerican(0.25)).toBe(300);
  });
});

describe('calcPayout', () => {
  test('negative odds: -110 with $100 stake pays ~$90.91', () => {
    expect(calcPayout(-110, 100)).toBeCloseTo(90.91, 1);
  });

  test('positive odds: +150 with $100 stake pays $150', () => {
    expect(calcPayout(150, 100)).toBe(150);
  });

  test('-200 with $50 stake pays $25', () => {
    expect(calcPayout(-200, 50)).toBe(25);
  });

  test('+100 with $100 stake pays $100 (even money)', () => {
    expect(calcPayout(100, 100)).toBe(100);
  });

  test('zero stake returns zero', () => {
    expect(calcPayout(-110, 0)).toBe(0);
  });
});

describe('kellyCalc', () => {
  test('returns zero stake when no edge exists', () => {
    // implied prob of -110 is ~52.4%, if true prob is 52.4% there is no edge
    const result = kellyCalc(0.524, -110, 1000);
    expect(result.amt).toBeCloseTo(0, 0);
  });

  test('returns positive stake when edge exists', () => {
    // true prob 60% at -110 odds should suggest a bet
    const result = kellyCalc(0.60, -110, 1000);
    expect(result.amt).toBeGreaterThan(0);
    expect(result.pct).toBeGreaterThan(0);
  });

  test('stake never exceeds 5% of bankroll', () => {
    // Even with massive edge, quarter-kelly should cap at 5%
    const result = kellyCalc(0.95, 100, 1000);
    expect(result.amt).toBeLessThanOrEqual(50);
  });

  test('returns zero for negative edge', () => {
    // true prob 40% at -110 (implied ~52.4%) = negative edge
    const result = kellyCalc(0.40, -110, 1000);
    expect(result.amt).toBe(0);
  });

  test('works with positive odds', () => {
    const result = kellyCalc(0.50, 150, 1000);
    expect(result.amt).toBeGreaterThan(0);
  });
});
