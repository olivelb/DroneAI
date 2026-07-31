import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch


APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

import pipeline_support


class TestPipelineSupport(unittest.TestCase):
    def test_discover_input_assets_supports_nested_vendor_dataset(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = os.path.join(tmp_dir, "images")
            rtk_dir = os.path.join(tmp_dir, "RTK_Data")
            os.makedirs(images_dir)
            os.makedirs(rtk_dir)
            image_path = os.path.join(images_dir, "MAX_0002.JPG")
            sidecar_path = os.path.join(rtk_dir, "mission_Timestamp.MRK")
            open(image_path, "wb").close()
            open(sidecar_path, "wb").close()

            images, sidecars = pipeline_support.discover_input_assets(tmp_dir)

            self.assertEqual([str(path) for path in images], [image_path])
            self.assertEqual([str(path) for path in sidecars], [sidecar_path])

    def test_discover_input_assets_rejects_duplicate_flattened_names(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            for directory in ("first", "second"):
                nested = os.path.join(tmp_dir, directory)
                os.makedirs(nested)
                open(os.path.join(nested, "image.jpg"), "wb").close()

            with self.assertRaisesRegex(ValueError, "duplicate name"):
                pipeline_support.discover_input_assets(tmp_dir)

    def test_projected_crs_policy_round_trip_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            geo_data_file = os.path.join(tmp_dir, "geo_data.txt")
            pipeline_support.save_projected_crs(
                geo_data_file,
                "EPSG:3944",
                policy="auto-local",
            )

            self.assertEqual(
                pipeline_support.read_saved_projected_crs(geo_data_file),
                "EPSG:3944",
            )
            self.assertEqual(
                pipeline_support.read_saved_projected_crs_policy(geo_data_file),
                {
                    "schema_version": 2,
                    "projected_crs": "EPSG:3944",
                    "policy": "auto-local",
                    "requested_crs": "",
                },
            )

    def test_extract_gps_data_uses_pillow_exif_metadata(self):
        fake_pyproj = types.ModuleType("pyproj")
        fake_transformer = MagicMock()
        fake_transformer.transform.return_value = (123.4, 567.8)
        fake_pyproj.Transformer = MagicMock()
        fake_pyproj.Transformer.from_crs.return_value = fake_transformer

        fake_image = MagicMock()
        fake_image.__enter__.return_value = fake_image
        fake_image._getexif.return_value = {
            0x8825: {
                1: "N",
                2: (45, 30, 0),
                3: "E",
                4: (5, 45, 0),
                6: 42.0,
            }
        }
        report_fn = MagicMock()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_file = os.path.join(tmp_dir, "geo_data.txt")
            with patch.dict(sys.modules, {"pyproj": fake_pyproj}):
                with patch("os.listdir", return_value=["img1.jpg"]):
                    with patch.object(pipeline_support.PILImage, "open", return_value=fake_image):
                        projected_crs = pipeline_support.extract_gps_data(
                            "images",
                            output_file,
                            "vol-1",
                            report_fn,
                        )

            with open(output_file, encoding="utf-8") as handle:
                output = handle.read()
            metadata = pipeline_support.read_saved_projected_crs_policy(
                output_file
            )

        self.assertEqual(projected_crs, "EPSG:3946")
        self.assertEqual(output, "img1.jpg 123.4 567.8 42.0\n")
        self.assertEqual(metadata["vertical"]["reference"], "unknown")
        self.assertFalse(
            metadata["vertical"]["orthometric_conversion_applied"]
        )
        report_fn.assert_any_call(
            "vol-1",
            "GPS_EXTRACTION",
            12,
            log=(
                "Extracted positions from 1/1 images "
                "(0 DJI MRK, 0 XMP RTK, 1 EXIF). "
                "Using CRS EPSG:3946 (france-cc9)"
            ),
        )

    def test_extract_gps_data_skips_images_without_gps(self):
        fake_pyproj = types.ModuleType("pyproj")
        fake_pyproj.Transformer = MagicMock()
        fake_image = MagicMock()
        fake_image.__enter__.return_value = fake_image
        fake_image._getexif.return_value = {}

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_file = os.path.join(tmp_dir, "geo_data.txt")
            with patch.dict(sys.modules, {"pyproj": fake_pyproj}):
                with patch("os.listdir", return_value=["img1.jpg"]):
                    with patch.object(pipeline_support.PILImage, "open", return_value=fake_image):
                        projected_crs = pipeline_support.extract_gps_data(
                            "images",
                            output_file,
                            "vol-1",
                            MagicMock(),
                        )

            self.assertIsNone(projected_crs)
            self.assertEqual(os.path.getsize(output_file), 0)

    def test_detect_existing_pipeline_distinguishes_descriptor_types(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "database.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE descriptors(rows INTEGER, cols INTEGER, data BLOB)")
            conn.execute("INSERT INTO descriptors(rows, cols, data) VALUES(?, ?, ?)", (2, 128, b"a" * 256))
            conn.commit()
            conn.close()

            self.assertEqual(pipeline_support.detect_existing_pipeline(db_path), "SIFT")

            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM descriptors")
            conn.execute("INSERT INTO descriptors(rows, cols, data) VALUES(?, ?, ?)", (2, 128, b"a" * 1024))
            conn.commit()
            conn.close()

            self.assertEqual(pipeline_support.detect_existing_pipeline(db_path), "ALIKED")


if __name__ == "__main__":
    unittest.main()
