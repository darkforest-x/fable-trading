from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy_vps.sh"


def test_deploy_vps_mirrors_renamed_data_files() -> None:
    script = DEPLOY.read_text(encoding="utf-8")

    assert 'rsync -az --delete data/ma206/ "$VPS:$DIR/data/ma206/"' in script
    assert 'rsync -az --delete data/kline_fetched/ "$VPS:$DIR/data/kline_fetched/"' in script
