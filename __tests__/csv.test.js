const { parsePlayerFromCSV, parseTOI, getPPRole, hasInjuryFlag } = require('../lib/csv');

describe('parsePlayerFromCSV', () => {
  const csvHeader = 'name,I_F_shotAttempts,I_F_xGoals,icetime,games_played,situation';

  test('finds player by last name and first initial', () => {
    const csv = [
      csvHeader,
      '"Marcus Johansson",50,3.5,800,40,all',
      '"Marcus Johansson",20,1.0,300,40,5on5',
    ].join('\n');
    const result = parsePlayerFromCSV(csv, 'Marcus Johansson', 'MIN');
    expect(result).not.toBeNull();
    expect(result.name).toBe('"Marcus Johansson"');
    expect(result.gamesPlayed).toBe(40);
  });

  test('only matches "all" situation', () => {
    const csv = [
      csvHeader,
      '"Marcus Johansson",20,1.0,300,40,5on5',
    ].join('\n');
    const result = parsePlayerFromCSV(csv, 'Marcus Johansson', 'MIN');
    expect(result).toBeNull();
  });

  test('returns null when player not found', () => {
    const csv = [
      csvHeader,
      '"Other Player",50,3.5,800,40,all',
    ].join('\n');
    const result = parsePlayerFromCSV(csv, 'Marcus Johansson', 'MIN');
    expect(result).toBeNull();
  });

  test('calculates ICF/60 correctly', () => {
    // 60 attempts / 600 seconds * 3600 = 360
    const csv = [
      csvHeader,
      '"Test Player",60,3.0,600,30,all',
    ].join('\n');
    const result = parsePlayerFromCSV(csv, 'Test Player', 'MIN');
    expect(result.icf60).toBe(6.0); // 60/600*60 = 6.0
  });

  test('calculates attPerGame correctly', () => {
    const csv = [
      csvHeader,
      '"Test Player",60,3.0,600,30,all',
    ].join('\n');
    const result = parsePlayerFromCSV(csv, 'Test Player', 'MIN');
    expect(result.attPerGame).toBe(2.0); // 60/30
  });

  test('calculates xSOG correctly', () => {
    const csv = [
      csvHeader,
      '"Test Player",60,3.0,600,30,all',
    ].join('\n');
    const result = parsePlayerFromCSV(csv, 'Test Player', 'MIN');
    expect(result.xsog).toBe(0.1); // 3.0/30 = 0.1
  });
});

describe('parseTOI', () => {
  test('parses standard TOI', () => {
    expect(parseTOI('16:30')).toBe(990);
  });

  test('parses single-digit minutes', () => {
    expect(parseTOI('9:45')).toBe(585);
  });

  test('returns 0 for 00:00', () => {
    expect(parseTOI('00:00')).toBe(0);
  });

  test('returns 0 for 0:00', () => {
    expect(parseTOI('0:00')).toBe(0);
  });

  test('returns 0 for null/empty', () => {
    expect(parseTOI(null)).toBe(0);
    expect(parseTOI('')).toBe(0);
  });

  test('parses 20:00 correctly', () => {
    expect(parseTOI('20:00')).toBe(1200);
  });
});

describe('getPPRole', () => {
  test('PP1 for 120+ seconds', () => {
    expect(getPPRole(120)).toBe('PP1');
    expect(getPPRole(180)).toBe('PP1');
  });

  test('PP2 for 30-119 seconds', () => {
    expect(getPPRole(30)).toBe('PP2');
    expect(getPPRole(90)).toBe('PP2');
    expect(getPPRole(119)).toBe('PP2');
  });

  test('None for under 30 seconds', () => {
    expect(getPPRole(0)).toBe('None');
    expect(getPPRole(29)).toBe('None');
  });
});

describe('hasInjuryFlag', () => {
  test('detects injury keywords', () => {
    expect(hasInjuryFlag('Player listed as day-to-day')).toBe(true);
    expect(hasInjuryFlag('Suffered an injury in practice')).toBe(true);
    expect(hasInjuryFlag('Will miss next 3 games')).toBe(true);
    expect(hasInjuryFlag('Healthy scratch tonight')).toBe(true);
    expect(hasInjuryFlag('Player is out indefinitely')).toBe(true);
  });

  test('returns false for clean text', () => {
    expect(hasInjuryFlag('Player scored 2 goals tonight')).toBe(false);
    expect(hasInjuryFlag('Great performance in win')).toBe(false);
  });

  test('case insensitive', () => {
    expect(hasInjuryFlag('INJURY report')).toBe(true);
    expect(hasInjuryFlag('Day-To-Day')).toBe(true);
  });
});
