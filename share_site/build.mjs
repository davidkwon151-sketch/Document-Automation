import fs from 'node:fs';
import path from 'node:path';
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json','.png':'image/png','.jpg':'image/jpeg','.svg':'image/svg+xml','.csv':'text/csv; charset=utf-8','.txt':'text/plain; charset=utf-8'};
const assets={};
function read(dir,prefix=''){for(const name of fs.readdirSync(dir)){const p=path.join(dir,name);if(fs.statSync(p).isDirectory())read(p,prefix+'/'+name);else assets[prefix+'/'+name]={type:types[path.extname(name)]||'application/octet-stream',data:fs.readFileSync(p).toString('base64')};}}
read('dist/assets','/assets');
for(const name of ['index.html','styles.css','app.js','workspace.html','workspace.css','workspace.js','sales.html','sales.css','sales.js','common.html','common.css','common.js','access.html','access.css','access.js','claude.html'])assets['/'+name]={type:types[path.extname(name)],data:fs.readFileSync('dist/'+name).toString('base64')};
fs.mkdirSync('dist/server',{recursive:true});fs.mkdirSync('dist/.openai',{recursive:true});
fs.writeFileSync('dist/server/index.js',fs.readFileSync('worker.mjs','utf8')+'\nexport default createWorker('+JSON.stringify(assets)+');\n');
fs.copyFileSync('.openai/hosting.json','dist/.openai/hosting.json');
