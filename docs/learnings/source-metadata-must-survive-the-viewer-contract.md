# Saved provenance is not evidence that the reader can inspect it

- **Problem**: A MA6 report saved `source.evidenceFlow` as a string. The source viewer accepted only an array, so it silently omitted the Evidence flow tab even though the source JSON looked complete.
- **Dead end**: A successful app build and a populated metadata field did not prove the source interface exposed the explanation.
- **Effective path**: Open the actual component source menu, compare the visible tabs with the data contract, and supply the documented array of title/detail steps. Keep the shared viewer unchanged.
- **General rule**: Verify at least one source panel in the delivered artifact. Metadata has to survive the viewer contract, not merely exist on disk.
- **Scope**: `yoyo/evaluation/monday_ma6_report.py`; Data App source inspection. No trading rules or historical fills changed.
