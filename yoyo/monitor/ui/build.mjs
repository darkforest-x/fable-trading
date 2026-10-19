// Reproducible local assets. Runtime browsers never depend on a third-party CDN.
import {compile} from 'sass';
import {build} from 'esbuild';
import {writeFile, copyFile, mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(here, '../static');
const css = compile(path.join(here, 'bootstrap.scss'), {
  loadPaths:[path.join(here, 'node_modules')], style:'compressed', logger:{warn(){},debug(){}}
});
await writeFile(path.join(out, 'workbench-bootstrap.css'), '/*! Bootstrap 5.3.8 | MIT | scoped component build, see ui/bootstrap.scss */\n'+css.css);
await build({entryPoints:[path.join(here,'bootstrap.js')], outfile:path.join(out,'workbench-bootstrap.js'), bundle:true, minify:true, format:'iife', legalComments:'eof'});
await copyFile(path.join(here,'node_modules/bootstrap/LICENSE'),path.join(out,'bootstrap-LICENSE.txt'));
await mkdir(path.join(out,'icons'),{recursive:true});
for (const name of ['chart-candle','activity','wallet','flask','book','settings','layout-sidebar-left-collapse','search','chevron-left','chevron-right','refresh','maximize','minimize','adjustments-horizontal','x','star','star-filled']) {
  const set=name==='star-filled'?'filled':'outline';
  const sourceName=name==='star-filled'?'star':name;
  await copyFile(path.join(here,`node_modules/@tabler/icons/icons/${set}/${sourceName}.svg`),path.join(out,`icons/${name}.svg`));
}
await copyFile(path.join(here,'node_modules/@tabler/icons/LICENSE'),path.join(out,'icons/LICENSE.tabler'));
