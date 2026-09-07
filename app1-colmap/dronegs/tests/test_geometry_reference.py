#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU qualification of camera-only estimated-depth reference preparation."""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import warnings

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import geometry_diagnostics as g
import geometry_reference as ref


def rotation(angle=.4):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def view():
    return dict(id=1, width=9, height=7, fx=200., fy=200., cx=4.5, cy=3.5,
                native_fx=400., native_fy=400., rotation=rotation().tolist(),
                translation=[.2, -.3, .4])


class ReferenceTests(unittest.TestCase):
    def test_similarity_recovers_known_units_and_rotation(self):
        x = np.random.default_rng(7).normal(size=(25, 3))
        for planar in (False, True):
            if planar: x[:, 2] = 0
            expected_r, expected_t = rotation(), np.array([4., -2, .7])
            s, r, t = ref.fit_similarity(x, 3.2*x@expected_r.T+expected_t)
            self.assertAlmostEqual(s, 3.2)
            np.testing.assert_allclose(r, expected_r, atol=1e-14)
            np.testing.assert_allclose(t, expected_t, atol=1e-14)

    def test_similarity_rejects_ambiguous_and_invalid_inputs(self):
        good = np.random.default_rng(2).normal(size=(8, 3))
        invalid = [np.zeros((8, 3)), np.arange(8)[:, None]*np.ones((1, 3)),
                   np.full((8, 3), np.nan), good[:3], good[:, :2]]
        for x in invalid:
            with self.subTest(shape=x.shape), self.assertRaises(ValueError):
                ref.fit_similarity(x, x)
        with self.assertRaises(ValueError): ref.fit_similarity(good, good[:5])

    def test_camera_conversion_preserves_rays_and_depth_scale(self):
        s, r, t = 3.2, rotation(), np.array([4., -2, .7])
        pose = np.eye(4); pose[:3, :3] = rotation(-.2); pose[:3, 3] = [1, 2, 3]
        cr, ct = ref.camera_in_source(pose, s, r, t)
        camera_point = np.array([.8, -.2, 4.])
        reference_world = pose[:3, :3]@camera_point+pose[:3, 3]
        source_world = r.T@(reference_world-t)/s
        np.testing.assert_allclose(cr@source_world+ct, camera_point/s, atol=1e-14)
        np.testing.assert_allclose(cr@cr.T, np.eye(3), atol=1e-14)
        self.assertAlmostEqual(np.linalg.det(cr), 1)
        for scale in (0., -1., np.nan):
            with self.assertRaises(ValueError): ref.camera_in_source(pose, scale, r, t)
        for bad_r in (np.eye(3)*2, np.diag([-1., 1, 1]), np.zeros((2, 2))):
            with self.assertRaises(ValueError): ref.camera_in_source(pose, s, bad_r, t)
        bad_pose = pose.copy(); bad_pose[:3, :3] *= 2
        with self.assertRaises(ValueError): ref.camera_in_source(bad_pose, s, r, t)
        bad_pose = pose.copy(); bad_pose[3, 0] = 1
        with self.assertRaises(ValueError): ref.camera_in_source(bad_pose, s, r, t)
        with self.assertRaises(ValueError): ref.camera_in_source(pose, s, r, [0, np.nan, 0])

    def test_plane_normals_world_frame_and_missing_depths(self):
        v = view(); y, x = np.indices((v['height'], v['width']))
        n = np.array([.2, -.1, 1.]); n /= np.linalg.norm(n)
        depth = 4/(g.rays(v, x, y)@n)
        normals, valid = ref.normals_from_depth(v, depth)
        self.assertEqual(valid.sum(), 35)
        expected = np.broadcast_to(n@rotation(), normals[valid].shape)
        np.testing.assert_allclose(normals[valid], expected, atol=1e-13)
        for missing in (0, -1, np.nan, np.inf):
            d = depth.copy(); d[3, 4] = missing
            with warnings.catch_warnings():
                warnings.simplefilter('error')
                normals, valid = ref.normals_from_depth(v, d)
            self.assertEqual(valid.sum(), 30)
            self.assertTrue(np.isfinite(normals).all())
            self.assertFalse(valid[3, 4])
        d = depth.copy(); d[3, 4] *= 2
        self.assertEqual(ref.normals_from_depth(v, d)[1].sum(), 30)
        with self.assertRaises(ValueError): ref.normals_from_depth(v, depth[:2])
        with self.assertRaises(ValueError): ref.normals_from_depth(v, depth, np.nan)

    def test_normal_mask_does_not_remove_depth_errors(self):
        v = view(); shape = (7, 9)
        normal = np.zeros((*shape, 3)); normal[..., 2] = 1
        valid = np.ones(shape, bool); nv = np.zeros(shape, bool); nv[2:5, 2:7] = True
        normal[~nv] = np.nan  # Unsupported normals do not invalidate depths.
        reference = dict(depth_z=np.full(shape, 4.), normal_world=normal,
                         valid=valid, normal_valid=nv)
        a = np.zeros((*shape, 12)); a[..., 0] = 1; a[..., 11] = 1
        a[..., 1] = 4.25; a[..., 9] = 4.5; a[..., 8] = 1
        a[..., 5:8] = np.array([0, 0, 1])@rotation().T
        plane = g.compare_reference(v, a, reference, .5)
        center = g.compare_reference(v, a, reference, .5, 'center')
        self.assertEqual(plane['matched_pixels'], 63)
        self.assertEqual(plane['tangent_matched_pixels'], 15)
        self.assertAlmostEqual(plane['absolute_z_error']['mean'], .5)
        self.assertAlmostEqual(center['absolute_z_error']['mean'], .25)
        self.assertLess(plane['unoriented_normal_degrees']['mean'], 1e-5)
        a[:2, :, 11] = 0
        self.assertEqual(g.compare_reference(v, a, reference, .5)['matched_pixels'], 45)
        self.assertEqual(g.compare_reference(v, a, reference, .5, 'center')['matched_pixels'], 63)
        reference['normal_valid'] = np.zeros(shape, bool)
        empty = g.compare_reference(v, a, reference, .5)
        self.assertIsNone(empty['point_to_reference_tangent_distance']['mean'])
        self.assertIsNotNone(empty['absolute_z_error']['mean'])

    def test_invalid_reference_contract(self):
        v = view(); shape=(7, 9)
        a = np.zeros((*shape, 12))
        normal = np.zeros((*shape, 3)); normal[..., 2] = 1
        good = dict(depth_z=np.ones(shape), normal_world=normal, valid=np.ones(shape, bool))
        for update in (dict(valid=np.ones(shape)), dict(depth_z=np.zeros(shape)),
                       dict(normal_world=normal*2), dict(normal_valid=np.ones((2, 2), bool)),
                       dict(valid=np.zeros(shape, bool), normal_valid=np.ones(shape, bool))):
            with self.subTest(update=list(update)), self.assertRaises(ValueError):
                g.compare_reference(v, a, dict(good, **update), .5)
        with self.assertRaises(ValueError): g.compare_reference(v, a, good, .5, 'typo')
        with self.assertRaises(ValueError): g.compare_reference(v, a, good, 0)
        with self.assertRaises(ValueError): g.compare_reference(v, a[:2], good, .5)

    def test_prepare_camera_reservation_scale_masks_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            x = np.random.default_rng(13).normal(size=(20, 3))
            s, r, t = 3.2, rotation(), np.array([1., 2., 3.])
            y = s*x@r.T+t
            y[1] += [2, 0, 0]  # Reserved evaluation pose must never bias alignment.
            np.savez(root/'poses.npz', colmap_ids=np.arange(20), metashape_ids=np.arange(20)+100,
                     colmap_centers=x, metashape_centers=y)
            v = view(); v.update(rotation=np.eye(3).tolist(), translation=[0, 0, 0])
            g.write_json(root/'footprints.json', dict(schema_version=1, views=[v], pairs=[]))
            pose = np.eye(4); pose[:3, :3] = r; pose[:3, 3] = y[1]
            cal = dict(width=9, height=7, f=200., cx=0., cy=0.,
                       b1=0, b2=0, k1=0, k2=0, k3=0, k4=0, p1=0, p2=0)
            camera = dict(key=101, label='synthetic', transform=pose.tolist(), calibration=cal,
                          source_width=18, source_height=14,
                          ray_checks=[dict(pixel=[4.5, 3.5], ray=[0, 0, 1])])
            g.write_json(root/'raw.json', dict(meters_per_internal_unit=.5, cameras=[camera]))
            depth=np.full((7, 9), 4*s, '<f4'); depth[3, 4]=0; depth.tofile(root/'101.f32')
            args=argparse.Namespace(output=root/'prepared', depth_manifest=root/'raw.json',
                                    footprints=root/'footprints.json', matched_poses=root/'poses.npz')
            ref.prepare(args)
            alignment=json.loads((args.output/'alignment.json').read_text())
            self.assertNotIn(1, alignment['fit_colmap_ids'])
            self.assertEqual(alignment['fit_count'], 15)
            self.assertAlmostEqual(alignment['scale'], s)
            self.assertLess(alignment['fit_residual_m']['mean'], 1e-14)
            self.assertGreater(alignment['validation_residual_m']['mean'], .1)
            manifest=json.loads((args.output/'views.json').read_text())
            self.assertAlmostEqual(manifest['provenance']['source_to_meters'], 1.6)
            self.assertEqual(manifest['provenance']['adapter_sha256'], g.digest(ref.__file__))
            self.assertEqual(manifest['views'][0]['native_fx'], 400)
            with np.load(args.output/'references/1.npz') as result:
                self.assertEqual(result['valid'].sum(), 62)
                self.assertEqual(result['normal_valid'].sum(), 30)
                np.testing.assert_allclose(result['depth_z'][result['valid']], 4)
            with self.assertRaises(ValueError): ref.prepare(args)
            raw = dict(meters_per_internal_unit=.5, cameras=[camera])
            cases = []
            bad = copy.deepcopy(raw); bad['cameras'][0]['calibration']['f'] = 0; cases.append(bad)
            bad = copy.deepcopy(raw); bad['cameras'][0]['calibration']['k1'] = .01; cases.append(bad)
            bad = copy.deepcopy(raw); bad['cameras'][0]['ray_checks'] = []; cases.append(bad)
            bad = copy.deepcopy(raw); bad['cameras'][0]['ray_checks'][0]['ray'] = [1, 0, 1]; cases.append(bad)
            bad = copy.deepcopy(raw); bad['cameras'][0]['source_width'] = 0; cases.append(bad)
            bad = copy.deepcopy(raw); bad['meters_per_internal_unit'] = -1; cases.append(bad)
            bad = copy.deepcopy(raw); bad['cameras'].append(copy.deepcopy(camera)); cases.append(bad)
            for index, bad in enumerate(cases):
                with self.subTest(invalid_manifest=index):
                    g.write_json(root/'raw.json', bad)
                    args.output = root/f'invalid-{index}'
                    with self.assertRaises(ValueError): ref.prepare(args)
            g.write_json(root/'raw.json', raw)
            args.output = root/'invalid-payload'
            (root/'101.f32').write_bytes(b'truncated')
            with self.assertRaisesRegex(ValueError, 'raw depth array shape'): ref.prepare(args)


if __name__ == '__main__':
    unittest.main()
