export default async function handler(req,res){
res.setHeader('Access-Control-Allow-Origin','*');
const{team}=req.query;
if(!team)return res.status(400).json({error:'No team'});
try{
const r=await fetch('https://api-web.nhle.com/v1/standings/now');
const d=await r.json();
const standings=d.standings||[];
const teams=standings.map(t=>({
team:t.teamAbbrev?.default||t.teamAbbrev,
name:t.teamName?.default||t.teamName,
goalsAgainstPerGame:t.goalsAgainstPerGame,
shotsAgainstPerGame:t.shotsAgainstPerGame||null,
wins:t.wins,
losses:t.losses,
points:t.points,
gamesPlayed:t.gamesPlayed
}));
teams.sort((a,b)=>(a.goalsAgainstPerGame||99)-(b.goalsAgainstPerGame||99));
const rank=teams.findIndex(t=>t.team?.toUpperCase()===team.toUpperCase())+1;
const found=teams.find(t=>t.team?.toUpperCase()===team.toUpperCase());
if(!found)return res.status(404).json({error:'Team not found',available:teams.map(t=>t.team)});
res.json({
team:found.team,
name:found.name,
goalsAgainstPerGame:found.goalsAgainstPerGame,
defensiveRank:rank,
totalTeams:teams.length,
wins:found.wins,
losses:found.losses,
points:found.points
});
}catch(e){
res.status(500).json({error:e.message});
}
}
