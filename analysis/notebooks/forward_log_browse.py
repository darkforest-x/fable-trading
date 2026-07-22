import marimo

__generated_with = "0.17.6"
app = marimo.App(width="medium")


@app.cell
def _():
    """Minimal forward_log / judgment CSV browser (CPU, side venv).

    Start:
      .venv-tools/bin/marimo edit analysis/notebooks/forward_log_browse.py
      # or headless:
      .venv-tools/bin/marimo run analysis/notebooks/forward_log_browse.py

    Does not touch holdout, MPS, VPS, or promote.
    """
    from pathlib import Path

    import pandas as pd

    PROJECT = Path(__file__).resolve().parents[2]
    CANDIDATES = [
        PROJECT / "data/forward_log.csv",
        PROJECT / "analysis/output/forward_log_vps_20260721.csv",
        PROJECT / "data/judgment_dataset_v2_strict.csv",
    ]
    return CANDIDATES, PROJECT, Path, pd


@app.cell
def _(CANDIDATES, mo, pd):
    existing = [p for p in CANDIDATES if p.is_file()]
    labels = [f"{p.name} ({p.stat().st_size} B)" for p in existing]
    picker = mo.ui.dropdown(
        options={lab: str(p) for lab, p in zip(labels, existing)},
        label="CSV",
        value=labels[0] if labels else None,
    )
    mo.vstack(
        [
            mo.md("## Forward / judgment CSV browse"),
            mo.md("本机只读。优先 `forward_log.csv`；本地空表时选 VPS 快照或 judgment。"),
            picker,
        ]
    )
    return existing, picker


@app.cell
def _(mo, pd, picker):
    if not picker.value:
        mo.md("没有可用 CSV")
        df = pd.DataFrame()
    else:
        df = pd.read_csv(picker.value)
        mo.vstack(
            [
                mo.md(f"**path** `{picker.value}` · rows={len(df)} cols={len(df.columns)}"),
                mo.ui.table(df.head(200)),
            ]
        )
    return (df,)


@app.cell
def _(df, mo, pd):
    if df.empty:
        mo.md("_empty_")
        return
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        mo.md("无数值列")
        return
    mo.vstack([mo.md("### describe()"), mo.ui.table(numeric.describe().reset_index())])
    return


@app.cell
def _():
    import marimo as mo

    return (mo,)


if __name__ == "__main__":
    app.run()
