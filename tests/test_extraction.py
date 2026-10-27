"""Synthetic regression tests for structured PDF extraction."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf
from PIL import Image

from stolas.extraction import extract_pdf, write_manifest


def _png_bytes() -> bytes:
    image = Image.new("RGB", (24, 18), (210, 40, 80))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _hebrew_font() -> Path | None:
    candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    )
    return next((path for path in candidates if path.exists()), None)


class ExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temporary.name) / "fixture.pdf"
        picture = _png_bytes()
        document = pymupdf.open()

        page = document.new_page(width=320, height=480)
        page.insert_image(pymupdf.Rect(30, 40, 150, 130), stream=picture)

        page = document.new_page(width=320, height=480)
        page.insert_image(pymupdf.Rect(-25, 35, 125, 145), stream=picture)

        page = document.new_page(width=320, height=480)
        page.insert_image(pymupdf.Rect(40, 60, 180, 165), stream=picture)

        page = document.new_page(width=320, height=480)
        page.insert_text((30, 45), "Native text beside an image")
        page.insert_image(pymupdf.Rect(30, 70, 150, 160), stream=picture)

        page = document.new_page(width=320, height=480)
        xref = page.insert_image(pymupdf.Rect(25, 40, 125, 115), stream=picture)
        page.insert_image(pymupdf.Rect(160, 40, 260, 115), xref=xref)

        page = document.new_page(width=320, height=480)
        shape = page.new_shape()
        shape.draw_rect(pymupdf.Rect(30, 40, 160, 130))
        shape.finish(color=(0.1, 0.2, 0.8), fill=(0.8, 0.7, 0.2), width=3,
                     stroke_opacity=0.75, fill_opacity=0.5)
        shape.commit()

        page = document.new_page(width=320, height=480)
        font = _hebrew_font()
        if font:
            # insert_text is not a shaping engine; reverse the input so that the
            # PDF's native extraction order is the logical Hebrew string.
            page.insert_text((30, 60), "םולש", fontname="HebrewFont",
                             fontfile=str(font), fontsize=18)

        document.new_page(width=320, height=480)
        document.save(cls.path)
        document.close()
        cls.manifest = extract_pdf(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def images(self, page_number):
        return [block for block in self.manifest["pages"][page_number - 1]["blocks"]
                if block["kind"] == "image"]

    def test_engine_and_schema_are_recorded(self):
        self.assertEqual(self.manifest["schema_version"], "0.2.0")
        self.assertEqual(self.manifest["extraction_engine"],
                         {"name": "PyMuPDF", "version": pymupdf.__version__})

    def test_image_inside_page_has_recoverable_metadata(self):
        images = self.images(1)
        self.assertEqual(len(images), 1)
        image = images[0]
        self.assertIsInstance(image["xref"], int)
        self.assertEqual((image["width"], image["height"]), (24, 18))
        self.assertEqual(len(image["content_sha256"]), 64)
        self.assertEqual(image["page_number"], 1)
        self.assertIn(image["ext"], {"png", "flate"})
        self.assertIn("image_info", image["detection_methods"])
        self.assertIn("page_resources", image["detection_methods"])

    def test_partially_outside_image_is_discovered(self):
        images = self.images(2)
        self.assertEqual(len(images), 1)
        self.assertLess(images[0]["bbox"][0], 0)

    def test_image_only_page_classification(self):
        page = self.manifest["pages"][2]
        self.assertEqual(page["content_type"], "image")
        self.assertFalse(page["has_native_text"])
        self.assertTrue(page["has_images"])
        self.assertTrue(page["requires_ocr_review"])

    def test_mixed_page_requests_raster_text_review(self):
        page = self.manifest["pages"][3]
        self.assertEqual(page["content_type"], "mixed")
        self.assertTrue(page["has_native_text"])
        self.assertTrue(page["has_images"])
        self.assertEqual(page["ocr_review"]["reasons"],
                         ["images_may_contain_rasterized_text"])

    def test_reused_image_occurrences_are_not_duplicated_by_method(self):
        images = self.images(5)
        self.assertEqual(len(images), 2)
        self.assertEqual(len({image["xref"] for image in images}), 1)
        self.assertEqual(len({tuple(image["bbox"]) for image in images}), 2)
        self.assertEqual(len({image["id"] for image in images}), 2)

    def test_vector_properties_are_preserved(self):
        page = self.manifest["pages"][5]
        self.assertEqual(page["content_type"], "vector")
        drawing = page["drawings"][0]
        self.assertTrue(drawing["items"])
        self.assertEqual(drawing["color"], [0.1, 0.2, 0.8])
        self.assertEqual(drawing["fill"], [0.8, 0.7, 0.2])
        self.assertEqual(drawing["width"], 3.0)
        self.assertEqual(drawing["stroke_opacity"], 0.75)
        self.assertEqual(drawing["fill_opacity"], 0.5)

    def test_native_hebrew_is_extracted(self):
        if _hebrew_font() is None:
            self.skipTest("No installed font with Hebrew glyphs")
        text = "".join(block.get("text", "") for block in self.manifest["pages"][6]["blocks"])
        self.assertIn("שלום", text)
        self.assertTrue(self.manifest["pages"][6]["has_native_text"])

    def test_empty_page_is_not_mistaken_for_ocr_candidate(self):
        page = self.manifest["pages"][7]
        self.assertEqual(page["content_type"], "empty")
        self.assertFalse(page["has_native_text"])
        self.assertFalse(page["has_images"])
        self.assertFalse(page["requires_ocr_review"])

    def test_ids_and_manifest_are_stable_and_serializable(self):
        repeated = extract_pdf(self.path)
        self.assertEqual(self.manifest, repeated)
        ids = [item["id"] for page in repeated["pages"]
               for item in page["blocks"] + page["drawings"]]
        self.assertEqual(len(ids), len(set(ids)))
        destination = Path(self.temporary.name) / "output" / "manifest.json"
        write_manifest(repeated, destination)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), repeated)

    def test_image_discovery_uses_lightweight_matching_and_xref_cache(self):
        original_info = pymupdf.Page.get_image_info
        original_extract = pymupdf.Document.extract_image
        extracted_xrefs = []

        def lightweight_info(page, *args, **kwargs):
            self.assertFalse(kwargs.get("hashes", False))
            self.assertFalse(kwargs.get("xrefs", False))
            return original_info(page, *args, **kwargs)

        def tracked_extract(document, xref):
            extracted_xrefs.append(xref)
            return original_extract(document, xref)

        with (mock.patch.object(pymupdf.Page, "get_image_info", new=lightweight_info),
              mock.patch.object(pymupdf.Page, "get_image_rects",
                                side_effect=AssertionError("expensive image matching used")),
              mock.patch.object(pymupdf.Document, "extract_image", new=tracked_extract)):
            manifest = extract_pdf(self.path)

        self.assertEqual(len(extracted_xrefs), len(set(extracted_xrefs)))
        self.assertEqual(sum(page["has_images"] for page in manifest["pages"]), 5)


if __name__ == "__main__":
    unittest.main()
