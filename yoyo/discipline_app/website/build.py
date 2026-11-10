"""Render verified Apple release links into the standalone product site; installation links also work without JavaScript."""
import json
import re
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHANNELS = {
    "testflight": (r"https://testflight\.apple\.com/join/[A-Za-z0-9]+", "打开 TestFlight", "内测尚未开放"),
    "app_store": (r"https://apps\.apple\.com/(?:[a-z]{2}/)?app/(?:[^/?#\s]+/)?id[0-9]+", "前往 App Store", "正式版尚未上架"),
}


def render(template, releases):
    if set(releases) != set(CHANNELS):
        raise ValueError("Provide exactly the two Apple release channels")
    for channel, (pattern, action, pending) in CHANNELS.items():
        marker = "{{" + channel + "}}"
        if template.count(marker) != 1:
            raise ValueError(f"Expected one {marker} in the site template")
        url = releases[channel]
        if url is None:
            element = f'<span class="channel-state">{pending}</span>'
        elif isinstance(url, str) and re.fullmatch(pattern, url):
            element = f'<a class="release-link" href="{escape(url, quote=True)}">{action} ↗</a>'
        else:
            raise ValueError(f"Use a verified Apple URL or null for {channel}")
        template = template.replace(marker, element)
    return template


if __name__ == "__main__":
    page = render((ROOT / "index.template.html").read_text(), json.loads((ROOT / "releases.json").read_text()))
    (ROOT / "dist").mkdir(exist_ok=True)
    (ROOT / "dist" / "index.html").write_text(page)
