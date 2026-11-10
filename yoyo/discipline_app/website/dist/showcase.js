(() => {
  const rows = [
    {scene:"chart",tag:"1.618 图表",sub:"周一 1.618",log:"图表周期",value:"15 分钟",time:"15m",title:"把触价放回 K 线里。",body:"默认打开 15 分钟图。先看周一高低，再看价格离区间有多远。",angle:[8,-21,6]},
    {scene:"touch",tag:"触价监控",sub:"区间参考线",log:"观察范围",value:"上沿 / 下沿",time:"1.618",title:"关键价位，一眼可见。",body:"按时间查看触价记录，对照每次触价的方向与评分。",angle:[5,-10,2]},
    {scene:"review",tag:"教学复盘",sub:"单笔回看",log:"示意标记",value:"入场 → 出场",time:"回看",title:"回到交易发生的那一刻。",body:"在一张图上对照入场、出场，以及持有期间的价格路径。",angle:[8,-25,4]}
  ];
  const by = id => document.getElementById(id);
  const tabs = [...document.querySelectorAll('#showcase [role="tab"]')], stage = by("stage"), device = by("device");
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const mini = window.SpikeChart?.mount(by("phone-chart"), {compact:true, scene:"chart"});
  let index = 0, drag = null, frame = 0, targetAngles = rows[0].angle;
  const clip = (v, min, max) => Math.min(max, Math.max(min, v));
  const angle = (rx, ry, rz) => {
    targetAngles = [rx, ry, rz];
    if (frame || motion.matches) return;
    frame = requestAnimationFrame(() => {
      ["--rx","--ry","--rz"].forEach((key, i) => device.style.setProperty(key, targetAngles[i] + "deg"));
      frame = 0;
    });
  };
  function select(next, focus = false) {
    index = (next + rows.length) % rows.length;
    const r = rows[index], num = String(index+1).padStart(2,"0");
    for (const [key, text] of Object.entries({"fig-index":num,"feature-index":num,"feature-title":r.title,"feature-body":r.body,"screen-tag":r.tag,"screen-code":num+" / 03","screen-sub":r.sub,"screen-log":r.log,"screen-value":r.value,"screen-time":r.time})) by(key).textContent=text;
    mini?.setScene(r.scene);
    tabs.forEach((t,i) => {t.tabIndex=i===index?0:-1;t.setAttribute("aria-selected",String(i===index));});
    by("feature-panel").setAttribute("aria-labelledby",tabs[index].id);
    if (!motion.matches) for (const el of [by("screen-inner"),by("feature-panel")]) {
      el.getAnimations().forEach(anim => anim.cancel());
      el.animate([{opacity:.4,transform:"translateY(3px)"},{opacity:1,transform:"translateY(0)"}],{duration:270,easing:"ease-out"});
    }
    angle(...r.angle);
    if(focus) tabs[index].focus();
  }
  tabs.forEach((t,i) => {
    t.addEventListener("click",()=>select(i));
    t.addEventListener("keydown",e=>{
      if(!["ArrowLeft","ArrowRight","Home","End"].includes(e.key))return;
      e.preventDefault();
      select(e.key==="Home"?0:e.key==="End"?2:i+(e.key==="ArrowRight"?1:-1),true);
    });
  });
  stage.addEventListener("pointerdown",e=>{
    if(!e.isPrimary || e.button!==0) return;
    drag={id:e.pointerId,x:e.clientX,y:e.clientY,kind:e.pointerType};
    stage.setPointerCapture(e.pointerId); stage.classList.add("dragging");
  });
  stage.addEventListener("pointermove",e=>{
    if(!drag || drag.id!==e.pointerId)return;
    const base=rows[index].angle;
    angle(base[0]-clip((e.clientY-drag.y)*.13,-12,12),base[1]+clip((e.clientX-drag.x)*.24,-30,30),base[2]);
  });
  function finish(e, ok){
    if (!drag || e.pointerId!==drag.id) return;
    const gesture=drag;drag=null;stage.classList.remove("dragging");
    if(stage.hasPointerCapture(e.pointerId))stage.releasePointerCapture(e.pointerId);
    const dx=e.clientX-gesture.x, dy=e.clientY-gesture.y;
    if(ok && gesture.kind!=="mouse" && Math.abs(dx)>38 && Math.abs(dx)>Math.abs(dy)*1.35) select(index+(dx<0?1:-1));
    else angle(...rows[index].angle);
  }
  stage.addEventListener("pointerup",e=>finish(e,true));
  stage.addEventListener("pointercancel",e=>finish(e,false));
  stage.addEventListener("lostpointercapture",e=>finish(e,false));
  motion.addEventListener("change",()=>angle(...rows[index].angle));

})();
