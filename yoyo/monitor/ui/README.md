# Workbench UI components

The existing monitor uses vanilla JavaScript. Bootstrap 5.3.8 supplies selected
buttons, forms, tabs and dropdown components; Tabler Icons 3.49.0 supplies SVGs.
Versions and transitive dependencies are pinned in `package-lock.json`.

```sh
cd yoyo/monitor/ui
npm ci --ignore-scripts --no-audit --no-fund
npm run build
```

The build writes local assets to `../static/`. Runtime pages do not use a CDN.
Keep generated assets with their sources and upstream MIT licenses. Bootstrap
CSS is scoped beneath `.spike-ui`; reboot, global layout and modal controllers
are deliberately excluded. Only the dropdown JavaScript and its Popper dependency
are bundled. The existing native dialogs retain their focus and confirmation flow.

`module-loader.js` loads these assets for the YOLO detection workspace.
`yolo-compact.css` maps visual states to existing light/dark theme tokens and owns
workspace density. Account routing, order sizing and execution remain in their
existing modules.

References: [selective imports](https://getbootstrap.com/docs/5.3/customize/optimize/),
[dropdown interactions](https://getbootstrap.com/docs/5.3/components/dropdowns/).
