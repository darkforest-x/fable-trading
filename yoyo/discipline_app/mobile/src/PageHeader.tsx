import {ThemeToggle} from './ThemeToggle';
/** Shared compact secondary header, from Stitch a2d59ef23b104df589c92a6e9033decd. */
import {Icon} from './Icon';

export function PageHeader({title,subtitle,onSettings,onBack}:{title:string;subtitle?:string;onSettings?:()=>void;onBack?:()=>void}){
 return <header className="page-header"><div className="page-header-row">
  {onBack&&<button className="settings-gear page-back" aria-label="返回上一页" onClick={onBack}><Icon name="chevron" size={21}/></button>}
  <h1>{title}</h1><ThemeToggle/>
  {onSettings&&<button className="settings-gear" aria-label="设置" onClick={onSettings}><Icon name="settings" size={21}/></button>}
 </div>{subtitle&&<p>{subtitle}</p>}</header>;
}
