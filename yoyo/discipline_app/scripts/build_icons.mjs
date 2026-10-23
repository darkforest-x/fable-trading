/** Export fixed vector masters; Stitch icon-size-0.8.16 supplies the 75% target.
 * Run with --sharp-module /absolute/path/to/sharp when using a bundled runtime.
 * No runtime font, account data, or trading service is involved.
 */
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const require=createRequire(import.meta.url);
const flag=process.argv.indexOf('--sharp-module');
const sharp=require(flag>=0?process.argv[flag+1]:'sharp');
const root=fileURLToPath(new URL('../../../',import.meta.url));
const mobile=path.join(root,'yoyo/discipline_app/mobile');
const dollar=await readFile(path.join(mobile,'public/spike-icon.svg'),'utf8');
const monogram=await readFile(path.join(root,'yoyo/monitor/static/icons/spike-s.svg'),'utf8');
const inside=svg=>svg.replace(/^[\s\S]*?<svg[^>]*>/,'').replace(/<\/svg>\s*$/,'');
const frame=content=>`<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">${content}</svg>`;
async function png(svg,size,destination,opaque=true){
 let pipeline=sharp(Buffer.from(svg),{density:144}).resize(size,size);
 if(opaque)pipeline=pipeline.flatten({background:'#000'}).removeAlpha();
 await pipeline.png().toFile(destination);
}
await png(dollar,1024,path.join(mobile,'ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png'));
await png(dollar,180,path.join(mobile,'public/apple-touch-icon.png'));
await png(monogram,180,path.join(root,'yoyo/monitor/static/icons/spike-s-touch-20261019.png'));
// Android adaptive foreground includes the platform's 18dp overscan on each side.
const glyph=inside(dollar).replace(/<rect\b[^>]*\/>/,'');
const foreground=frame(`<g transform="translate(512 512) scale(0.6666666666666666) translate(-512 -512)">${glyph}</g>`);
const round=frame(`<defs><clipPath id="round"><circle cx="512" cy="512" r="512"/></clipPath></defs><g clip-path="url(#round)">${inside(dollar)}</g>`);
for(const [density,size,adaptive] of [['mdpi',48,108],['hdpi',72,162],['xhdpi',96,216],['xxhdpi',144,324],['xxxhdpi',192,432]]){
 const folder=path.join(mobile,'android/app/src/main/res',`mipmap-${density}`);
 await png(dollar,size,path.join(folder,'ic_launcher.png'));
 await png(round,size,path.join(folder,'ic_launcher_round.png'),false);
 await png(foreground,adaptive,path.join(folder,'ic_launcher_foreground.png'),false);
}
const preview=`<svg xmlns="http://www.w3.org/2000/svg" width="520" height="288" viewBox="0 0 520 288"><rect width="520" height="288" fill="#f1f2f1"/><defs><clipPath id="mask"><rect width="1024" height="1024" rx="224"/></clipPath></defs><g transform="translate(32 24) scale(.2)" clip-path="url(#mask)">${inside(dollar)}</g><g transform="translate(284 24) scale(.2)" clip-path="url(#mask)">${inside(monogram)}</g><text x="134" y="261" font-family="Arial,sans-serif" font-size="17" text-anchor="middle" fill="#172422">Spike $</text><text x="386" y="261" font-family="Arial,sans-serif" font-size="17" text-anchor="middle" fill="#172422">Spike S</text></svg>`;
await sharp(Buffer.from(preview)).png().toFile(path.join(root,'yoyo/discipline_app/design/icon-size-0.8.16/preview.png'));
console.log('Exported iOS, Android, touch icons and comparison preview.');
