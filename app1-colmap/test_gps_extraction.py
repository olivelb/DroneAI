import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, mock_open, patch


APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

if "exif" not in sys.modules:
    exif_module = types.ModuleType("exif")
    exif_module.Image = MagicMock()
    sys.modules["exif"] = exif_module

import pipeline_support


class TestPipelineSupport(unittest.TestCase):
    def test_extract_gps_data_uses_exif_metadata(self):
        fake_pyproj = types.ModuleType("pyproj")
        fake_transformer = MagicMock()
        fake_transformer.transform.return_value = (123.4, 567.8)
        fake_pyproj.Transformer = MagicMock()
        fake_pyproj.Transformer.from_crs.return_value = fake_transformer

        report_fn = MagicMock()
        exif_image = MagicMock()
        exif_image.gps_latitude = (45, 30, 0)
        exif_image.gps_latitude_ref = "N"
        exif_image.gps_longitude = (5, 45, 0)
        exif_image.gps_longitude_ref = "E"
        exif_image.gps_altitude = 42.0

        with patch.dict(sys.modules, {"pyproj": fake_pyproj}):
            with patch("os.listdir", return_value=["img1.jpg"]):
                with patch("builtins.open", mock_open()) as open_mock:
                    with patch.object(pipeline_support, "ExifImage", return_value=exif_image):
                        utm_crs = pipeline_support.extract_gps_data(
                            "images",
                            "geo_data.txt",
                            "vol-1",
                            report_fn,
                        )

        self.assertEqual(utm_crs, "EPSG:32631")
        handle = open_mock()
        handle.write.assert_any_call("img1.jpg 123.4 567.8 42.0\n")
        report_fn.assert_any_call(
            "vol-1",
            "GPS_EXTRACTION",
            12,
            log="Extracted GPS from 1/1 images using EXIF. Using CRS EPSG:32631",
        )

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
