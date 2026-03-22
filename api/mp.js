export default async function handler(req,res){
res.setHeader('Access-Control-Allow-Origin','*');
const{player,team}=req.query;
if(!player)return res.status(400).json({error:'No player'});
try{
const url=`https://moneypuck.com/moneypuck/playerData/seasonSummary/2025/regular/teams/skaters/${team||'MIN'}.csv`;
const r=await fetch(url,{headers:{'User-Agent':'Mozilla/5.0'}});
const text=await r.text();
const rows=text.split('\n');
const hdrs=rows[0].split(',').map(h=>h.trim().replace(/"/g,''));
const col=n=>hdrs.indexOf(n);
const nc=col('name'),ic=col('I_F_shotAttempts'),xc=col('I_F_xGoals'),tc=col('icetime'),gc=col('games_played'),sc=col('situation');
const lnLow=player.toLowerCase().split(' ').pop();
const fnFirst=player.toLowerCase().charAt(0);
for(const row of rows.slice(1)){
const c=row.split(',');
if(!c[nc])continue;
const rn=(c[nc]||'').toLowerCase().replace(/"/g,'');
const sit=(c[sc]||'').toLowerCase().replace(/"/g,'');
if(sit!=='all')continue;
if(rn.includes(lnLow)&&rn.includes(fnFirst)){
const toi=parseFloat(c[tc])||1;
const att=parseFloat(c[ic])||0;
const xg=parseFloat(c[xc])||0;
const gp=parseFloat(c[gc])||1;
return res.json({
name:c[nc],
icf60:Math.round(att/toi*60*10)/10,
xsog:Math.round(xg/gp*10)/10,
attPerGame:Math.round(att/gp*10)/10,
gamesPlayed:Math.round(gp)
});
}
}
res.status(404).json({error:'Player not found in team file'});
}catch(e){
res.status(500).json({error:e.message});
}
}
