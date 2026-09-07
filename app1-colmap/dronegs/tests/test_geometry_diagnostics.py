#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import importlib.util
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import numpy as np

TOOL = Path(__file__).resolve().parents[1]/'tools/geometry_diagnostics.py'
spec = importlib.util.spec_from_file_location('geometry_diagnostics', TOOL)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
# CTest supplies a native probe only for direct execution; pytest collection
# must not consume its own arguments or mistake them for a binary path.
BINARY = sys.argv.pop(1) if __name__ == '__main__' and len(sys.argv) > 1 else None


def view(i=1, tx=0):
    return dict(id=i, width=41, height=31, fx=40., fy=40., cx=20.5, cy=15.5,
                native_fx=40., native_fy=40., rotation=np.eye(3).tolist(), translation=[tx, 0., 0.])


def exact_scene(v, surface='plane'):
    y, x = np.indices((v['height'], v['width']))
    direction = g.rays(v, x, y) @ np.asarray(v['rotation'])
    center = -np.asarray(v['translation']) @ np.asarray(v['rotation'])
    if surface == 'plane':
        n = np.array([.3, 0., 1.]); n /= np.linalg.norm(n)
        distance = (4/np.sqrt(1.09)-center@n)/(direction@n)
        normals = np.broadcast_to(n, (*x.shape, 3))
        valid = distance > 0
    else:
        # Vertical cylinder radius 2, center (0,0,6); exact quadratic intersections.
        ox, oz = center[0], center[2]-6
        a = direction[..., 0]**2+direction[..., 2]**2
        b = 2*(ox*direction[..., 0]+oz*direction[..., 2])
        discriminant = b*b-4*a*(ox*ox+oz*oz-4)
        valid = discriminant >= 0
        distance = (-b-np.sqrt(np.maximum(discriminant, 0)))/(2*a)
        point = center+distance[..., None]*direction
        normals = np.stack((point[..., 0], np.zeros_like(distance), point[..., 2]-6), axis=-1)
        normals /= np.linalg.norm(normals, axis=-1)[..., None]
        valid &= distance > 0
    frame = np.zeros((*x.shape, 12), dtype=np.float64)
    frame[..., 0] = valid
    frame[..., 1] = np.where(valid, distance, 0)
    frame[..., 9] = frame[..., 1]
    frame[..., 5:8] = (normals @ np.asarray(v['rotation']).T)*valid[..., None]
    frame[..., 8] = valid
    frame[..., 11] = valid
    return frame, dict(depth_z=frame[..., 9], normal_world=normals, valid=valid)


