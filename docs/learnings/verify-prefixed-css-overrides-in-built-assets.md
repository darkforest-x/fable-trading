# Verify prefixed CSS overrides in the production build

- **Problem**: A new static card explicitly disabled backdrop blur, but the built mobile page still computed `blur(28px) saturate(1.8)`.
- **Dead end**: Selector specificity was already higher than the shared card rule. Reading only the source CSS could not explain why the override lost.
- **Effective path**: Inspecting the Vite 8.1.3 output showed that `backdrop-filter:none;-webkit-backdrop-filter:none` retained only the prefixed declaration. The test browser ignored that declaration and used the shared standard property. Putting the prefixed fallback first and the standard property last preserved both in the output; computed blur became `none` at 320px and 390px.
- **General rule**: Put vendor fallbacks before the standard property, then verify the generated CSS and the actual computed value. Source declarations and specificity checks alone do not prove a production style override works.
- **Scope**: `yoyo/discipline_app/mobile/src/mySystem.css`; build and browser evidence under `output/playwright/app-my-system/`. This records the observed build behavior, not a claim about every CSS optimizer or browser.
