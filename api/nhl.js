export default async function handler(req,res){
res.setHeader('Access-Control-Allow-Origin','*');
const{player,line,side}=req.query;
if(!player)return res.status(400).json({error:'No player'});
try{
const sr=await fetch(`https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=5&q=${encodeURIComponent(player)}&active=true`);
const ps=await sr.json();
if(!ps.length)return res.status(404).json({error:'Not found'});
const p=ps[0];const id=p.playerId;
const dr=await fetch(`https://api-web.nhle.com/v1/player/${id}/landing`);
const d=await dr.json();
const s=d.featuredStats?.regularSeason?.subSeason;
const c=d.careerTotals?.regularSeason;
const gl=await fetch(`https://api-web.nhle.com/v1/player/${id}/game-log/20252026/2`);
const gd=await gl.json();
const games=gd.gameLog||[];
const sogs=games.map(g=>g.shots||0);
const ln=parseFloat(line)||1.5;
const si=side||'under';
const hr=(n)=>{const sl=sogs.slice(0,n);if(!sl.length)return null;return Math.round(sl.filter(x=>si==='under'?x<ln:x>ln).length/sl.length*100)/100;};
const avg=sogs.length?Math.round(sogs.reduce((a,b)=>a+b,0)/sogs.length*10)/10:null;
const med=[...sogs].sort((a,b)=>a-b);
const median=med.length?med[Math.floor(med.length/2)]:null;
const ppt=s?.powerPlayToi?(s.powerPlayToi/(s.gamesPlayed||1)):0;
const ppr=ppt>=120?'PP1':ppt>=30?'PP2':'None';
const apg=s?.shots&&s?.gamesPlayed?Math.round(s.shots/s.gamesPlayed*10)/10:null;
const toiParts=c?.avgToi?c.avgToi.split(':'):null;
const toiSec=toiParts?parseInt(toiParts[0])*60+parseInt(toiParts[1]):null;
const icf=toiSec&&apg?Math.round(apg/(toiSec/60)*60*10)/10:null;
const rw=await fetch('https://www.rotowire.com/hockey/rss/news.php');
const rt=await rw.text();
const news=[];
const its=rt.match(/<item>([\s\S]*?)<\/item>/g)||[];
for(const it of its.slice(0,50)){
const t=((it.match(/<title>(.*?)<\/title>/)||[])[1]||'').replace(/<[^>]+>/g,'');
const dc=((it.match(/<description>(.*?)<\/description>/)||[])[1]||'').replace(/<[^>]+>/g,'').slice(0,150);
if(t.toLowerCase().includes(p.lastName.toLowerCase())||dc.toLowerCase().includes(p.lastName.toLowerCase())){news.push({title:t,desc:dc});if(news.length>=2)break;}
}
const inj=news.some(n=>/injur|day-to-day|scratch|out|miss/i.test(n.title+n.desc));
res.json({name:`${p.firstName} ${p.lastName}`,team:p.teamAbbrev,position:p.positionCode,toi:c?.avgToi||null,sogPerGame:s?Math.round(s.shots/s.gamesPlayed*10)/10:null,attPerGame:apg,icf60:icf,ppRole:ppr,ppToiPerGame:Math.round(ppt/60*10)/10,gamesPlayed:s?.gamesPlayed||null,avg,median,hitL5:hr(5),hitL10:hr(10),hitL20:hr(20),last5:d.last5Games?.map(g=>({date:g.gameDate,opp:g.opponentAbbrev,shots:g.shots||0}))||[],news,injuryFlag:inj});
}catch(e){res.status(500).json({error:e.message});}
}
