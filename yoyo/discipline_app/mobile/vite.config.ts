import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {readFileSync} from 'node:fs';
// One source for native/Web prepaint state. /assets is mounted by the discipline API.
const theme=()=>readFileSync(new URL('../../monitor/static/theme.js',import.meta.url),'utf8');
export default defineConfig({plugins:[react(),{
 name:'spike-shared-appearance',
 configureServer(server){server.middlewares.use('/assets/theme.js',(_req,res)=>{res.setHeader('Content-Type','text/javascript');res.end(theme())})},
 generateBundle(){this.emitFile({type:'asset',fileName:'assets/theme.js',source:theme()})},
}],server:{host:'127.0.0.1',port:5199,strictPort:true},build:{outDir:'dist',assetsInlineLimit:0}});
