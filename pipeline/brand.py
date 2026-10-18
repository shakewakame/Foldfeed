"""Brand: the name, the colour scheme, and the logo (a cartoon alpha helix with a bound ligand).

    python3 -m pipeline.brand        # writes assets/logo.svg and assets/logo-mark.svg

Colour scheme 藍と朱 (indigo ink and vermilion), chosen by the owner on 2026-10-10. The structure
figures (render.py) and the website (site.py) both take their colours from THEME.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .common import ROOT

SITE_NAME = "FoldFeed"
SITE_NAME_EN = "構造生物学デイリー"  # Japanese descriptor shown under the wordmark

FONT = "'Hiragino Sans','Hiragino Kaku Gothic ProN','Noto Sans JP','Yu Gothic',Meiryo,sans-serif"


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    ground: str      # page background
    paper: str       # background of the figures
    ink: str         # headings and body text
    ink2: str        # secondary text
    desc: str        # description text under a headline: darker than the labels, lighter than the title
    muted: str       # labels
    hair: str        # the few thin rules that are kept
    accent: str      # the single accent (first-structure mark, contents numbers, quote label)
    accent_ink: str  # the accent as readable text on the ground
    soft: str        # tinted panel (figure placeholders)
    entities: tuple  # colours of the polymer entities in the structure figure
    ligand: str      # the bound ligand in the logo
    loop_back: str   # logo: the far side of the helix
    loop_front: str  # logo: the near side of the helix


THEMES = {
    "b": Theme("b", "藍と朱", ground="#F0F1EF", paper="#FFFFFF", ink="#1B2638", ink2="#283346", desc="#2F3A4C", muted="#4A5466",
               hair="#CDD1D8", accent="#C8442A", accent_ink="#B33A22", soft="#E4E7EB",
               entities=("#3C5A8C", "#C9A04B", "#5F9296", "#A5707A", "#7C8AA8", "#8A9A5B", "#9C7B55", "#6E6E8E"),
               ligand="#D4452B", loop_back="#8DA0C4", loop_front="#FFFFFF"),
}
DEFAULT_THEME = "b"
THEME = THEMES[DEFAULT_THEME]


def _coil_points(x: float, y: float, s: float, turns: float = 3.5, steps: int = 260):
    """A cartoon alpha helix seen from the side: loops drifting up and to the right."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        th = t * turns * 2 * math.pi
        px = x + s * (0.16 + 0.56 * t + 0.165 * math.cos(th))
        py = y + s * (0.52 - 0.16 * (t - 0.5) - 0.185 * math.sin(th))
        pts.append((px, py, math.cos(th)))
    return pts


def logo_mark(x: float, y: float, s: float, theme: Theme = THEME) -> str:
    """SVG fragment: the mark in an s×s rounded square at (x, y)."""
    pts = _coil_points(x, y, s)
    back_segs, front_segs = [], []
    for (x1, y1, c1), (x2, y2, _) in zip(pts, pts[1:]):
        (front_segs if c1 >= 0 else back_segs).append(f"M{x1:.2f},{y1:.2f}L{x2:.2f},{y2:.2f}")
    w = s * 0.105  # thick, cartoon-ribbon weight
    dot_x, dot_y = x + s * 0.805, y + s * 0.205
    return (f'<rect x="{x}" y="{y}" width="{s}" height="{s}" rx="{s * 0.24:.2f}" fill="{theme.ink}"/>'
            # back loops: soft, slightly thinner, sitting behind
            f'<path d="{"".join(back_segs)}" stroke="{theme.loop_back}" stroke-width="{w * 0.92:.2f}" '
            f'stroke-linecap="round" fill="none"/>'
            # front loops: full weight
            f'<path d="{"".join(front_segs)}" stroke="{theme.loop_front}" stroke-width="{w:.2f}" '
            f'stroke-linecap="round" fill="none"/>'
            # bound ligand
            f'<circle cx="{dot_x:.2f}" cy="{dot_y:.2f}" r="{s * 0.092:.2f}" fill="{theme.ligand}" '
            f'stroke="{theme.ink}" stroke-width="{s * 0.02:.2f}"/>')


def logo_svg(height: int = 64, subtitle: bool = True) -> str:
    s = height
    width = int(s * 6.6)
    sub = (f'<text x="{s * 1.26:.1f}" y="{s * 0.88:.1f}" font-size="{s * 0.16:.1f}" '
           f'letter-spacing="{s * 0.02:.1f}" fill="{THEME.muted}">{SITE_NAME_EN} · β</text>') if subtitle else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{s}" viewBox="0 0 {width} {s}" '
            f'font-family="{FONT}">'
            f'{logo_mark(0, 0, s)}'
            f'<text x="{s * 1.24:.1f}" y="{s * 0.57:.1f}" font-size="{s * 0.45:.1f}" font-weight="700" '
            f'fill="{THEME.ink}">{SITE_NAME}</text>{sub}</svg>\n')


def mark_svg(size: int = 128) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'{logo_mark(0, 0, size)}</svg>\n')


def main():
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    (out / "logo.svg").write_text(logo_svg(), encoding="utf-8")
    (out / "logo-mark.svg").write_text(mark_svg(), encoding="utf-8")
    print(f"wrote {out / 'logo.svg'} and {out / 'logo-mark.svg'}")


if __name__ == "__main__":
    main()
