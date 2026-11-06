import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {readFileSync} from 'node:fs';
// One source for native/Web prepaint state. /assets is mounted by the discipline API.
const theme=()=>readFileSync(new URL('../../monitor/static/theme.js',import.meta.url),'utf8');
// QA builds pointed at a loopback fixture (VITE_SPIKE_* env) may reach that origin from the WebView.
// Release builds set none of these variables, so their CSP is byte-identical.
const qaOrigins=()=>[process.env.VITE_SPIKE_SERVICE_URL,process.env.VITE_SPIKE_CANDLES_HTTP,process.env.VITE_SPIKE_CANDLES_WS]
 .flatMap(v=>{try{const u=new URL(v??'');return ['127.0.0.1','localhost'].includes(u.hostname)&&!u.username&&!u.password?[u.origin]:[]}catch{return []}});
export default defineConfig({plugins:[react(),{
 name:'spike-qa-loopback-csp',
 transformIndexHtml(html){const extra=[...new Set(qaOrigins())];return extra.length?html.replace("connect-src 'self'",`connect-src 'self' ${extra.join(' ')}`):html},
},{
 // Simulator acceptance only: tests/discipline_app/qa_tour.js walks the synthetic fixture.
 name:'spike-qa-tour',
 generateBundle(){if(process.env.VITE_SPIKE_QA_TOUR==='1'&&qaOrigins().length)this.emitFile({type:'asset',fileName:'assets/qa-tour.js',source:readFileSync(new URL('../../../tests/discipline_app/qa_tour.js',import.meta.url),'utf8')})},
 transformIndexHtml(html){return process.env.VITE_SPIKE_QA_TOUR==='1'&&qaOrigins().length?html.replace('</body>','<script src="/assets/qa-tour.js" defer></script></body>'):html},
},{
 name:'spike-shared-appearance',
 configureServer(server){server.middlewares.use('/assets/theme.js',(_req,res)=>{res.setHeader('Content-Type','text/javascript');res.end(theme())})},
 generateBundle(){this.emitFile({type:'asset',fileName:'assets/theme.js',source:theme()})},
}],server:{host:'127.0.0.1',port:5199,strictPort:true},build:{outDir:'dist',assetsInlineLimit:0}});
