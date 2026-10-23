/** Public-symbol preferences only. Hiding expires at the NY week boundary. */
export type FibPreferences={favorites:string[];hidden:{week:string;symbols:string[]}};
const symbols=(values:unknown)=>Array.isArray(values)?[...new Set(values.filter((s):s is string=>typeof s==='string'&&/^[A-Z0-9]{1,40}-USDT-SWAP$/.test(s)))].slice(0,2000):[];
export function parseFibPreferences(value:string|null):FibPreferences{
 try{const p=JSON.parse(value??'{}');return {favorites:symbols(p.favorites),hidden:{week:typeof p.hidden?.week==='string'?p.hidden.week:'',symbols:symbols(p.hidden?.symbols)}}}catch{return {favorites:[],hidden:{week:'',symbols:[]}}}
}
export function visibleFibSymbol(p:FibPreferences,week:string,symbol:string){return p.hidden.week!==week||!p.hidden.symbols.includes(symbol)}
export function hideFibSymbol(p:FibPreferences,week:string,symbol:string){return {...p,hidden:{week,symbols:symbols([...(p.hidden.week===week?p.hidden.symbols:[]),symbol])}}}
export function restoreFibSymbol(p:FibPreferences,week:string,symbol:string){return p.hidden.week===week?{...p,hidden:{week,symbols:p.hidden.symbols.filter(s=>s!==symbol)}}:p}
export function favoriteFibSymbol(p:FibPreferences,symbol:string){return {...p,favorites:p.favorites.includes(symbol)?p.favorites.filter(s=>s!==symbol):symbols([...p.favorites,symbol])}}