class GeometryTests(unittest.TestCase):
    def test_colmap_crop_calibration_and_validation(self):
        row = [7, 4000, 3000, 3000, 3100, 2000, 1500, *np.eye(3).reshape(-1), 1, 2, 3]
        v = g.colmap_view(row, 2, [1000, 500, 2000, 2000])
        self.assertEqual((v['width'], v['height'], v['fx'], v['cx'], v['cy']), (1000, 1000, 1500, 500, 500))
        self.assertEqual(v['native_fx'], 3000)
        with self.assertRaises(ValueError): g.colmap_view(row, 0)
        with self.assertRaises(ValueError): g.colmap_view(row, 1, [3999, 0, 2, 2])
        v['rotation'][0][0] = 2
        with self.assertRaises(ValueError): g.validate_view(v)

    def test_plane_reference_detects_depth_and_normal_error(self):
        v = view(); a, reference = exact_scene(v)
        correct = g.compare_reference(v, a, reference, .5)
        self.assertEqual(correct['reference_coverage_fraction'], 1)
        self.assertLess(correct['absolute_z_error']['mean'], 1e-12)
        self.assertLess(correct['unoriented_normal_degrees']['mean'], 1e-5)
        wrong = a.copy(); wrong[..., 9] += .2; wrong[..., 5:8] = [0, 1, 0]
        bad = g.compare_reference(v, wrong, reference, .5)
        self.assertGreater(bad['point_to_reference_tangent_distance']['mean'], .15)
        self.assertAlmostEqual(bad['unoriented_normal_degrees']['mean'], 90)
        wrong[:10, :, 11] = 0
        self.assertLess(g.compare_reference(v, wrong, reference, .5)['reference_coverage_fraction'], .7)

    def test_multiview_plane_and_curved_surface(self):
        v1, v2 = view(), view(2, -.3)
        for surface in ('plane', 'cylinder'):
            with self.subTest(surface=surface):
                a, _ = exact_scene(v1, surface); b, _ = exact_scene(v2, surface)
                result = g.compare_pair(v1, v2, a, b)
                self.assertGreater(result['target_supported'], 400)
                self.assertLess(result['roundtrip_pixels']['p95'], .9)
                identity = g.compare_pair(v1, v1, a, a)
                self.assertLess(identity['absolute_relative_z_error']['mean'], 1e-12)
                self.assertLess(identity['unoriented_normal_degrees']['p95'], 1e-5)
                if surface == 'cylinder':
                    # Exact geometry still has sampling error near the silhouette.
                    # Increasing image resolution at fixed FOV must reduce it.
                    import copy
                    hi1, hi2 = copy.deepcopy(v1), copy.deepcopy(v2)
                    for v in (hi1, hi2):
                        for key in ('width', 'height', 'fx', 'fy', 'cx', 'cy', 'native_fx', 'native_fy'):
                            v[key] *= 4
                    ha, _ = exact_scene(hi1, surface); hb, _ = exact_scene(hi2, surface)
                    high = g.compare_pair(hi1, hi2, ha, hb)
                    self.assertLess(high['absolute_relative_z_error']['mean'], result['absolute_relative_z_error']['mean']/2)
                    self.assertLess(high['unoriented_normal_degrees']['mean'], result['unoriented_normal_degrees']['mean']/2)

    def test_occlusion_does_not_hide_errors_or_missing_support(self):
        v1, v2 = view(), view(2)
        a, _ = exact_scene(v1); b = a.copy(); b[..., 9] *= .5
        bad = g.compare_pair(v1, v2, a, b)
        self.assertEqual(bad['possible_occlusion_count'], bad['target_supported'])
        self.assertAlmostEqual(bad['absolute_relative_z_error']['mean'], .5)
        b[:15, :, 11] = 0
        holes = g.compare_pair(v1, v2, a, b)
        self.assertGreater(holes['target_unsupported'], 0)
        self.assertLess(holes['evaluated_fraction'], .6)
        self.assertEqual(holes['sampled_pixels'], bad['sampled_pixels'])

    def test_footprint_units_and_resolution(self):
        v = view(); ray = np.array([[0., 0., 1.]])
        n = np.array([[0., 0., 1.]])
        footprint, valid = g.surface_footprint(v, ray, np.array([4.]), n)
        self.assertTrue(valid[0]); self.assertAlmostEqual(footprint[0], .1)
        scaled, _ = g.surface_footprint(v, ray, np.array([40.]), n)
        self.assertAlmostEqual(scaled[0]/footprint[0], 10)
        v['fx'] /= 2; v['fy'] /= 2
        downsampled, _ = g.surface_footprint(v, ray, np.array([4.]), n)
        self.assertAlmostEqual(downsampled[0], footprint[0])

    def test_empty_support_is_null_not_perfect(self):
        a = np.zeros((31, 41, 12))
        result = g.compare_pair(view(), view(2), a, a)
        self.assertEqual(result['evaluated_fraction'], 0)
        self.assertIsNone(result['roundtrip_pixels']['mean'])
        self.assertIsNone(g.view_summary(view(), a, .5)['metrics']['plane_z']['mean'])

    def test_array_corruption_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'1.geometry.f32'
            p.write_bytes(b'bad')
            with self.assertRaises(ValueError): g.load_frame(tmp, view())
            a = np.zeros((31, 41, 12), dtype='<f4'); a[0, 0, 1] = np.nan; a.tofile(p)
            with self.assertRaises(ValueError): g.load_frame(tmp, view())

    @unittest.skipUnless(BINARY, 'native probe path not supplied')
    def test_native_colmap_normals_and_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); sparse = root/'sparse'; sparse.mkdir()
            # Genuine binary COLMAP boundary, with known world-to-camera pose.
            (sparse/'cameras.bin').write_bytes(struct.pack('<QIiQQdddd', 1, 1, 1, 41, 31, 40, 40, 20.5, 15.5))
            (sparse/'images.bin').write_bytes(struct.pack('<QI7dI', 1, 7, 1, 0, 0, 0, 0, 0, 0, 1)+b'image.jpg\0'+struct.pack('<Q', 0))
            (sparse/'points3D.bin').write_bytes(struct.pack('<Q', 0))
            export = root/'cameras.txt'
            subprocess.run([BINARY, '--export-colmap', str(root), str(export)], check=True, capture_output=True)
            row = list(map(float, export.read_text().split()))
            v = g.colmap_view(row, 1); self.assertEqual(v['id'], 7)
            np.testing.assert_allclose(v['rotation'], np.eye(3))
            properties = ['x', 'y', 'z', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity',
                          'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3']
            header = 'ply\nformat binary_little_endian 1.0\nelement vertex 2\n'+''.join('property float '+p+'\n' for p in properties)+'end_header\n'
            flat = [0, 0, 4, 0, 0, 0, 4, math.log(.7), math.log(.7), math.log(.01), 1, 0, 0, 0]
            sphere = [0, 0, 6, 0, 0, 0, 1, math.log(.7), math.log(.7), math.log(.7), 1, 0, 0, 0]
            ply = root/'model.ply'; ply.write_bytes(header.encode()+struct.pack('<28f', *(flat+sphere)))
            manifest = root/'views.json'; g.write_json(manifest, dict(schema_version=1, views=[v], pairs=[]))
            cmd = [sys.executable, str(TOOL), 'run', '--binary', BINARY, '--ply', str(ply),
                   '--views-json', str(manifest), '--output', str(root/'run')]
            subprocess.run(cmd, check=True, capture_output=True)
            report = json.loads((root/'run/report.json').read_text())
            self.assertEqual(report['native']['gaussians'], 2)
            normals = np.fromfile(root/'run/native/gaussian_normals.f32', dtype='<f4').reshape(-1, 6)
            np.testing.assert_allclose(normals[0, :3], [0, 0, 1], atol=1e-6)
            self.assertGreater(normals[0, 3], .99)
            np.testing.assert_array_equal(normals[1, :4], [0, 0, 0, 0])
            self.assertEqual(g.digest(ply), report['provenance']['model_sha256'])
            retry = subprocess.run(cmd, capture_output=True)
            self.assertNotEqual(retry.returncode, 0)


if __name__ == '__main__':
    unittest.main()
