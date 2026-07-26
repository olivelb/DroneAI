#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


TOOL = Path(__file__).parents[1] / "tools" / "lpips_eval.py"
SPEC = importlib.util.spec_from_file_location("dronegs_lpips_eval", TOOL)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LpipsToolTests(unittest.TestCase):
    def test_pair_discovery_results_and_manifest_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evaluation = root / "evaluation"
            predictions = evaluation / "predictions"
            targets = evaluation / "targets"
            predictions.mkdir(parents=True)
            targets.mkdir()
            for index in (2, 0):
                payload = f"P6\n1 1\n255\n{index:03d}".encode()
                (predictions / f"{index:06d}.ppm").write_bytes(payload)
                (targets / f"{index:06d}.ppm").write_bytes(payload)
            (evaluation / "metrics.csv").write_text(
                "stage,held_out_index,scene_index,image_name,"
                "psnr,ssim,active_pixel_fraction\n"
                'final,0,0,"a.jpg",1,0.1,1\n'
                'final,2,8,"b.jpg",2,0.2,1\n',
                encoding="utf-8",
            )
            manifest = root / "trainer_run.json"
            manifest.write_text(
                json.dumps({"metrics": {"lpips": None}, "artifacts": {}}),
                encoding="utf-8",
            )
            manifest.chmod(0o640)

            pairs = MODULE.discover_pairs(evaluation)
            self.assertEqual([pair.index for pair in pairs], [0, 2])
            summary = MODULE.write_results(
                evaluation, pairs, [0.1, 0.3], "alex", "0.1", "cpu"
            )
            MODULE.update_manifest(manifest, evaluation, summary)

            self.assertAlmostEqual(summary["mean"], 0.2)
            self.assertAlmostEqual(summary["median"], 0.2)
            self.assertAlmostEqual(summary["p95"], 0.29)
            document = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertAlmostEqual(document["metrics"]["lpips"], 0.2)
            self.assertEqual(document["metrics"]["lpips_network"], "alex")
            self.assertIn("evaluation/lpips.csv", document["artifacts"])
            self.assertIn("evaluation/lpips.json", document["artifacts"])
            self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o640)
            self.assertEqual(
                stat.S_IMODE((evaluation / "lpips.csv").stat().st_mode), 0o644
            )
            if hasattr(os, "getuid"):
                self.assertEqual(manifest.stat().st_uid, os.getuid())
                self.assertEqual((evaluation / "lpips.csv").stat().st_uid, os.getuid())

    def test_mismatched_pairs_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "predictions").mkdir()
            (root / "targets").mkdir()
            (root / "predictions" / "000000.ppm").write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "indices differ"):
                MODULE.discover_pairs(root)


if __name__ == "__main__":
    unittest.main()
