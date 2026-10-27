"""Basic synthetic PDF extraction tests."""
import tempfile
import unittest
from pathlib import Path

import fitz

from stolas.extraction import extract_pdf, write_manifest


class ExtractionTests(unittest.TestCase):
    def test_manifest_and_stable_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.pdf"
            doc = fitz.open()
            page = doc.new_page(width=320, height=480)
            page.insert_text((35, 55), "Hello world")
            page.draw_rect(fitz.Rect(20, 80, 100, 120))
            doc.new_page()
            doc.save(path)
            doc.close()

            a = extract_pdf(path)
            b = extract_pdf(path)
            self.assertEqual(a, b)
            self.assertEqual(a["page_count"], 2)
            self.assertEqual(a["pages"][0]["number"], 1)
            self.assertTrue(any("Hello world" in block.get("text", "") for block in a["pages"][0]["blocks"]))
            self.assertTrue(a["pages"][0]["drawings"])
            self.assertTrue(a["pages"][1]["requires_ocr_review"])
            destination = Path(tmp) / "output" / "manifest.json"
            write_manifest(a, destination)
            self.assertTrue(destination.exists())


if __name__ == "__main__":
    unittest.main()
