/**
 * CSV parsing and player matching logic.
 * Extracted from api/mp.js for testability.
 */

/** Parse MoneyPuck CSV text and find player stats */
function parsePlayerFromCSV(text, playerName, team) {
  const rows = text.split('\n');
  const hdrs = rows[0].split(',').map((h) => h.trim().replace(/"/g, ''));
  const col = (n) => hdrs.indexOf(n);
  const nc = col('name'), ic = col('I_F_shotAttempts'), xc = col('I_F_xGoals');
  const tc = col('icetime'), gc = col('games_played'), sc = col('situation');

  const lnLow = playerName.toLowerCase().split(' ').pop();
  const fnFirst = playerName.toLowerCase().charAt(0);

  for (const row of rows.slice(1)) {
    const c = row.split(',');
    if (!c[nc]) continue;
    const rn = (c[nc] || '').toLowerCase().replace(/"/g, '');
    const sit = (c[sc] || '').toLowerCase().replace(/"/g, '');
    if (sit !== 'all') continue;
    if (rn.includes(lnLow) && rn.includes(fnFirst)) {
      const toi = parseFloat(c[tc]) || 1;
      const att = parseFloat(c[ic]) || 0;
      const xg = parseFloat(c[xc]) || 0;
      const gp = parseFloat(c[gc]) || 1;
      return {
        name: c[nc],
        icf60: Math.round(att / toi * 60 * 10) / 10,
        xsog: Math.round(xg / gp * 10) / 10,
        attPerGame: Math.round(att / gp * 10) / 10,
        gamesPlayed: Math.round(gp),
      };
    }
  }
  return null;
}

/** Parse TOI string "MM:SS" to seconds */
function parseTOI(toi) {
  if (!toi || toi === '00:00' || toi === '0:00') return 0;
  const pts = toi.split(':');
  return parseInt(pts[0]) * 60 + parseInt(pts[1]);
}

/** Determine power play role from PP TOI per game (seconds) */
function getPPRole(ppToiPerGame) {
  if (ppToiPerGame >= 120) return 'PP1';
  if (ppToiPerGame >= 30) return 'PP2';
  return 'None';
}

/** Check if injury keywords match in text */
function hasInjuryFlag(text) {
  return /injur|day-to-day|scratch|out|miss/i.test(text);
}

module.exports = { parsePlayerFromCSV, parseTOI, getPPRole, hasInjuryFlag };
