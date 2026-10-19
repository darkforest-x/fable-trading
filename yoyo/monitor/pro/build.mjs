// Compile Stitch's exact per-screen configuration; never normalize its tokens.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const root = path.dirname(fileURLToPath(import.meta.url));
const source = path.join(root, 'source');
const output = path.join(root, '../static/pro');
fs.mkdirSync(output, {recursive:true});
fs.mkdirSync(path.join(root, 'build'), {recursive:true});
for (const file of fs.readdirSync(source).filter(f=>f.endsWith('.html'))) {
  const html = fs.readFileSync(path.join(source,file),'utf8');
  const object = html.match(/<script id="tailwind-config">\s*tailwind\.config\s*=\s*(.*?)<\/script>/s)[1];
  // Some exports use unquoted JS object keys. Parse scalar data, never execute
  // an exported script (or a live site's instrumentation) during the build.
  const config = JSON.parse(object.trim().replace(/;$/, '').replace(/([{,])\s*([A-Za-z_$][\w$]*)\s*:/g, '$1"$2":'));
  // All adapter classes are drawn from the export's vocabulary.
  config.content = [path.join(source,file), path.join(output,'*.js')];
  const name = file.replace('.html','');
  const cfg = path.join(root,'build',`${name}.json`);
  const css = path.join(root,'build',`${name}.css`);
  fs.writeFileSync(cfg,JSON.stringify(config));
  const styles = [...html.matchAll(/<style[^>]*>(.*?)<\/style>/gs)].map(m=>m[1]).join('\n');
  fs.writeFileSync(css,'@tailwind base;\n@tailwind components;\n@tailwind utilities;\n'+styles);
  execFileSync(process.execPath,[path.join(root,'node_modules/tailwindcss/lib/cli.js'),'-c',cfg,'-i',css,'-o',path.join(output,`${name}.css`),'--minify'],{stdio:'inherit'});
}
