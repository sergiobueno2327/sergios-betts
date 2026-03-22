export default async function handler(req,res){
res.setHeader('Access-Control-Allow-Origin','*');
const{player,line,side}=req.query;
if(!player)return res.status(400).json({error:'No player'});
try{
const sr=await fetch(`https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q=${encodeURIComponent(player)}&active=true`);
const ps=await sr.json();
if(!ps.length)return res.status(404).json({error:'Not found'});
const p=ps[0];
const id=p.playerId;
const firstName=p.firstName?.default||p.firstName||'';
const lastName=p.lastName?.default||p.lastName||'';
const name=`${firstName} ${lastName}`;
const lastNameLower=lastName.toLowerCase();
const dr=await fetch(`https://api-web.nhle.com/v1/player/${id}/landing`);
const d=await dr.json();
const s=d.featuredStats?.regularSeason?.subSeason;
const gl=await fetch(`https://api-web.nhle.com/v1/player/${id}/game-log/20252026/2`);
const gd=await gl.json();
const games=(gd.gameLog||[]).slice(0,20);
const sogs=games.map(g=>g.shots||0);
const tois=games.map(g=>{if(!g.toi)return 0;const pts=g.toi.split(':');return parseInt(pts[0])+parseInt(pts[1])/60;});
const avgToi=tois.length?Math.round(tois.reduce((a,b)=>a+b,0)/tois.length*10)/10:null;
const ln=parseFloat(line)||1.5;
const si=side||'under';
const hr=(n)=>{const sl=sogs.slice(0,n);if(!sl.length)return null;return Math.round(sl.filter(x=>si==='under'?x<ln:x>ln).length/sl.length*100)/100;};
const avg=sogs.length?Math.round(sogs.reduce((a,b)=>a+b,0)/sogs.length*10)/10:null;
const sorted=[...sogs].sort((a,b)=>a-b);
const median=sorted.length?sorted[Math.floor(sorted.length/2)]:null;
const ppt=s?.powerPlayTimeOnIce?(s.powerPlayTimeOnIce/(s.gamesPlayed||1)):0;
const ppr=ppt>=120?'PP1':ppt>=30?'PP2':'None';
const attPG=s?.shots&&s?.gamesPlayed?Math.round(s.shots/s.gamesPlayed*10)/10:null;
const icf=avgToi&&attPG?Math.round(attPG/avgToi*60*10)/10:null;
let mpICF=null,xsog=null;
try{
const mpRes=await fetch('https://moneypuck.com/moneypuck/playerData/seasonSummary/2025-2026/regular/skaters.csv');
const mpText=await mpRes.text();
const mpLines=mpText.split('\n');
const headers=mpLines[0].split(',');
const nameIdx=headers.findIndex(h=>h.trim()==='name');
const icfIdx=headers.findIndex(h=>h.trim()==='I_F_shotAttempts');
const xgIdx=headers.findIndex(h=>h.trim()==='I_F_xGoals');
const toiIdx=headers.findIndex(h=>h.trim()==='icetime');
const gpIdx=headers.findIndex(h=>h.trim()==='games_played');
for(const row of mpLines.slice(1)){
const cols=row.split(',');
if(!cols[nameIdx])continue;
const rowName=(cols[nameIdx]||'').toLowerCase().replace(/"/g,'');
if(rowName.includes(lastNameLower)){
const toi=parseFloat(cols[toiIdx])||1;
const att=parseFloat(cols[icfIdx])||0;
const xg=parseFloat(cols[xgIdx])||0;
const gp=parseFloat(cols[gpIdx])||s?.gamesPlayed||1;
mpICF=Math.round(att/toi*60*10)/10;
xsog=Math.round(xg/gp*10)/10;
break;
}
}
}catch(e){}
let news=[],inj=false;
try{
const rw=await fetch('https://www.rotowire.com/hockey/rss/news.php');
const rt=await rw.text();
const its=rt.match(/<item>([\s\S]*?)<\/item>/g)||[];
for(const it of its.slice(0,50)){
const t=((it.match(/<title>(.*?)<\/title>/)||[])[1]||'').replace(/<[^>]+>/g,'');
const dc=((it.match(/<description>(.*?)<\/description>/)||[])[1]||'').replace(/<[^>]+>/g,'').slice(0,200);
if(t.toLowerCase().includes(lastNameLower)||dc.toLowerCase().includes(lastNameLower)){
news.push({title:t,desc:dc});
if(news.length>=2)break;
}
}
inj=news.some(n=>/injur|day-to-day|scratch|out|miss/i.test(n.title+n.desc));
}catch(e){}
res.json({name,team:p.teamAbbrev,position:p.positionCode,toi:avgToi,sogPerGame:s?Math.round(s.shots/s.gamesPlayed*10)/10:null,attPerGame:attPG,icf60:mpICF||icf,xsog,ppRole:ppr,ppToiPerGame:Math.round(ppt/60*10)/10,gamesPlayed:s?.gamesPlayed||null,avg,median,hitL5:hr(5),hitL10:hr(10),hitL20:hr(20),last5:games.slice(0,5).map(g=>({date:g.gameDate,opp:g.opponentAbbrev,shots:g.shots||0,toi:g.toi})),news,injuryFlag:inj});
}catch(e){res.status(500).json({error:e.message});}
}
