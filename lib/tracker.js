/**
 * Bet tracker logic.
 * Extracted from tracker.html for testability.
 */

const { calcPayout } = require('./odds');

/** Calculate streak from an ordered array of bet results */
function calcStreak(bets) {
  let streak = 0;
  for (const b of bets) {
    if (b.result === 'pending') continue;
    if (b.result === 'win') {
      if (streak >= 0) streak++;
      else break;
    } else if (b.result === 'loss') {
      if (streak <= 0) streak--;
      else break;
    } else break;
  }
  return streak;
}

/** Calculate win rate from wins and losses */
function calcWinRate(wins, losses) {
  const total = wins + losses;
  return total > 0 ? Math.round((wins / total) * 1000) / 10 : 0;
}

/** Calculate ROI from settled bets */
function calcROI(bets) {
  const settled = bets.filter((b) => b.result !== 'pending');
  const totalStaked = settled.reduce((s, b) => s + b.stake, 0);
  const totalProfit = settled.reduce((s, b) => s + b.profit, 0);
  return totalStaked > 0 ? Math.round((totalProfit / totalStaked) * 1000) / 10 : 0;
}

/** Compute bankroll after marking a bet result */
function applyBetResult(bet, result, bankroll) {
  let newBankroll = bankroll;

  // Reverse previous result if re-marking
  if (bet.result === 'win') newBankroll -= (bet.stake + bet.profit);
  else if (bet.result === 'push') newBankroll -= bet.stake;

  let profit = 0;
  if (result === 'win') {
    profit = Math.round(calcPayout(bet.odds, bet.stake) * 100) / 100;
    newBankroll += bet.stake + profit;
  } else if (result === 'loss') {
    profit = -bet.stake;
  } else if (result === 'push') {
    profit = 0;
    newBankroll += bet.stake;
  }

  return { bankroll: newBankroll, profit };
}

module.exports = { calcStreak, calcWinRate, calcROI, applyBetResult };
