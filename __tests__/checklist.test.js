const { getLayerResult, getVerdict, LAYERS } = require('../lib/checklist');

function makeState() {
  const state = {};
  LAYERS.forEach((l) => {
    state[l.id] = {};
    l.criteria.forEach((c) => { state[l.id][c.id] = null; });
  });
  return state;
}

describe('getLayerResult', () => {
  test('returns neu when no criteria are set', () => {
    const state = makeState();
    expect(getLayerResult('profile', state)).toBe('neu');
  });

  test('returns pass when all required pass-type criteria are true', () => {
    const state = makeState();
    state.profile.p1 = true;
    state.profile.p2 = true;
    state.profile.p3 = true;
    expect(getLayerResult('profile', state)).toBe('pass');
  });

  test('returns fail when a required pass-type criterion is false', () => {
    const state = makeState();
    state.profile.p1 = true;
    state.profile.p2 = false; // required, type=pass, marked false = fail
    state.profile.p3 = true;
    expect(getLayerResult('profile', state)).toBe('fail');
  });

  test('returns warn when optional criteria are false', () => {
    const state = makeState();
    state.profile.p1 = true;
    state.profile.p2 = true;
    state.profile.p3 = true;
    state.profile.p4 = false; // optional, marked false = warn
    expect(getLayerResult('profile', state)).toBe('warn');
  });

  test('returns fail when fail-type criterion is activated (true)', () => {
    const state = makeState();
    state.matchup.m5 = true; // type=fail, checked=true means fail triggered
    expect(getLayerResult('matchup', state)).toBe('fail');
  });

  test('returns warn when warn-type criterion is activated', () => {
    const state = makeState();
    state.price.pr2 = true; // type=warn
    expect(getLayerResult('price', state)).toBe('warn');
  });

  test('boost criteria count as pass', () => {
    const state = makeState();
    state.opportunity.o5 = true; // type=boost
    expect(getLayerResult('opportunity', state)).toBe('pass');
  });

  test('returns fail when required pass criterion is set to false in flags layer', () => {
    const state = makeState();
    state.flags.f1 = true;
    state.flags.f2 = true;
    state.flags.f3 = false; // required, pass-type, set to false
    expect(getLayerResult('flags', state)).toBe('fail');
  });
});

describe('getVerdict', () => {
  test('stop when a required layer fails', () => {
    const results = [
      { required: true, result: 'fail' },
      { required: true, result: 'pass' },
      { required: true, result: 'pass' },
      { required: true, result: 'pass' },
      { required: false, result: 'pass' },
      { required: true, result: 'pass' },
    ];
    expect(getVerdict(results)).toBe('stop');
  });

  test('stop when 2+ layers fail', () => {
    const results = [
      { required: false, result: 'fail' },
      { required: false, result: 'fail' },
      { required: true, result: 'pass' },
      { required: true, result: 'pass' },
      { required: false, result: 'pass' },
      { required: true, result: 'pass' },
    ];
    expect(getVerdict(results)).toBe('stop');
  });

  test('caution when 1 non-required layer fails', () => {
    const results = [
      { required: false, result: 'fail' },
      { required: true, result: 'pass' },
      { required: true, result: 'pass' },
      { required: true, result: 'pass' },
      { required: false, result: 'pass' },
      { required: true, result: 'pass' },
    ];
    expect(getVerdict(results)).toBe('caution');
  });

  test('caution when 2+ warnings', () => {
    const results = [
      { required: true, result: 'warn' },
      { required: true, result: 'warn' },
      { required: true, result: 'pass' },
      { required: true, result: 'pass' },
      { required: false, result: 'pass' },
      { required: true, result: 'pass' },
    ];
    expect(getVerdict(results)).toBe('caution');
  });

  test('go when 4+ layers pass and no failures', () => {
    const results = [
      { required: true, result: 'pass' },
      { required: true, result: 'pass' },
      { required: true, result: 'pass' },
      { required: true, result: 'pass' },
      { required: false, result: 'neu' },
      { required: true, result: 'neu' },
    ];
    expect(getVerdict(results)).toBe('go');
  });

  test('go when all 6 layers pass', () => {
    const results = LAYERS.map((l) => ({ required: l.required, result: 'pass' }));
    expect(getVerdict(results)).toBe('go');
  });
});
