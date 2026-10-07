"""打印置顶频道名称对应的 ID，便于核对 config/pinned_channels.yaml"""

from __future__ import annotations

from yoyo.copier.pinned_channels import load_pinned_config, resolve_pinned_channel_ids


def main() -> None:
    cfg = load_pinned_config()
    ids, mapping = resolve_pinned_channel_ids()
    print("guild:", cfg.get("guild_id"))
    print("resolved:", len(ids))
    for name, cid in mapping.items():
        print(f"  #{name} -> {cid}")
    missing = [n for n in cfg.get("channels", []) if str(n).lstrip("#") not in mapping]
    if missing:
        print("not found:", missing)


if __name__ == "__main__":
    main()
