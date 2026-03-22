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
const fullName=p.name||'';
const lnLow=fullName.toLowerCase().split(' ').pop();
const dr=await fetch(`https://api-web.nhle.com/v1/player/${id}/landing`);
const d=await dr.json();
const s=d.featuredStats?.regularSeason?.subSeason;
const gl=await fetch(`https://api-web.nhle.com/v1/player/${id}/game-log/20252026/2`);
const gd=await gl.json();
const allGames=gd.gameLog||[];
const validGames=allGames.filter(g=>g.toi&&g.toi!=='00:00'&&g.toi!=='0:00');
const sogs=validGames.map(g=>g.shots||0);
const toisRaw=validGames.map(g=>{const pts=g.toi.split(':');return parseInt(pts[0])*60+parseInt(pts[1]);});
const totalToiSec=toisRaw.reduce((a,b)=>a+b,0);
const recentTois=toisRaw.slice(0,10).map(t=>t/60);
const avgToi=recentTois.length?Math.round(recentTois.reduce((a,b)=>a+b,0)/recentTois.length*10)/10:null;
const lineNum=parseFloat(line)||1.5;
const sideStr=side||'under';
const hr=(n)=>{const sl=sogs.slice(0,n);if(!sl.length)return null;return Math.round(sl.filter(x=>sideStr==='under'?x<lineNum:x>lineNum).length/sl.length*100)/100;};
const sogSlice=sogs.slice(0,20);
const avg=sogSlice.length?Math.round(sogSlice.reduce((a,b)=>a+b,0)/sogSlice.length*10)/10:null;
const srt=[...sogSlice].sort((a,b)=>a-b);
const median=srt.length?srt[Math.floor(srt.length/2)]:null;
const ppt=s?.powerPlayTimeOnIce?(s.powerPlayTimeOnIce/(s.gamesPlayed||1)):0;
const ppr=ppt>=120?'PP1':ppt>=30?'PP2':'None';
const sogTotal=s?.shots||0;
const gpTotal=s?.gamesPlayed||1;
const sogPG=Math.round(sogTotal/gpTotal*10)/10;
const attPG=validGames.length?Math.round(validGames.reduce((a,g)=>a+(g.shots||0),0)/validGames.length*10)/10:null;
const icfCalc=totalToiSec>0&&attPG?Math.round(attPG/(totalToiSec/validGames.length/60)*60*10)/10:null;
const schedRes=await fetch(`https://api-web.nhle.com/v1/club-schedule/${p.teamAbbrev}/week/now`);
const schedData=await schedRes.json();
const games2=schedData.games||[];
const today=new Date().toISOString().split('T')[0];
let b2b=false;
for(let i=0;i<games2.length-1;i++){
if(games2[i].gameDate===today&&i>0){
const prev=new Date(games2[i-1].gameDate);
const curr=new Date(games2[i].gameDate);
const diff=(curr-prev)/(1000*60*60*24);
if(diff===1)b2b=true;
}
}
let news=[],inj=false;
try{
const rw=await fetch('https://www.rotowire.com/hockey/rss/news.php',{headers:{'User-Agent':'Mozilla/5.0'}});
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
res.json({
name:fullName,
team:p.teamAbbrev,
position:p.positionCode,
toi:avgToi,
sogPerGame:sogPG,
attPerGame:attPG,
icf60:icfCalc,
ppRole:ppr,
ppToiPerGame:Math.round(ppt/60*10)/10,
gamesPlayed:gpTotal,
avg,
median,
hitL5:hr(5),
hitL10:hr(10),
hitL20:hr(20),
b2b,
last5:validGames.slice(0,5).map(g=>({date:g.gameDate,opp:g.opponentAbbrev,shots:g.shots||0,toi:g.toi})),
news,
injuryFlag:inj,
validGames:validGames.length
});
}catch(e){res.status(500).json({error:e.message});}
}
