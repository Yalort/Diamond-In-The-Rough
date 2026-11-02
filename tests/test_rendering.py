from __future__ import annotations

import unittest
from unittest.mock import patch

from ctp500_app import rendering
from PIL import ImageFont

from ctp500_app.catalog import parse_category_csv
from ctp500_app.rendering import raster_bands, render_tag, tag_text, wrap_text


class RenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.item = parse_category_csv(
            "Weapons",
            0,
            (
                "Manufacturer,Name,Element,Rank,Range,Form Factor,Clip,Ammo,Special Notes,Price\n"
                'Caliber Collective,Very Long Arc Pistol,Arc,6,30,Pistol,10,Capacitor,"-",25\n'
            ).encode(),
        )[0]

    def test_category_aware_text_omits_placeholders(self) -> None:
        text = tag_text(self.item)
        self.assertIn("Very Long Arc Pistol", text)
        self.assertIn("Element: Arc", text)
        self.assertIn("Price: 25", text)
        self.assertNotIn("Special Notes", text)

    def test_tag_is_dynamic_and_printer_width(self) -> None:
        image = render_tag(self.item)
        self.assertEqual(image.width, 384)
        self.assertGreater(image.height, 100)

    def test_long_words_are_broken_to_width(self) -> None:
        font = ImageFont.load_default()
        lines = wrap_text("X" * 100, font, 30)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(font.getlength(line) <= 30 for line in lines))

    def test_raster_is_split_into_valid_bands(self) -> None:
        image = render_tag(self.item)
        bands = list(raster_bands(image, band_height=32))
        self.assertGreater(len(bands), 1)
        for payload in bands:
            self.assertEqual(payload[:4], b"\x1d\x76\x30\x00")
            self.assertEqual(payload[4:6], b"\x30\x00")  # 384 / 8
            height = payload[6] + payload[7] * 256
            self.assertEqual(len(payload), 8 + 48 * height)

    def test_oversized_printable_text_is_rejected_before_image_allocation(self) -> None:
        with patch.object(rendering, "MAX_RENDER_TEXT_CHARS", 10), patch.object(
            rendering.Image, "new", side_effect=AssertionError("image allocated")
        ):
            with self.assertRaisesRegex(ValueError, "allowed size"):
                render_tag(self.item)

    def test_oversized_width_is_rejected_before_image_allocation(self) -> None:
        with patch.object(rendering.Image, "new", side_effect=AssertionError("image allocated")):
            with self.assertRaisesRegex(ValueError, "allowed width"):
                render_tag(self.item, width=10**9)


if __name__ == "__main__":
    unittest.main()
