#!/usr/bin/env python3
"""Test Gaussian orthophoto generation on vol_banyuls data."""
import sys
import os
import time
import json
import traceback

sys.path.insert(0, "/home/olivier/app1-colmap")

def main():
    from gaussian_ortho.generate_gaussian_orthophoto import generate_gaussian_orthophoto

    dense_path = "/mnt/j/workspace/vol_banyuls/dense"
    transform_file = "/mnt/j/workspace/vol_banyuls/alignment_transform.json"
    ortho_file = "/mnt/j/workspace/vol_banyuls/orthomosaic.gaussian_test9.tif"
    checkpoint_dir = "/mnt/j/workspace/vol_banyuls/gaussian_checkpoints_test9"
    utm_crs = "EPSG:32631"

    # Read CRS from file
    crs_file = "/mnt/j/workspace/vol_banyuls/geo_data.txt.crs"
    if os.path.exists(crs_file):
        with open(crs_file) as f:
            utm_crs = f.read().strip()

    start = time.time()

    def report_fn(vol_id, step, progress, msg):
        elapsed = time.time() - start
        print(f"[{elapsed:7.1f}s] [{step} {progress:3d}%] {msg}", flush=True)

    try:
        result = generate_gaussian_orthophoto(
            dense_path=dense_path,
            ortho_file=ortho_file,
            utm_crs=utm_crs,
            vol_id="banyuls_test",
            transform_file=transform_file,
            report_fn=report_fn,
            resolution=0.02,
            iterations=7000,
            partition_m=1,
            partition_n=1,
            sh_degree=3,
            fagk=True,
            lambda_depth=0.1,
            checkpoint_dir=checkpoint_dir,
            data_factor=2,
            strategy="mcmc",
            cap_max=2_000_000,
        )
        elapsed = time.time() - start
        print(f"\n=== DONE in {elapsed:.1f}s ===")
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        traceback.print_exc()
        print(f"\n=== FAILED: {e} ===")
        sys.exit(1)

if __name__ == "__main__":
    main()
