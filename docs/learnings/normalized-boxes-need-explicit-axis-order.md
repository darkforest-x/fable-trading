# Normalized boxes need explicit axis order across the full contract

- **Problem**: The Gemini prompt initially described xyxy while the workbench overlay consumed `[ymin, xmin, ymax, xmax]`. A valid-looking model box could be transposed on display.
- **Dead end**: Validating four integers, a 0..1000 range and positive area cannot detect swapped axis semantics. Each component can pass its own validation while the combined picture is wrong.
- **Effective path**: Name the axes and order in both the Pydantic schema description and the provider prompt, keep the frontend interpretation identical, and assert the shared convention in request tests. Inference output stays tied to the exact selected image.
- **General rule**: For visual coordinates, freeze axis order, normalization basis and image identity together before connecting the renderer. Numeric shape alone is not a spatial contract.
- **Related files**: `yoyo/vision_research/schemas.py`, `gemini.py`, `static/app.js`, `tests/vision_research/test_gemini.py`. This engineering fix does not validate model localization accuracy.
