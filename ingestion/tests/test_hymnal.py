import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hymnal.cli import HymnalError, convert


class HymnalConversionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.source = self.raw / "001.txt"
        self.source.write_bytes(
            b"\xef\xbb\xbf001 - Synthetic title\r\n\r\n"
            b"Original example stanza.\r\n\r\nAnother example stanza.\r\n"
        )
        self.metadata = {
            "revision": 1,
            "source_uri": "urn:synthetic:hymnal-tests",
            "approval_reference": "urn:synthetic:review",
            "complete_catalog": False,
            "languages": ["en"],
            "hymns": [{
                "file": "001.txt",
                "hymn_id": "synthetic-001",
                "number": 1,
                "language": "en",
                "category": "Example",
                "copyright_notice": "Original synthetic test text.",
                "rights": {
                    "status": "approved",
                    "basis": "original",
                    "reference": "Authored for this test.",
                },
            }],
        }
        self.metadata_path = self.root / "metadata.json"

    def run_conversion(self) -> tuple[dict, dict]:
        self.metadata_path.write_text(json.dumps(self.metadata), encoding="utf-8")
        return convert(self.raw, self.metadata_path)

    def test_preserves_stanzas_and_emits_backend_manifest(self) -> None:
        manifest, provenance = self.run_conversion()
        hymn = manifest["hymns"][0]
        self.assertEqual(hymn["title"], "Synthetic title")
        self.assertEqual(
            hymn["lyrics"], "Original example stanza.\n\nAnother example stanza."
        )
        self.assertEqual(set(manifest), {
            "revision", "source_uri", "approval_reference", "complete_catalog",
            "languages", "hymns",
        })
        self.assertNotIn("file", hymn)
        self.assertNotIn("rights", hymn)
        self.assertIsNone(hymn["mp3_url"])
        self.assertIsNone(hymn["flac_url"])
        self.assertEqual(provenance["files"][0]["sha256"],
                         hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(provenance["missing_numbers"]["en"][:2], [2, 3])
        self.assertEqual(provenance["files"][0]["rights"]["basis"], "original")

    def test_unicode_heading_and_text_are_preserved(self) -> None:
        self.source.write_text(
            "001 \u2013 Synthetic title\n\nOriginal caf\u00e9 example.\n", encoding="utf-8"
        )
        manifest, _ = self.run_conversion()
        self.assertEqual(manifest["hymns"][0]["lyrics"], "Original caf\u00e9 example.")

    def test_pending_or_missing_rights_are_rejected(self) -> None:
        for change in [
            {"status": "pending"}, {"reference": ""}, {"basis": "unknown"}, {"basis": []},
        ]:
            with self.subTest(change=change):
                rights = self.metadata["hymns"][0]["rights"]
                original = rights.copy()
                rights.update(change)
                with self.assertRaises(HymnalError):
                    self.run_conversion()
                rights.clear()
                rights.update(original)
        del self.metadata["hymns"][0]["rights"]
        with self.assertRaises(HymnalError):
            self.run_conversion()

    def test_heading_number_must_match_metadata(self) -> None:
        self.source.write_text("002 - Wrong number\n\nOriginal example.", encoding="utf-8")
        with self.assertRaisesRegex(HymnalError, "heading"):
            self.run_conversion()

    def test_missing_and_invalid_utf8_files_fail(self) -> None:
        self.source.unlink()
        with self.assertRaisesRegex(HymnalError, "file"):
            self.run_conversion()
        self.source.write_bytes(b"\xff")
        with self.assertRaisesRegex(HymnalError, "UTF-8"):
            self.run_conversion()

    def test_paths_cannot_escape_the_raw_directory(self) -> None:
        self.metadata["hymns"][0]["file"] = "../metadata.json"
        with self.assertRaisesRegex(HymnalError, "relative"):
            self.run_conversion()
        outside = self.root / "outside.txt"
        outside.write_text("001 - Outside\n\nOriginal example.", encoding="utf-8")
        self.source.unlink()
        self.source.symlink_to(outside)
        self.metadata["hymns"][0]["file"] = "001.txt"
        with self.assertRaisesRegex(HymnalError, "outside"):
            self.run_conversion()

    def test_duplicate_and_inconsistent_identities_fail(self) -> None:
        second = self.metadata["hymns"][0].copy()
        self.metadata["hymns"].append(second)
        with self.assertRaisesRegex(HymnalError, "identity"):
            self.run_conversion()
        second["number"] = 2
        with self.assertRaisesRegex(HymnalError, "identity"):
            self.run_conversion()

    def test_backend_field_constraints_are_enforced(self) -> None:
        for field, invalid in [
            ("number", True), ("number", 697), ("language", "EN"),
            ("category", ""), ("copyright_notice", "\0"),
            ("tempo", 401), ("mp3_url", "http://example.test/audio.mp3"),
            ("mp3_url", "https://example.test/audio.mp3?token=secret"),
            ("mp3_url", "https://example.test/audio.mp3?"),
            ("mp3_url", "https://example.test/audio.mp3#"),
            ("mp3_url", "https://invalid host/audio.mp3"),
            ("flac_url", "https://example.test/audio.flac"), ("tags", [""] * 31),
            ("unexpected", "value"),
        ]:
            with self.subTest(field=field, invalid=invalid):
                hymn = self.metadata["hymns"][0]
                original = hymn.copy()
                hymn[field] = invalid
                with self.assertRaises(HymnalError):
                    self.run_conversion()
                hymn.clear()
                hymn.update(original)

    def test_complete_catalog_requires_every_number_in_every_language(self) -> None:
        self.metadata["complete_catalog"] = True
        with self.assertRaisesRegex(HymnalError, "1-696"):
            self.run_conversion()
        self.metadata["complete_catalog"] = False
        self.metadata["languages"].append("es")
        with self.assertRaisesRegex(HymnalError, "language"):
            self.run_conversion()

    def test_translations_keep_stable_ids_and_independent_text(self) -> None:
        translated = self.raw / "es" / "001.txt"
        translated.parent.mkdir()
        translated.write_text("001 - Otro ejemplo\n\nTexto original de prueba.", encoding="utf-8")
        self.metadata["languages"].append("es")
        first = self.metadata["hymns"][0]
        first["mp3_url"] = "https://example.test/original.mp3"
        first["flac_url"] = "https://example.test/original.flac"
        self.metadata["hymns"].append({
            **first, "file": "es/001.txt", "language": "es",
            "mp3_url": None, "flac_url": None,
        })
        manifest, _ = self.run_conversion()
        english, spanish = manifest["hymns"]
        self.assertEqual(english["hymn_id"], spanish["hymn_id"])
        self.assertEqual(english["number"], spanish["number"])
        self.assertNotEqual(english["lyrics"], spanish["lyrics"])
        self.assertEqual(spanish["language"], "es")
        self.assertEqual(english["flac_url"], first["flac_url"])
        self.assertIsNone(spanish["mp3_url"])

    def test_text_limits_use_utf8_bytes_and_duplicate_json_is_rejected(self) -> None:
        self.metadata["hymns"][0]["category"] = "\u00e9" * 60
        self.run_conversion()
        self.metadata["hymns"][0]["category"] = "\u00e9" * 61
        with self.assertRaisesRegex(HymnalError, "120 UTF-8 bytes"):
            self.run_conversion()
        self.metadata_path.write_text('{"revision":1,"revision":2}', encoding="utf-8")
        with self.assertRaisesRegex(HymnalError, "Duplicate JSON"):
            convert(self.raw, self.metadata_path)

    def test_complete_synthetic_catalog_is_accepted_and_sorted(self) -> None:
        first = self.metadata["hymns"][0]
        self.metadata["hymns"] = []
        for number in range(696, 0, -1):
            filename = f"{number:03}.txt"
            (self.raw / filename).write_text(
                f"{number} - Synthetic {number}\n\nOriginal test text.", encoding="utf-8"
            )
            self.metadata["hymns"].append({
                **first, "file": filename, "number": number,
                "hymn_id": f"synthetic-{number:03}",
            })
        self.metadata["complete_catalog"] = True
        manifest, provenance = self.run_conversion()
        self.assertEqual(len(manifest["hymns"]), 696)
        self.assertEqual(manifest["hymns"][0]["number"], 1)
        self.assertEqual(provenance["missing_numbers"], {"en": []})

    def test_cli_unapproved_rights_never_publish_output(self) -> None:
        self.metadata["hymns"][0]["rights"]["status"] = "pending"
        self.metadata_path.write_text(json.dumps(self.metadata), encoding="utf-8")
        output = self.root / "manifest.json"
        command = [
            sys.executable, "-m", "hymnal.cli", "--raw-dir", str(self.raw),
            "--metadata", str(self.metadata_path), "--output", str(output),
        ]
        for existing_output in [None, b"Previously approved manifest"]:
            with self.subTest(existing_output=existing_output):
                if existing_output is not None:
                    output.write_bytes(existing_output)
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("approved rights", result.stderr)
                self.assertEqual(result.stdout, "")
                if existing_output is None:
                    self.assertFalse(output.exists())
                else:
                    self.assertEqual(output.read_bytes(), existing_output)

    def test_cli_reports_checksum_and_preserves_output_on_failure(self) -> None:
        self.run_conversion()
        output = self.root / "manifest.json"
        command = [
            sys.executable, "-m", "hymnal.cli", "--raw-dir", str(self.raw),
            "--metadata", str(self.metadata_path), "--output", str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        original = output.read_bytes()
        self.assertEqual(report["manifest_sha256"], hashlib.sha256(original).hexdigest())
        self.source.write_text("Invalid heading", encoding="utf-8")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("heading", result.stderr)
        self.assertEqual(output.read_bytes(), original)

    def test_output_cannot_overwrite_inputs(self) -> None:
        self.run_conversion()
        result = subprocess.run([
            sys.executable, "-m", "hymnal.cli", "--raw-dir", str(self.raw),
            "--metadata", str(self.metadata_path), "--output", str(self.source),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("overwrite", result.stderr)
        self.assertTrue(self.source.read_bytes().startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
