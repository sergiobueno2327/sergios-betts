const { calcStreak, calcWinRate, calcROI, applyBetResult } = require('../lib/tracker');

describe('calcStreak', () => {
  test('returns 0 for empty array', () => {
    expect(calcStreak([])).toBe(0);
  });

  test('counts consecutive wins', () => {
    const bets = [
      { result: 'win' },
      { result: 'win' },
      { result: 'win' },
      { result: 'loss' },
    ];
    expect(calcStreak(bets)).toBe(3);
  });

  test('counts consecutive losses as negative', () => {
    const bets = [
      { result: 'loss' },
      { result: 'loss' },
      { result: 'win' },
    ];
    expect(calcStreak(bets)).toBe(-2);
  });

  test('skips pending bets', () => {
    const bets = [
      { result: 'pending' },
      { result: 'pending' },
      { result: 'win' },
      { result: 'win' },
      { result: 'loss' },
    ];
    expect(calcStreak(bets)).toBe(2);
  });

  test('push breaks streak', () => {
    const bets = [
      { result: 'win' },
      { result: 'push' },
      { result: 'win' },
    ];
    expect(calcStreak(bets)).toBe(1);
  });

  test('all pending returns 0', () => {
    expect(calcStreak([{ result: 'pending' }, { result: 'pending' }])).toBe(0);
  });
});

describe('calcWinRate', () => {
  test('72.4% for 21W-8L', () => {
    expect(calcWinRate(21, 8)).toBeCloseTo(72.4, 0);
  });

  test('0% for 0W-5L', () => {
    expect(calcWinRate(0, 5)).toBe(0);
  });

  test('100% for 5W-0L', () => {
    expect(calcWinRate(5, 0)).toBe(100);
  });

  test('0 for no bets', () => {
    expect(calcWinRate(0, 0)).toBe(0);
  });
});

describe('calcROI', () => {
  test('positive ROI on winning bets', () => {
    const bets = [
      { result: 'win', stake: 100, profit: 90.91 },
      { result: 'win', stake: 100, profit: 90.91 },
    ];
    expect(calcROI(bets)).toBeGreaterThan(0);
  });

  test('negative ROI on losing bets', () => {
    const bets = [
      { result: 'loss', stake: 100, profit: -100 },
      { result: 'loss', stake: 100, profit: -100 },
    ];
    expect(calcROI(bets)).toBeLessThan(0);
  });

  test('ignores pending bets', () => {
    const bets = [
      { result: 'pending', stake: 100, profit: 0 },
      { result: 'win', stake: 100, profit: 50 },
    ];
    expect(calcROI(bets)).toBeCloseTo(50, 0);
  });

  test('0 ROI for no settled bets', () => {
    expect(calcROI([{ result: 'pending', stake: 100, profit: 0 }])).toBe(0);
  });
});

describe('applyBetResult', () => {
  test('win adds stake + payout to bankroll', () => {
    const bet = { result: 'pending', odds: -110, stake: 100, profit: 0 };
    const { bankroll, profit } = applyBetResult(bet, 'win', 1000);
    expect(profit).toBeCloseTo(90.91, 1);
    expect(bankroll).toBeCloseTo(1190.91, 1);
  });

  test('loss does not change bankroll (already deducted)', () => {
    const bet = { result: 'pending', odds: -110, stake: 100, profit: 0 };
    const { bankroll, profit } = applyBetResult(bet, 'loss', 900);
    expect(profit).toBe(-100);
    expect(bankroll).toBe(900);
  });

  test('push returns stake', () => {
    const bet = { result: 'pending', odds: -110, stake: 100, profit: 0 };
    const { bankroll, profit } = applyBetResult(bet, 'push', 900);
    expect(profit).toBe(0);
    expect(bankroll).toBe(1000);
  });

  test('re-marking win to loss reverses correctly', () => {
    const bet = { result: 'win', odds: -110, stake: 100, profit: 90.91 };
    const { bankroll, profit } = applyBetResult(bet, 'loss', 1190.91);
    // Reverses the win first (1190.91 - 190.91 = 1000), then loss = no change
    expect(profit).toBe(-100);
    expect(bankroll).toBeCloseTo(1000, 0);
  });

  test('positive odds payout', () => {
    const bet = { result: 'pending', odds: 150, stake: 100, profit: 0 };
    const { bankroll, profit } = applyBetResult(bet, 'win', 900);
    expect(profit).toBe(150);
    expect(bankroll).toBe(1150);
  });
});
