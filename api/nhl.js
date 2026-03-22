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
const fn=typeof p.firstName==='object'?p.firstName.default:p.firstName||'';
const ln=typeof p.lastName==='object'?p.lastName.default:p.lastName||'';
const name=`${fn} ${ln}`;
const lnLow=ln.toLowerCase();
const dr=await fetch(`https://api-web.nhle.com/v1/player/${id}/landing`);
const d=await dr.json();
const s=d.featuredStats?.regularSeason?.subSeason;
const gl=await fetch(`https://api-web.nhle.com/v1/player/${id}/game-log/20252026/2`);
const gd=await gl.json();
const games=gd.gameLog||[];
const sogs=games.map(g=>g.shots||0);
const toisRaw=games.map(g=>{if(!g.toi)return 0;const pts=g.toi.split(':');return parseInt(pts[0])+parseInt(pts[1])/60;});
const avgToi=toisRaw.length?Math.round(toisRaw.reduce((a,b)=>a+b,0)/toisRaw.length*10)/10:null;
const lineNum=parseFloat(line)||1.5;
const sideStr=side||'under';
const hr=(n)=>{const sl=sogs.slice(0,n);if(!sl.length)return null;return Math.round(sl.filter(x=>sideStr==='under'?x<lineNum:x>lineNum).length/sl.length*100)/100;};
const avg=sogs.length?Math.round(sogs.reduce((a,b)=>a+b,0)/sogs.length*10)/10:null;
const srt=[...sogs].sort((a,b)=>a-b);
const median=srt.length?srt[Math.floor(srt.length/2)]:null;
const ppt=s?.powerPlayTimeOnIce?(s.powerPlayTimeOnIce/(s.gamesPlayed||1)):0;
const ppr=ppt>=120?'PP1':ppt>=30?'PP2':'None';
const attPG=s?.shots&&s?.gamesPlayed?Math.round(s.shots/s.gamesPlayed*10)/10:null;
const icfCalc=avgToi&&attPG?Math.round(attPG/avgToi*60*10)/10:null;
let mpICF=null,xsog=null;
try{
const mpRes=await fetch('https://moneypuck.com/moneypuck/playerData/seasonSummary/2025-2026/regular/skaters.csv');
const mpText=await mpRes.text();
const rows=mpText.split('\n');
const hdrs=rows[0].split(',').map(h=>h.trim().replace(/"/g,''));
const col=(n)=>hdrs.indexOf(n);
const nameCol=col('name');
const icfCol=col('I_F_shotAttempts');
const xgCol=col('I_F_xGoals');
const toiCol=col('icetime');
const gpCol=col('games_played');
const situationCol=col('situation');
for(const row of rows.slice(1)){
const c=row.split(',');
if(!c[nameCol])continue;
const rn=(c[nameCol]||'').toLowerCase().replace(/"/g,'');
const sit=(c[situationCol]||'').toLowerCase().replace(/"/g,'');
if(sit!=='all')continue;
if(rn.includes(lnLow)){
const toi=parseFloat(c[toiCol])||1;
const att=parseFloat(c[icfCol])||0;
const xg=parseFloat(c[xgCol])||0;
const gp=parseFloat(c[gpCol])||s?.gamesPlayed||1;
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
if(t.toLowerCase().includes(lnLow)||dc.toLowerCase().includes(lnLow)){
news.push({title:t,desc:dc});
if(news.length>=2)break;
}
}
inj=news.some(n=>/injur|day-to-day|scratch|out|miss/i.test(n.title+n.desc));
}catch(e){}
res.json({name,team:p.teamAbbrev,position:p.positionCode,toi:avgToi,sogPerGame:s?Math.round(s.shots/s.gamesPlayed*10)/10:null,attPerGame:attPG,icf60:mpICF||icfCalc,xsog,ppRole:ppr,ppToiPerGame:Math.round(ppt/60*10)/10,gamesPlayed:s?.gamesPlayed||null,avg,median,hitL5:hr(5),hitL10:hr(10),hitL20:hr(20),last5:games.slice(0,5).map(g=>({date:g.gameDate,opp:g.opponentAbbrev,shots:g.shots||0,toi:g.toi})),news,injuryFlag:inj});
}catch(e){res.status(500).json({error:e.message});}
}
