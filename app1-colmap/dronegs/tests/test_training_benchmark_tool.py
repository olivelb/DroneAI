#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import importlib.util
from pathlib import Path
import unittest

tool = Path(__file__).parents[1] / "tools/benchmark_training_steps.py"
spec = importlib.util.spec_from_file_location("training_benchmark_tool", tool)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BenchmarkToolTests(unittest.TestCase):
    def test_rewrite_preserves_training_identity_and_prevents_checkpoint_writes(self):
        original = ["dronegs", "--data-path", "/old/data", "--iter", "30000",
                    "--seed", "42", "--dataset-fingerprint", "original-fingerprint",
                    "--checkpoint-path", "/precious/model.ckpt", "--checkpoint-every", "1000",
                    "--stop-after", "10000", "--eval-every", "1000", "--resume-from", "/old.ckpt"]
        command = module.make_command(original, Path("/binary"), Path("/data"), Path("/input.ckpt"),
                                      Path("/output"), 3, 5, 1)
        options = dict(zip(command[1::2], command[2::2]))
        self.assertEqual(command[0], "/binary")
        self.assertEqual(options["--iter"], "30000")
        self.assertEqual(options["--dataset-fingerprint"], "original-fingerprint")
        self.assertEqual(options["--resume-from"], "/input.ckpt")
        self.assertEqual(options["--run-manifest"], "/output/trainer_run.json")
        for key in ["--checkpoint-path", "--checkpoint-every", "--stop-after", "--eval-every"]:
            self.assertNotIn(key, options)
        with self.assertRaises(ValueError):
            module.make_command(["dronegs", "--seed", "1", "--seed", "2"], Path("/b"), Path("/d"),
                                Path("/c"), Path("/o"), 1, 1, 0)

    @staticmethod
    def rows():
        identity = {k: "1" for k in ["frame_index", "scene_index", "tile_index", "width", "height",
                    "checkpoint_iteration", "step", "gaussians", "active_sh_degree", "mse_blend",
                    "collect_refinement_statistics"]}
        identity["image_name"] = "image.jpg"
        return [dict(identity, warmup=str(int(i == 0)), repeat=str(i), view="0", total_ms=str(v),
                     wall_ms=str(v + 1), loss="0.02") for i, v in enumerate([999, 10, 12, 11])]

    def test_summary_excludes_warmup_and_reports_dispersion(self):
        result = module.summarize(self.rows(), 1, 3)["0"]["metrics"]["total_ms"]
        self.assertEqual(result, {"median": 11, "min": 10, "max": 12, "mad": 1})

    def test_summary_rejects_missing_duplicate_nonfinite_or_changed_samples(self):
        mutations = [lambda r: r.pop(), lambda r: r[-1].update(repeat="1"),
                     lambda r: r[-1].update(total_ms="nan"),
                     lambda r: r[-1].update(gaussians="200")]
        for mutate in mutations:
            rows = self.rows()
            mutate(rows)
            with self.assertRaises(ValueError):
                module.summarize(rows, 1, 3)


if __name__ == "__main__":
    unittest.main()
