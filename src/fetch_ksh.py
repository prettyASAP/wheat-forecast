"""KSH búzahozam letöltése (19.1.2.4. tábla).

Az oldalt letöltjük, kiszedjük belőle a relatív xlsx/csv letöltési linket
(nem hardcode-olunk fájl-URL-t, mert az verzióváltáskor változhat — brief 5.1),
majd mindkét fájlt lementjük a data/raw/ksh/ alá. Idempotens: meglévő fájlt nem
tölt újra --force nélkül.

Futtatás:  python -m src.fetch_ksh [--force]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

from src import config

TIMEOUT = 60


def _get(url: str) -> requests.Response:
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "wheat-forecast/1.0"})
    resp.raise_for_status()
    return resp


def find_download_links(page_html: str, page_url: str) -> dict[str, str]:
    """A KSH oldal HTML-jéből kiszedi az xlsx és csv letöltési linkeket.

    Visszaad: {"xlsx": abszolút_url, "csv": abszolút_url} — csak a megtalált kulcsokkal.
    """
    links: dict[str, str] = {}
    for ext in ("xlsx", "csv"):
        m = re.search(rf'href="([^"]+\.{ext})"', page_html, flags=re.IGNORECASE)
        if m:
            links[ext] = urljoin(page_url, m.group(1))
    return links


def download(url: str, dest: Path, force: bool) -> bool:
    """Letölti az URL-t a dest-be. True, ha ténylegesen letöltött, False ha kihagyta."""
    if dest.exists() and not force:
        print(f"  [skip] már megvan: {dest.name} ({dest.stat().st_size} bájt)")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = _get(url)
    dest.write_bytes(resp.content)
    print(f"  [ok]   {dest.name} <- {url} ({len(resp.content)} bájt)")
    return True


def sanity_check_csv(csv_path: Path) -> None:
    """Rövid szanity: kiírja az évoszlopokat és a sorok számát."""
    text = csv_path.read_bytes().decode(config.KSH_ENCODING, errors="replace")
    lines = text.splitlines()
    header = next((ln for ln in lines if "Területi egység szintje" in ln), "")
    years = [c for c in header.split(";") if c.strip().isdigit()]
    print(f"  szanity: {len(lines)} sor, évoszlopok: "
          f"{years[0] if years else '?'}..{years[-1] if years else '?'} ({len(years)} év)")


def main(force: bool = False) -> None:
    print("KSH búzahozam letöltése")
    print(f"  oldal: {config.KSH_PAGE_URL}")
    page = _get(config.KSH_PAGE_URL).content.decode(config.KSH_ENCODING, errors="replace")
    links = find_download_links(page, config.KSH_PAGE_URL)

    if not links:
        sys.exit("HIBA: nem találtam letöltési linket (xlsx/csv) a KSH oldalon. "
                 "Lehet, hogy változott az oldal szerkezete — állj meg és ellenőrizd (brief 0. pont).")

    for ext, url in links.items():
        download(url, config.RAW_KSH / f"mez0071.{ext}", force)

    csv_path = config.RAW_KSH / "mez0071.csv"
    if csv_path.exists():
        sanity_check_csv(csv_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="KSH búzahozam letöltése")
    ap.add_argument("--force", action="store_true", help="Újratöltés akkor is, ha megvan")
    main(force=ap.parse_args().force)
