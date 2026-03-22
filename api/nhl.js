export default async function handler(req,res){
res.setHeader('Access-Control-Allow-Origin','*');
const{player}=req.query;
if(!player)return res.status(400).json({error:'No player'});
const sr=await fetch(`https://search.d3.nhle.com/api/v1/search/player?culture=en-us&limit=3&q=${encodeURIComponent(player)}&active=true`);
const ps=await sr.json();
res.json({raw:ps[0],keys:ps[0]?Object.keys(ps[0]):[]});
}
