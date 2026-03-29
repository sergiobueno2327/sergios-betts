/**
 * Checklist layer evaluation logic.
 * Extracted from checklist.html for testability.
 */

const LAYERS = [
  { id: 'profile', required: true, criteria: [
    { id: 'p1', type: 'pass', req: true },
    { id: 'p2', type: 'pass', req: true },
    { id: 'p3', type: 'pass', req: true },
    { id: 'p4', type: 'pass', req: false },
  ]},
  { id: 'opportunity', required: true, criteria: [
    { id: 'o1', type: 'pass', req: true },
    { id: 'o2', type: 'pass', req: true },
    { id: 'o3', type: 'pass', req: false },
    { id: 'o4', type: 'pass', req: false },
    { id: 'o5', type: 'boost', req: false },
  ]},
  { id: 'matchup', required: true, criteria: [
    { id: 'm1', type: 'pass', req: false },
    { id: 'm2', type: 'boost', req: false },
    { id: 'm3', type: 'pass', req: false },
    { id: 'm4', type: 'pass', req: false },
    { id: 'm5', type: 'fail', req: false },
  ]},
  { id: 'price', required: true, criteria: [
    { id: 'pr1', type: 'pass', req: false },
    { id: 'pr2', type: 'warn', req: false },
    { id: 'pr3', type: 'fail', req: false },
    { id: 'pr4', type: 'pass', req: true },
    { id: 'pr5', type: 'boost', req: false },
  ]},
  { id: 'movement', required: false, criteria: [
    { id: 'mv1', type: 'pass', req: false },
    { id: 'mv2', type: 'boost', req: false },
    { id: 'mv3', type: 'fail', req: false },
    { id: 'mv4', type: 'fail', req: false },
    { id: 'mv5', type: 'warn', req: false },
  ]},
  { id: 'flags', required: true, criteria: [
    { id: 'f1', type: 'pass', req: true },
    { id: 'f2', type: 'pass', req: true },
    { id: 'f3', type: 'pass', req: true },
    { id: 'f4', type: 'pass', req: false },
    { id: 'f5', type: 'pass', req: false },
  ]},
];

/** Determine layer result from criteria state */
function getLayerResult(layerId, state) {
  const layer = LAYERS.find((l) => l.id === layerId);
  if (!layer) return 'neu';
  const s = state[layerId];
  const hasAny = Object.values(s).some((v) => v !== null);
  if (!hasAny) return 'neu';

  // Check required criteria
  for (const c of layer.criteria) {
    if (c.req && s[c.id] !== null) {
      if (c.type === 'pass' && s[c.id] === false) return 'fail';
      if (c.type === 'fail' && s[c.id] === true) return 'fail';
    }
  }

  let failCount = 0, warnCount = 0, passCount = 0;
  for (const c of layer.criteria) {
    if (s[c.id] === null) continue;
    if (c.type === 'fail' && s[c.id] === true) failCount++;
    else if (c.type === 'warn' && s[c.id] === true) warnCount++;
    else if ((c.type === 'pass' || c.type === 'boost') && s[c.id] === true) passCount++;
    else if (s[c.id] === false) warnCount++;
  }

  if (failCount > 0) return 'fail';
  if (warnCount > 0) return 'warn';
  if (passCount > 0) return 'pass';
  return 'neu';
}

/** Determine overall verdict from layer results */
function getVerdict(layerResults) {
  const fails = layerResults.filter((r) => r.required && r.result === 'fail');
  const allFails = layerResults.filter((r) => r.result === 'fail');
  const warns = layerResults.filter((r) => r.result === 'warn');
  const passes = layerResults.filter((r) => r.result === 'pass');

  if (fails.length > 0 || allFails.length >= 2) return 'stop';
  if (allFails.length === 1 || warns.length >= 2) return 'caution';
  if (passes.length >= 4) return 'go';
  return 'caution';
}

module.exports = { LAYERS, getLayerResult, getVerdict };
