import {ThemeToggle} from './ThemeToggle';
/** Shared compact secondary header, from Stitch a2d59ef23b104df589c92a6e9033decd. */
import {Icon} from './Icon';

import type {ReactNode} from 'react';
export function PageHeader({title,subtitle,onSettings,onBack,actions}:{title:string;subtitle?:string;onSettings?:()=>void;onBack?:()=>void;actions?:ReactNode}){
 return <header className="page-header"><div className="page-header-row">
  {onBack&&<button className="settings-gear page-back" aria-label="返回上一页" onClick={onBack}><Icon name="chevron" size={21}/></button>}
  <h1>{title}</h1>{actions}<ThemeToggle/>
  {onSettings&&<button className="settings-gear" aria-label="设置" onClick={onSettings}><Icon name="settings" size={21}/></button>}
 </div>{subtitle&&<p>{subtitle}</p>}</header>;
}
