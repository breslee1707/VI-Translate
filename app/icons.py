"""The window's small icons, drawn at start-up instead of shipped as files.

Each is drawn four times too large and scaled down, so its edges stay smooth at
any display scaling, in a light and a dark variant to match the window. Text
glyphs could not do this: "✓" and "✕" come from whichever font Tk finds, at
different sizes and weights on every system. Nothing here touches Tk.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

# Drawn at this many times the size CustomTkinter is given; it scales the
# image down to the display, so the extra pixels become anti-aliasing.
SUPERSAMPLE = 4
BADGE_SIZE = 22
DROP_SIZE = 64
GLYPH_SIZE = 14
SPINNER_FRAMES = 12

# Every colour is a (light, dark) pair, like the window's own palette.
BADGE_FILLS = {
    "done": ("#1E9E5A", "#2FB874"),
    "partial": ("#E08A00", "#D99A3A"),
    "paused": ("#E08A00", "#D99A3A"),
    "failed": ("#DC3940", "#E5534B"),
    "skipped": ("#9AA3AD", "#5F6872"),
    "info": ("#176CFF", "#3D86FF"),
}
QUEUED_RING = ("#C3CAD3", "#4A5059")
SPINNER_TRACK = ("#DCE7FF", "#24324A")
SPINNER_ARC = ("#176CFF", "#5B9BFF")
DROP_DISC = ("#EAF1FF", "#1C2940")
DROP_GLYPH = ("#176CFF", "#6EA8FF")
GLYPH_COLORS = {"plus": ("#1259D9", "#7EB0FF"), "close": ("#5E6772", "#9AA2AC")}
MARK = "#FFFFFF"
BADGE_STATES = ("queued", "running", *BADGE_FILLS)


def _stroke(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], width: int, fill: str) -> None:
    """A polyline with round ends, which ImageDraw.line does not give."""
    draw.line(points, fill=fill, width=width, joint="curve")
    radius = width / 2
    for x, y in points:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _scaled(image: Image.Image, size: int) -> Image.Image:
    """Twice the display size: sharp on a 200% screen, and CustomTkinter
    scales it down smoothly for anything less."""
    return image.resize((size * 2, size * 2), Image.Resampling.LANCZOS)


def badge(state: str, dark: bool = False, frame: int = 0) -> Image.Image:
    """The round status mark in front of a queued file.

    ``frame`` turns the running spinner's arc by 1/SPINNER_FRAMES of a turn.
    """
    if state not in BADGE_STATES:
        raise ValueError(f"no badge for {state!r}")
    pick = 1 if dark else 0
    side = BADGE_SIZE * SUPERSAMPLE
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = (4, 4, side - 4, side - 4)
    middle = side / 2
    if state == "queued":
        draw.ellipse(box, outline=QUEUED_RING[pick], width=7)
    elif state == "running":
        start = frame % SPINNER_FRAMES * 360 / SPINNER_FRAMES - 90
        draw.ellipse(box, outline=SPINNER_TRACK[pick], width=9)
        draw.arc(box, start=start, end=start + 110, fill=SPINNER_ARC[pick], width=9)
    else:
        draw.ellipse(box, fill=BADGE_FILLS[state][pick])
        if state == "done":
            _stroke(draw, [(27, 45), (39, 57), (62, 33)], 9, MARK)
        elif state == "partial":
            _stroke(draw, [(middle, 25), (middle, 49)], 9, MARK)
            draw.ellipse((middle - 5.5, 57, middle + 5.5, 68), fill=MARK)
        elif state == "paused":
            _stroke(draw, [(36, 31), (36, 57)], 9, MARK)
            _stroke(draw, [(52, 31), (52, 57)], 9, MARK)
        elif state == "failed":
            _stroke(draw, [(32, 32), (56, 56)], 9, MARK)
            _stroke(draw, [(56, 32), (32, 56)], 9, MARK)
        elif state == "skipped":
            _stroke(draw, [(30, middle), (58, middle)], 9, MARK)
        elif state == "info":
            draw.ellipse((middle - 5.5, 20, middle + 5.5, 31), fill=MARK)
            _stroke(draw, [(middle, 41), (middle, 64)], 9, MARK)
    return _scaled(image, BADGE_SIZE)


def drop_icon(dark: bool = False) -> Image.Image:
    """An arrow into a tray on a soft disc: the empty queue's invitation."""
    pick = 1 if dark else 0
    side = DROP_SIZE * SUPERSAMPLE
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, side - 1, side - 1), fill=DROP_DISC[pick])
    glyph = DROP_GLYPH[pick]
    _stroke(draw, [(128, 70), (128, 150)], 14, glyph)
    _stroke(draw, [(98, 121), (128, 151), (158, 121)], 14, glyph)
    _stroke(draw, [(80, 152), (80, 184), (176, 184), (176, 152)], 14, glyph)
    return _scaled(image, DROP_SIZE)


def glyph(kind: str, dark: bool = False) -> Image.Image:
    """A small line glyph beside a button's text: "plus" adds files, "close"
    takes one out of the queue."""
    if kind not in GLYPH_COLORS:
        raise ValueError(f"no glyph named {kind!r}")
    side = GLYPH_SIZE * SUPERSAMPLE
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = GLYPH_COLORS[kind][1 if dark else 0]
    if kind == "plus":
        _stroke(draw, [(side / 2, 12), (side / 2, side - 12)], 7, color)
        _stroke(draw, [(12, side / 2), (side - 12, side / 2)], 7, color)
    else:
        _stroke(draw, [(16, 16), (side - 16, side - 16)], 7, color)
        _stroke(draw, [(side - 16, 16), (16, side - 16)], 7, color)
    return _scaled(image, GLYPH_SIZE)
