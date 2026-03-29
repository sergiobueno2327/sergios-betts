/**
 * Core betting engine logic.
 * Extracted from index.html for testability.
 */

const cl = (v, a, b) => Math.max(a, Math.min(b, v));
const r2 = (x) => Math.round(x * 100) / 100;
const avgArr = (a) => {
  const v = a.filter((x) => x != null && !isNaN(x));
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

/** Detect sharp betting rules and return boosts */
function getSharpRules(p) {
  const r = [];
  if (p.sp === 'NHL' && p.mkt === 'SOG') {
    if (p.b2b === 'yes' && p.side === 'under') r.push({ t: 'B2B + SOG Under -- fatigue cuts shot volume', boost: 0.03 });
    if (p.icf != null && p.icf <= 11 && p.side === 'under') r.push({ t: 'ICF/60 ≤ 11 -- elite under profile', boost: 0.02 });
    if (p.icf != null && p.icf >= 20 && p.side === 'over') r.push({ t: 'ICF/60 ≥ 20 -- elite over profile', boost: 0.02 });
    if (p.toi != null && p.toi <= 14 && p.side === 'under') r.push({ t: 'TOI ≤ 14 min -- limited ice time', boost: 0.02 });
    if (p.pp === 'None' && p.side === 'under') r.push({ t: 'No PP role -- zero power play shots', boost: 0.01 });
    if (p.oppRk != null && p.oppRk <= 8 && p.side === 'under') r.push({ t: 'Top-8 defensive opponent', boost: 0.02 });
  }
  if (p.sp === 'NBA') {
    if (p.g7 != null && p.g7 >= 4) r.push({ t: '4+ games in 7 days -- fatigue factor', boost: p.side === 'under' ? 0.03 : -0.02 });
    if (p.blowout != null && p.blowout >= 7) r.push({ t: 'High blowout risk', boost: p.side === 'under' ? 0.02 : -0.03 });
  }
  if (p.sp === 'MLB') {
    if (p.dan === 'yes' && p.mkt === 'KS' && p.side === 'under') r.push({ t: 'Day after night game -- K under boosted', boost: 0.03 });
    if (p.leash != null && p.leash <= 80 && p.mkt === 'OUTS' && p.side === 'under') r.push({ t: 'Short leash -- outs under confirmed', boost: 0.04 });
  }
  if (p.sp === 'TENNIS') {
    if (p.rest != null && p.rest === 0) r.push({ t: 'Same-day turnaround -- severe fatigue', boost: -0.05 });
    if (p.tForm != null && p.tForm >= 0.80) r.push({ t: 'Hot form -- 80%+ win rate L5', boost: 0.02 });
  }
  return r;
}

/** Calculate projected stat output */
function calcProjection(p) {
  if (p.sp === 'NHL' && p.mkt === 'SOG') {
    let pts = [];
    if (p.avg != null) pts.push(p.avg * 0.40);
    if (p.med != null) pts.push(p.med * 0.25);
    if (p.sog != null) pts.push(p.sog * 0.20);
    if (p.att != null && p.sog != null && p.att > 0) {
      const c = cl(p.sog / p.att, 0.25, 0.75);
      pts.push(p.att * c * 0.15);
    }
    let pr = pts.length ? pts.reduce((a, b) => a + b, 0) : avgArr([p.avg, p.med]) || 1;
    if (p.xsog != null && p.sog != null) pr += (p.xsog - p.sog) * 0.3;
    if (p.pp === 'PP1') pr += 0.15;
    else if (p.pp === 'PP2') pr += 0.05;
    if (p.icf != null) {
      if (p.icf >= 20) pr += 0.20;
      else if (p.icf >= 17) pr += 0.10;
      else if (p.icf <= 11) pr -= 0.15;
      else if (p.icf <= 13) pr -= 0.05;
    }
    if (p.b2b === 'yes') pr -= 0.12;
    if (p.good) pr += p.side === 'under' ? -0.10 : 0.10;
    if (p.bad) pr += p.side === 'under' ? 0.10 : -0.10;
    return Math.max(0, r2(pr));
  }
  if (p.sp === 'NBA') {
    let pr = avgArr([p.avg, p.med]) || 5;
    if (p.nmin != null) { if (p.nmin >= 36) pr += 0.2; else if (p.nmin <= 28) pr -= 0.2; }
    if (p.g7 != null && p.g7 >= 4) pr *= 0.93;
    if (p.blowout != null && p.blowout >= 7) pr *= 0.90;
    if (p.good) pr += p.side === 'over' ? 0.15 : -0.15;
    if (p.bad) pr -= p.side === 'over' ? 0.15 : -0.15;
    return Math.max(0, r2(pr));
  }
  if (p.sp === 'MLB') {
    const inn = p.inn || 5.8;
    if (p.mkt === 'KS') {
      let k = p.kr != null ? p.kr * 1.05 : 0.95;
      if (p.okr != null) k *= (0.90 + p.okr);
      let pr = inn * k;
      if (p.leash != null && p.leash <= 80) pr *= 0.85;
      if (p.dan === 'yes') pr *= 0.92;
      return Math.max(0, r2(pr));
    }
    if (p.mkt === 'ER') {
      let b = 0.35;
      if (p.whip != null) b += (p.whip - 1.10) * 0.08;
      if (p.park != null) b += (p.park - 1.0) * 0.08;
      return Math.max(0, r2(inn * b));
    }
    if (p.mkt === 'OUTS') return Math.max(0, r2(inn * 3 * (p.leash != null && p.leash <= 80 ? 0.82 : 1)));
    return r2(avgArr([p.avg, p.med]) || 3);
  }
  if (p.sp === 'TENNIS') {
    let sv2 = 0, ret = 0;
    if (p.hold != null) sv2 += (p.hold - 0.65) * 0.4;
    if (p.t1st != null) sv2 += (p.t1st - 0.64) * 0.3;
    if (p.tRet != null) ret += (p.tRet - 0.38) * 0.5;
    if (p.tBrk != null) ret += (p.tBrk - 0.25) * 0.4;
    let fat = 0;
    if (p.tSets != null && p.tSets >= 5) fat -= 0.04;
    if (p.rest != null && p.rest === 0) fat -= 0.06;
    if (p.tMtch != null && p.tMtch >= 3) fat -= 0.03;
    let form = p.tForm != null ? (p.tForm - 0.50) * 0.15 : 0;
    let elo = p.tElo != null ? (p.tElo - 1500) * 0.00008 : 0;
    return cl(0.50 + (sv2 * 0.30) + (ret * 0.35) + fat + form + elo, 0.05, 0.95);
  }
  return r2(avgArr([p.avg, p.med]) || 1);
}

/** Calculate true probability from projection + boosts */
function calcTrueProb(p, pr, boost) {
  if (p.sp === 'TENNIS') return cl(pr + boost, 0.05, 0.95);
  if (p.line == null) return 0.50;
  const g = pr - p.line;
  let b = p.side === 'under' ? 0.50 + (-g * 0.18) : 0.50 + (g * 0.18);
  if (p.med != null) {
    if ((p.side === 'under' && p.med < p.line) || (p.side === 'over' && p.med > p.line)) b += 0.05;
    else b -= 0.02;
  }
  const tv = [p.l5, p.l10, p.l20].filter((v) => v != null);
  if (tv.length) {
    const ta = avgArr(tv);
    b = (b * 0.75) + (ta * 0.25);
  }
  if (p.good) b += 0.03;
  if (p.bad) b -= 0.04;
  if (p.rfSpike) b -= 0.02;
  if (p.rfRole) b -= 0.05;
  if (p.rfProf) b -= 0.06;
  if (p.rfInj) b -= 0.03;
  return cl(b + boost, 0.05, 0.95);
}

/** Calculate red flag score */
function calcRedFlags(flags) {
  let pts = 0;
  const f = [];
  if (flags.spike)   { pts += 1; f.push({ t: 'Recent spike game', c: 'warn' }); }
  if (flags.min)     { pts += 2; f.push({ t: 'Unstable minutes', c: 'warn' }); }
  if (flags.role)    { pts += 3; f.push({ t: 'Role change', c: 'red' }); }
  if (flags.inj)     { pts += 2; f.push({ t: 'Injury uncertainty', c: 'warn' }); }
  if (flags.prof)    { pts += 3; f.push({ t: 'Profile conflicts with side', c: 'red' }); }
  if (flags.trap)    { pts += 2; f.push({ t: 'Trap line signal', c: 'warn' }); }
  if (flags.early)   { pts += 2; f.push({ t: 'Early season sample', c: 'warn' }); }
  if (flags.lineMov) { pts += 2; f.push({ t: 'Line moving against us', c: 'red' }); }
  if (flags.juice)   { pts += 2; f.push({ t: 'Suspicious juice', c: 'warn' }); }
  if (flags.pub)     { pts += 1; f.push({ t: 'Public bias risk', c: 'warn' }); }
  return { pts, lvl: pts <= 1 ? 'Clean' : pts <= 3 ? 'Caution' : 'Severe', flags: f };
}

/** Calculate multi-factor score breakdown */
function calcScores(p, pr, tp, ip, rf) {
  const { impliedProb: _ip } = require('./odds');
  let opp = 5.0;
  if (p.line != null) {
    const g = pr - p.line;
    opp += p.side === 'under' ? cl(-g * 2.5, -2.5, 3.0) : cl(g * 2.5, -2.5, 3.0);
  }
  if (p.sp === 'NHL' && p.icf != null) {
    if (p.side === 'under') {
      if (p.icf <= 11) opp += 1.5;
      else if (p.icf <= 13) opp += 0.8;
      else if (p.icf >= 20) opp -= 1.5;
      else if (p.icf >= 17) opp -= 0.8;
    } else {
      if (p.icf >= 20) opp += 1.5;
      else if (p.icf >= 17) opp += 0.8;
      else if (p.icf <= 11) opp -= 1.5;
    }
  }
  opp = cl(opp, 1, 10);

  let role = 7.8;
  if (rf.flags && rf.flags.some(f => f.t === 'Unstable minutes')) role -= 2.0;
  if (rf.flags && rf.flags.some(f => f.t === 'Role change')) role -= 2.0;
  if (p.sp === 'NHL') {
    if (p.toi != null) {
      if (p.side === 'under' && p.toi <= 15) role += 0.8;
      else if (p.side === 'under' && p.toi >= 20) role -= 0.6;
      else if (p.side === 'over' && p.toi >= 19) role += 0.8;
    }
    if (p.pp === 'PP1') role += p.side === 'over' ? 1.0 : -1.0;
    else if (p.pp === 'None' && p.side === 'under') role += 0.6;
    if (p.b2b === 'yes') role -= 0.5;
  }
  role = cl(role, 1, 10);

  let mat = 6.5;
  if (p.good) mat += 1.5;
  if (p.bad) mat -= 1.5;
  if (p.sp === 'NHL' && p.oppRk != null) {
    if (p.side === 'under' && p.oppRk <= 8) mat += 1.0;
    else if (p.side === 'over' && p.oppRk >= 25) mat += 1.0;
  }
  mat = cl(mat, 1, 10);

  const ed = (tp - ip) * 100;
  let mkt = 5.0;
  if (ed >= 9) mkt += 4.0;
  else if (ed >= 7) mkt += 3.0;
  else if (ed >= 5) mkt += 2.0;
  else if (ed >= 3) mkt += 1.0;
  else if (ed < 0) mkt -= 2.5;
  mkt = cl(mkt, 1, 10);

  let tr = 6.0;
  const tv = [p.l5, p.l10, p.l20].filter((v) => v != null);
  if (tv.length) {
    const ta = avgArr(tv);
    if (ta >= 0.75) tr += 2.0;
    else if (ta >= 0.65) tr += 1.2;
    else if (ta >= 0.55) tr += 0.5;
    else tr -= 0.8;
  }
  if (p.med != null && p.line != null) {
    if ((p.side === 'under' && p.med < p.line) || (p.side === 'over' && p.med > p.line)) tr += 1.0;
  }
  tr = cl(tr, 1, 10);

  const raw = opp * 0.25 + role * 0.20 + mat * 0.20 + mkt * 0.20 + tr * 0.15;
  const pen = rf.pts <= 1 ? 0 : rf.pts <= 3 ? 0.5 : rf.pts <= 5 ? 1.0 : 1.5;
  return { opp: r2(opp), role: r2(role), mat: r2(mat), mkt: r2(mkt), tr: r2(tr), adj: r2(cl(raw - pen, 1, 10)) };
}

module.exports = { getSharpRules, calcProjection, calcTrueProb, calcRedFlags, calcScores, cl, r2, avgArr };
