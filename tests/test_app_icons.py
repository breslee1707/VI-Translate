from __future__ import annotations

import unittest

try:
    from app.icons import (
        BADGE_SIZE,
        BADGE_STATES,
        DROP_SIZE,
        GLYPH_COLORS,
        GLYPH_SIZE,
        SPINNER_FRAMES,
        badge,
        drop_icon,
        glyph,
    )
except ImportError:  # Pillow is an app dependency
    badge = None


@unittest.skipIf(badge is None, "desktop app dependencies are not installed")
class IconTests(unittest.TestCase):
    """The window's icons are drawn at start-up, so a broken one would only
    show as a missing or blank badge in front of somebody's file."""

    def test_every_queue_state_has_a_badge_at_twice_its_display_size(self):
        for state in BADGE_STATES:
            for dark in (False, True):
                with self.subTest(state=state, dark=dark):
                    image = badge(state, dark)
                    self.assertEqual(image.mode, "RGBA")
                    self.assertEqual(image.size, (BADGE_SIZE * 2, BADGE_SIZE * 2))
                    self.assertIsNotNone(image.getbbox(), "the badge is blank")

    def test_corners_stay_transparent_so_any_background_shows_through(self):
        for state in BADGE_STATES:
            with self.subTest(state=state):
                self.assertEqual(badge(state).getpixel((0, 0))[3], 0)

    def test_the_running_spinner_turns_from_frame_to_frame(self):
        frames = [badge("running", frame=frame).tobytes() for frame in range(SPINNER_FRAMES)]
        self.assertEqual(len(set(frames)), SPINNER_FRAMES)
        self.assertEqual(badge("running", frame=SPINNER_FRAMES).tobytes(), frames[0])

    def test_each_theme_gets_its_own_colours(self):
        for state in ("done", "failed", "running"):
            with self.subTest(state=state):
                self.assertNotEqual(badge(state).tobytes(), badge(state, dark=True).tobytes())

    def test_an_unknown_state_or_glyph_is_an_error_not_a_blank(self):
        with self.assertRaises(ValueError):
            badge("exploded")
        with self.assertRaises(ValueError):
            glyph("star")

    def test_glyphs_and_the_drop_icon_have_their_sizes(self):
        for kind in GLYPH_COLORS:
            with self.subTest(kind=kind):
                self.assertEqual(glyph(kind).size, (GLYPH_SIZE * 2, GLYPH_SIZE * 2))
                self.assertIsNotNone(glyph(kind, dark=True).getbbox())
        self.assertEqual(drop_icon().size, (DROP_SIZE * 2, DROP_SIZE * 2))
        self.assertIsNotNone(drop_icon(dark=True).getbbox())


if __name__ == "__main__":
    unittest.main()
