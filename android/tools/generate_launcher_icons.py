"""Regenerate the Android launcher icons from the project logo.

Run from the repository root with the project virtualenv:

    .venv/Scripts/python.exe android/tools/generate_launcher_icons.py

The Android icon was a hand-written vector of a blue square and a page outline
that had nothing to do with the product's actual mark. This derives every
density from `.github/assets/logo.png`, so the phone icon and the desktop icon
cannot drift apart.

Adaptive icons are drawn on a 108dp canvas of which only the middle 66dp is
guaranteed to survive the launcher's mask; the rest is cropped and used for
parallax. The logo fills ~89% of its own canvas, so it has to be scaled into
that safe zone rather than dropped in whole.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[2]
LOGO = REPO / ".github" / "assets" / "logo.png"
RES = REPO / "android" / "app" / "src" / "main" / "res"

# Density buckets as multiples of the mdpi baseline.
DENSITIES = {
    "mdpi": 1.0,
    "hdpi": 1.5,
    "xhdpi": 2.0,
    "xxhdpi": 3.0,
    "xxxhdpi": 4.0,
}

ADAPTIVE_DP = 108  # Full adaptive-icon canvas.
LEGACY_DP = 48  # Pre-26 launcher icon.
MARK_DP = 48  # In-app mark drawn in the header.

# Fraction of the 108dp canvas the artwork is allowed to occupy. The safe zone
# is 66/108 = 0.611; sitting just inside it keeps the mark clear of the mask on
# a circular launcher without looking lost in the middle.
SAFE_FRACTION = 0.58


def trimmed_logo() -> Image.Image:
    """The logo cropped to its ink, so scaling is about the mark, not padding."""
    logo = Image.open(LOGO).convert("RGBA")
    box = logo.split()[3].getbbox()
    if box is None:
        raise SystemExit(f"{LOGO} has no visible pixels")
    return logo.crop(box)


def fitted(art: Image.Image, canvas_px: int, fraction: float) -> Image.Image:
    """Centre `art` on a transparent square, scaled to `fraction` of its side."""
    target = max(1, round(canvas_px * fraction))
    scale = min(target / art.width, target / art.height)
    resized = art.resize(
        (max(1, round(art.width * scale)), max(1, round(art.height * scale))),
        Image.LANCZOS,
    )
    canvas = Image.new("RGBA", (canvas_px, canvas_px), (0, 0, 0, 0))
    canvas.paste(
        resized,
        ((canvas_px - resized.width) // 2, (canvas_px - resized.height) // 2),
        resized,
    )
    return canvas


def monochrome(layer: Image.Image) -> Image.Image:
    """A themed-icon layer: shape only, in black, tinted by the launcher.

    Android 13 asks for one flat silhouette. Feeding it the alpha channel keeps
    the page-and-arrows outline; feeding it the colours would produce a blob.
    """
    alpha = layer.split()[3]
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.putalpha(alpha)
    return out


def circular(layer: Image.Image) -> Image.Image:
    """Legacy round icon: the square artwork clipped to a circle."""
    mask = Image.new("L", layer.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, layer.width - 1, layer.height - 1), fill=255)
    out = layer.copy()
    out.putalpha(mask)
    return out


def on_white(layer: Image.Image) -> Image.Image:
    """Flatten onto the brand's white ground for the pre-26 square icon."""
    base = Image.new("RGBA", layer.size, (255, 255, 255, 255))
    base.alpha_composite(layer)
    return base


def main() -> None:
    art = trimmed_logo()
    written = []

    for bucket, factor in DENSITIES.items():
        out_dir = RES / f"mipmap-{bucket}"
        out_dir.mkdir(parents=True, exist_ok=True)

        adaptive_px = round(ADAPTIVE_DP * factor)
        foreground = fitted(art, adaptive_px, SAFE_FRACTION)
        foreground.save(out_dir / "ic_launcher_foreground.png")
        monochrome(foreground).save(out_dir / "ic_launcher_monochrome.png")

        # The legacy icon has no mask eating its edges, so the mark can be
        # bigger than it may be on the adaptive foreground.
        legacy_px = round(LEGACY_DP * factor)
        legacy = fitted(art, legacy_px, 0.88)
        on_white(legacy).save(out_dir / "ic_launcher.png")
        on_white(circular(fitted(art, legacy_px, 0.66))).save(
            out_dir / "ic_launcher_round.png"
        )

        # A separate drawable for the header. Compose's painterResource reads
        # vectors and bitmaps only, so it cannot be pointed at R.mipmap
        # .ic_launcher: on API 26+ that resolves to the <adaptive-icon> XML and
        # throws at first composition. This is a plain PNG, full bleed, with no
        # launcher safe-zone padding to waste at 36dp.
        mark_dir = RES / f"drawable-{bucket}"
        mark_dir.mkdir(parents=True, exist_ok=True)
        mark_px = round(MARK_DP * factor)
        fitted(art, mark_px, 1.0).save(mark_dir / "ic_app_mark.png")

        written.append(
            f"mipmap-{bucket}: {adaptive_px}px adaptive, {legacy_px}px legacy; "
            f"drawable-{bucket}: {mark_px}px mark"
        )

    for line in written:
        print(line)


if __name__ == "__main__":
    main()
