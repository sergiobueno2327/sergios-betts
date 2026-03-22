export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  const { player, line, side } = req.query;
  if (!player) return res.status(400).json({ error: 'No player name' });
  try {
    const searchRes = await fetch(`https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q=${encodeURIComponent(player)}&active=true`);
    const players = await searchRes.json();
    if (!players.length) return res.status(404).json({ error: 'Player not found' });
    const p = players[0];
    const id = p.playerId;
    const name = `${p.firstName} ${p.lastName}`;
    const statsRes = await fetch(`https://api-web.nhle.com/v1/player/${id}/landing`);
    const data = await statsRes.json();
    const s = data.featuredStats?.regularSeason?.subSeason;
    const career = data.careerTotals?.regularSeason;
    const log = data.last5Games || [];
    const gameLogRes = await fetch(`https://api-web.nhle.com/v1/player/${id}/game-log/20252026/2`);
    const gameLogData = await gameLogRes.json();
    const allGames = gameLogData.gameLog || [];
    const sogAll = allGames.map(g => g.shots || 0);const lineNum = parseFloat(line) || 1.5;
    const sideStr = side || 'under';
    const calcHitRate = (games, n) => {
      const slice = games.slice(0, n);
      if (!slice.length) return null;
      const hits = slice.filter(s => sideStr === 'under' ? s < lineNum : s > lineNum).length;
      return Math.round((hits / slice.length) * 100) / 100;
    };
    const avg = sogAll.length ? Math.round((sogAll.reduce((a,b)=>a+b,0)/sogAll.length)*10)/10 : null;
    const sorted = [...sogAll].sort((a,b)=>a-b);
    const median = sorted.length ? sorted[Math.floor(sorted.length/2)] : null;
    const ppToi = s?.powerPlayToi ? s.powerPlayToi / (s.gamesPlayed || 1) : 0;
    const ppRole = ppToi >= 120 ? 'PP1' : ppToi >= 30 ? 'PP2' : 'None';
    const toiSec = career?.avgToi ? parseInt(career.avgToi.split(':')[0])*60 + parseInt(career.avgToi.split(':')[1]) : null;
    const attPG = s?.shots && s?.gamesPlayed ? Math.round((s.shots/s.gamesPlayed)*10)/10 : null;
    const icf60 = toiSec && attPG ? Math.round((attPG/(toiSec/60))*60*10)/10 : null;
    const mpRes = await fetch('https://moneypuck.com/moneypuck/playerData/seasonSummary/2025-2026/regular/skaters.csv');
    const mpText = await mpRes.text();
    const mpLines = mpText.split('\n');
    const headers = mpLines[0].split(',');
    const nameIdx = headers.indexOf('name');
    const icfIdx = headers.indexOf('I_F_shotAttempts');
    const xgIdx = headers.indexOf('I_F_xGoals');
    const toiIdx = headers.indexOf('icetime');
    let mpICF = null, xsog = null;
    for (const row of mpLines.slice(1)) {
      const cols = row.split(',');
      if (cols[nameIdx] && cols[nameIdx].toLowerCase().includes(p.lastName.toLowerCase())) {
        const toi = parseFloat(cols[toiIdx]) || 1;
        const att = parseFloat(cols[icfIdx]) || 0;
        const xg = parseFloat(cols[xgIdx]) || 0;
        const gp = s?.gamesPlayed || 1;
        mpICF = Math.round((att/toi)*60*10)/10;
        xsog = Math.round((xg/gp)*10)/10;
        break;
      }
    }const rwRes = await fetch('https://www.rotowire.com/hockey/rss/news.php');
    const rwText = await rwRes.text();
    const playerNews = [];
    const items = rwText.match(/<item>([\s\S]*?)<\/item>/g) || [];
    for (const item of items.slice(0, 50)) {
      const title = (item.match(/<title>(.*?)<\/title>/) || [])[1] || '';
      const desc = (item.match(/<description>(.*?)<\/description>/) || [])[1] || '';
      if (title.toLowerCase().includes(p.lastName.toLowerCase()) || desc.toLowerCase().includes(p.lastName.toLowerCase())) {
        playerNews.push({ title: title.replace(/<[^>]+>/g,''), desc: desc.replace(/<[^>]+>/g,'').slice(0,200) });
        if (playerNews.length >= 2) break;
      }
    }
    const injuryFlag = playerNews.some(n => /injur|day-to-day|scratch|out|miss/i.test(n.title + n.desc));
    res.json({
      name, playerId: id, team: p.teamAbbrev, position: p.positionCode,
      toi: career?.avgToi || null,
      sogPerGame: s ? Math.round((s.shots/s.gamesPlayed)*10)/10 : null,
      attPerGame: attPG,
      icf60: mpICF || icf60,
      xsog, ppRole,
      ppToi: Math.round(ppToi/60*10)/10,
      gamesPlayed: s?.gamesPlayed || null,
      avg, median,
      hitL5: calcHitRate(sogAll, 5),
      hitL10: calcHitRate(sogAll, 10),
      hitL20: calcHitRate(sogAll, 20),
      last5Games: log.map(g=>({date:g.gameDate,opponent:g.opponentAbbrev,shots:g.shots||0,toi:g.toi})),
      news: playerNews,
      injuryFlag
    });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
}
