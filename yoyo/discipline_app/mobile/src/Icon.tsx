/** Local vector icons matching the Stitch Porcelain & Jade design language. */
export type IconName='trend'|'trade'|'positions'|'shield'|'link'|'chevron'|'clock'|'check'|'close'|'arrow'|'lock'|'settings';
const paths:Record<IconName,string>={
 settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1zM16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
 trend:'M3 17l5-5 4 3 8-10M15 5h5v5',
 trade:'M6 3v18M3 8h6v6H3zM17 3v18M14 6h6v9h-6z',
 positions:'M4 7h16v13H4zM8 7V4h8v3M4 12h16M10 12v3h4v-3',
 shield:'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6',
 link:'M10 13a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-2 2M14 11a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l2-2',
 chevron:'M9 5l7 7-7 7',
 clock:'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
 check:'M5 12l4 4L19 6',
 close:'M6 6l12 12M18 6L6 18',
 arrow:'M5 12h14M13 6l6 6-6 6',
 lock:'M6 10h12v11H6zM8 10V6a4 4 0 0 1 8 0v4M12 14v3',
};
export function Icon({name,size=20}:{name:IconName;size?:number}){
 return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
