/* Minimal DOM/event test double; no browser packages or network services. */
const test = require("node:test");
const assert = require("node:assert/strict");
const install = require("../yoyo/review/label_studio_queue_entry.js");

class Element {
  constructor(win, tag, text = "", attrs = {}) {
    Object.assign(this, { win, tagName: tag.toUpperCase(), ownText: text,
      attrs: { ...attrs }, children: [], parentElement: null, disabled: false,
      style: { display: "block", visibility: "visible" } });
  }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } return this; }
  remove() { this.parentElement.children = this.parentElement.children.filter(x => x !== this); this.parentElement = null; }
  get textContent() { return this.ownText + this.children.map(x => x.textContent).join(""); }
  get isConnected() { return this === this.win.document.documentElement || !!this.parentElement?.isConnected; }
  getAttribute(key) { return Object.hasOwn(this.attrs, key) ? this.attrs[key] : null; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  removeAttribute(key) { delete this.attrs[key]; }
  matches(selector) {
    return selector.split(",").some(part => {
      part = part.trim();
      const path = part.split(/\s+/);
      if (path.length > 1) return this.matches(path.pop()) && !!this.parentElement?.closest(path.join(" "));
      if (part === ":disabled") return this.disabled;
      if (part.startsWith(".")) return (this.attrs.class || "").split(" ").includes(part.slice(1));
      const attr = part.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
      if (attr) return attr[2] === undefined ? Object.hasOwn(this.attrs, attr[1]) : this.attrs[attr[1]] === attr[2];
      return this.tagName === part.toUpperCase();
    });
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  getClientRects() {
    for (let el = this; el; el = el.parentElement) if (el.style.display === "none") return [];
    return this.isConnected ? [{}] : [];
  }
  click() { if (!this.disabled) return this.win.click(this); }
}

function fixture(url = "http://192.168.1.4:8081/projects/77/data?tab=44") {
  const listeners = new Map(), observers = [], counters = { all: 0, filtered: 0 };
  const win = {
    location: { href: url },
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
    getComputedStyle(el) { return el.style; },
    MutationObserver: class {
      constructor(fn) { this.fn = fn; this.active = true; observers.push(this); }
      observe() {}
      disconnect() { this.active = false; }
    },
    mutate() { for (const o of observers) if (o.active) o.fn(); },
    click(target, extra = {}) {
      const event = { target, button: 0, defaultPrevented: false, stopped: false, ...extra,
        preventDefault() { this.defaultPrevented = true; },
        stopImmediatePropagation() { this.stopped = true; } };
      for (const fn of listeners.get("click") || []) { fn(event); if (event.stopped) break; }
      if (!event.stopped) target.closest("button")?.nativeClick?.();
      return event;
    },
    fetch() { throw Error("Unexpected API call"); },
  };
  Object.defineProperty(win, "localStorage", { get() { throw Error("Unexpected storage access"); } });
  const el = (tag, text, attrs) => new Element(win, tag, text, attrs);
  const root = el("html");
  win.document = { documentElement: root, querySelectorAll: selector => root.querySelectorAll(selector) };
  const toolbar = el("div", "", { class: "dm-tab-panel" });
  const group = el("div"), primaryGroup = el("div");
  const main = el("button", "Label All Tasks", { title: "original title" });
  const trigger = el("button", "", { "aria-label": "Toggle open" });
  const displayed = el("button", "Label Tasks As Displayed");
  displayed.style.display = "none";
  main.nativeClick = () => counters.all++;
  displayed.nativeClick = () => counters.filtered++;
  root.append(toolbar.append(group.append(primaryGroup.append(main, trigger), displayed)));
  return { win, root, toolbar, group, primaryGroup, main, displayed, counters, el };
}

test("forwards a normal main-button click to the hidden native AsDisplayed button", () => {
  const f = fixture(), state = install(f.win);
  const event = f.win.click(f.main);
  assert.equal(event.defaultPrevented, true);
  assert.deepEqual(f.counters, { all: 0, filtered: 1 });
  assert.equal(state.forwardedActivations, 1);
  assert.match(f.main.getAttribute("title"), /当前列表/);
  assert.equal(f.main.textContent, "Label All Tasks");
});

for (const label of ["Label 1043 Tasks", "Label 1 Task", "Label 0 Tasks", " Label\n 17 Tasks "]) {
  test(`forwards the native count/plural variant: ${JSON.stringify(label)}`, () => {
    const f = fixture(); f.main.ownText = label; install(f.win); f.win.click(f.main);
    assert.deepEqual(f.counters, { all: 0, filtered: 1 });
  });
}

test("a nested main-button child also activates the supported entry", () => {
  const f = fixture(); f.main.ownText = "";
  const child = f.el("span", "Label All Tasks"); f.main.append(child);
  install(f.win); f.win.click(child); assert.equal(f.counters.filtered, 1);
});

for (const url of [
  "http://127.0.0.1:8081/projects/77/data?tab=44&labeling=1",
  "http://127.0.0.1:8081/projects/77/data?tab=44&task=37276",
  "http://127.0.0.1:8081/projects/77/data?tab=44&task=",
  "http://127.0.0.1:8081/projects/76/data?tab=44",
  "http://127.0.0.1:8081/projects/77/data?tab=45",
  "http://127.0.0.1:8081/projects/77/data",
  "https://example.com/projects/77/data?tab=44",
]) test(`does not intercept out-of-scope entry: ${url}`, () => {
  const f = fixture(url); install(f.win); const event = f.win.click(f.main);
  assert.equal(event.defaultPrevented, false); assert.equal(f.counters.filtered, 0);
  assert.equal(f.main.getAttribute("title"), "original title");
});

for (const defect of ["missing", "multiple", "wrong-container", "multiple-main", "target-disabled",
  "target-aria-disabled", "target-inert", "main-disabled", "main-aria-disabled", "modal"]) {
  test(`protected main entry cannot fall back to All: ${defect}`, () => {
    const f = fixture();
    if (defect === "missing") f.displayed.remove();
    if (defect === "multiple") f.group.append(f.el("button", "Label Tasks As Displayed"));
    if (defect === "wrong-container") { f.displayed.remove(); f.toolbar.append(f.displayed); }
    if (defect === "multiple-main") f.primaryGroup.append(f.el("button", "Label All Tasks"));
    if (defect === "target-disabled") f.displayed.disabled = true;
    if (defect === "target-aria-disabled") f.displayed.setAttribute("aria-disabled", "true");
    if (defect === "target-inert") f.displayed.setAttribute("inert", "");
    if (defect === "main-disabled") f.main.disabled = true;
    if (defect === "main-aria-disabled") f.main.setAttribute("aria-disabled", "true");
    if (defect === "modal") f.root.append(f.el("div", "", { role: "dialog" }));
    install(f.win); const event = f.win.click(f.main);
    assert.equal(event.defaultPrevented, true);
    assert.deepEqual(f.counters, { all: 0, filtered: 0 });
    assert.match(f.main.getAttribute("title"), /未启动全部任务/);
  });
}

test("a closed modal does not block the current list", () => {
  const f = fixture(), modal = f.el("div", "", { role: "dialog" });
  modal.style.display = "none"; f.root.append(modal);
  install(f.win); f.win.click(f.main); assert.equal(f.counters.filtered, 1);
});

for (const extra of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true }, { button: 1 }]) {
  test(`modifier/non-primary gesture is not forwarded: ${JSON.stringify(extra)}`, () => {
    const f = fixture(); install(f.win); const event = f.win.click(f.main, extra);
    assert.equal(event.defaultPrevented, false); assert.equal(f.counters.filtered, 0);
  });
}

test("native AsDisplayed clicks and buttons outside the toolbar remain untouched", () => {
  const f = fixture(), other = f.el("button", "Label All Tasks"); f.root.append(other);
  install(f.win);
  assert.equal(f.win.click(f.displayed).defaultPrevented, false);
  assert.equal(f.counters.filtered, 1);
  assert.equal(f.win.click(other).defaultPrevented, false);
});

test("dispose, idempotent installation, and subsequent installation restore ownership", () => {
  const f = fixture(), state = install(f.win);
  assert.equal(install(f.win), state);
  f.win.click(f.main); assert.equal(f.counters.filtered, 1);
  state.dispose(); assert.equal(f.main.getAttribute("title"), "original title");
  f.win.click(f.main); assert.equal(f.counters.all, 1);
  const next = install(f.win); assert.notEqual(next, state);
  f.win.click(f.main); assert.equal(f.counters.filtered, 2);
});

test("SPA scope changes restore help and never intercept task/detail pages", () => {
  const f = fixture(); install(f.win);
  f.win.location.href += "&labeling=1"; f.win.mutate();
  assert.equal(f.main.getAttribute("title"), "original title");
  assert.equal(f.win.click(f.main).defaultPrevented, false);
  assert.equal(f.counters.filtered, 0);
});
