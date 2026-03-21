export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  const { player } = req.query;
  if (!player) return res.status(400).json({ error: 'No player name' });
  try {
    const search = await fetch(`https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q=${encodeURIComponent(player)}&active=true`);
    const players = await search.json();
    if (!players.length) return res.status(404).json({ error: 'Player not found' });
    const p = players[0];
    const id = p.playerId;
    const stats = await fetch(`https://api-web.nhle.com/v1/player/${id}/landing`);
    const data = await stats.json();
    const s = data.featuredStats?.regularSeason?.subSeason;
    const log = data.last5Games || [];
    const sog = log.map(g => g.shots || 0);
    const avg = sog.length ? (sog.reduce((a,b)=>a+b,0)/sog.length).toFixed(1) : null;
    res.json({
      name: `${p.firstName} ${p.lastName}`,
      playerId: id,
      team: p.teamAbbrev,
      position: p.positionCode,
      toi: data.careerTotals?.regularSeason?.avgToi || null,
      sogPerGame: s ? (s.shots / s.gamesPlayed).toFixed(1) : null,
      gamesPlayed: s?.gamesPlayed || null,
      last5Games: log.map(g=>({date:g.gameDate,opponent:g.opponentAbbrev,shots:g.shots||0,toi:g.toi})),
      avgLast5: avg
    });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
}
