/** Strong detents without one native bridge call for every slider pixel. */
export function rangeImpact(previous:number,next:number,elapsed:number):'medium'|'heavy'|null{
 if(!Number.isFinite(previous)||!Number.isFinite(next)||previous===next)return null;
 const endpoint=next===0||next===100;
 if(elapsed<(endpoint?40:80))return null;
 return endpoint?'heavy':Math.floor(previous/5)!==Math.floor(next/5)?'medium':null;
}
