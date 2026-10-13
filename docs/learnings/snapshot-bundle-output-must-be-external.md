# Write a snapshot bundle outside the directory it archives

- **Problem**: Packaging a frozen snapshot can recursively package the output archive itself when the ZIP destination is nested under the snapshot root. The archive grows as it reads itself and never reaches a valid bounded bundle.
- **Dead end**: Relying on a caller to choose a safe destination allowed a nested output path; once the ZIP was open, it kept appearing in the recursive file walk.
- **Effective path**: Resolve both paths before writing and reject any bundle destination inside the snapshot root. Keep the bundle adjacent to the snapshot directory, not below it.
- **General rule**: When an operation recursively reads a tree while writing an artifact, validate that the output path is outside the input tree before opening the output.
- **Related**: `yoyo/evaluation/grade_a_two_month_replay.py`, `bundle_phase`.
