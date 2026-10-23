import './mySystem.css';

/** Owner principles from Notion task 68, edited 2026-10-23.
 * https://app.notion.com/p/4028856479af80e28a01d52495d5eba0
 * Bundled reading content; execution policy remains server-owned.
 */
export function MySystem(){
 return <section className="card my-system" aria-labelledby="my-system-title">
  <h2 id="my-system-title">我的系统</h2>
  <ol role="list">
   <li>只在区间外 1.618 开单</li>
   <li>等信号确认</li>
   <li>及时止盈止损，或分批止盈</li>
  </ol>
 </section>;
}
