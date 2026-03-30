/**
 * Odds conversion and probability utilities.
 * Extracted from index.html engine logic.
 */

/** Convert American odds to implied probability (0-1) */
function impliedProb(odds) {
  return odds < 0
    ? Math.abs(odds) / (Math.abs(odds) + 100)
    : 100 / (odds + 100);
}

/** Convert probability (0-1) to American odds */
function toAmerican(p) {
  return p >= 0.5
    ? Math.round(-(p / (1 - p)) * 100)
    : Math.round(((1 - p) / p) * 100);
}

/** Calculate payout from American odds and stake */
function calcPayout(odds, stake) {
  return odds < 0
    ? stake * (100 / Math.abs(odds))
    : stake * (odds / 100);
}

/** Kelly Criterion stake calculation (quarter-Kelly) */
function kellyCalc(trueProb, odds, bankroll) {
  const dec = odds < 0 ? 1 + (100 / Math.abs(odds)) : 1 + (odds / 100);
  const b = dec - 1;
  const q = 1 - trueProb;
  const fk = Math.max(0, (b * trueProb - q) / b);
  const amt = Math.min(fk * 0.25 * bankroll, bankroll * 0.05);
  return { pct: Math.round(fk * 25 * 100) / 100, amt: Math.max(0, amt) };
}

module.exports = { impliedProb, toAmerican, calcPayout, kellyCalc };
