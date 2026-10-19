# iPhone search zoom must be checked against the final input style

- **Problem**: Focusing the contract search enlarged the interface and pushed the close control out of reach.
- **Dead end**: A generic 16px input rule was insufficient because a more specific search rule overrode it with 14px. Disabling user zoom would conceal the issue and reduce accessibility.
- **Effective path**: Give the search and modal inputs a computed 16px minimum, constrain the dialog to the visual viewport, keep its header and close control outside the scroll list, and restore focus without scrolling.
- **Rule**: Inspect effective component styles and keyboard viewport bounds, not only the global CSS or a desktop screenshot.
- **Scope**: `mobile/src/style.css`, `useKeyboard.ts`, `Dialog.tsx`; 390px synthetic browser acceptance supplements, but does not replace, an actual iPhone keyboard check.
