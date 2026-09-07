#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prepare estimated depth references with a camera-only global frame alignment.

Never fit to Gaussian positions, depth errors, or image quality. Reference camera
poses are used only for evaluation; training cameras remain unchanged.
"""
import argparse
import json
import math
from pathlib import Path
import numpy as np
from geometry_diagnostics import digest, rays, stats, validate_manifest, validate_view, write_json


def fit_similarity(source, target):
    x, y = np.asarray(source, dtype=np.float64), np.asarray(target, dtype=np.float64)
    if x.shape != y.shape or x.ndim != 2 or x.shape[1] != 3 or len(x) < 4:
        raise ValueError('at least four matched 3D camera centers are required')
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('nonfinite camera centers')
    mx, my = x.mean(0), y.mean(0)
    xx, yy = x-mx, y-my
    if np.linalg.matrix_rank(xx) < 2 or np.linalg.matrix_rank(yy) < 2:
        raise ValueError('camera centers are collinear/coincident; alignment is ambiguous')
    u, d, vt = np.linalg.svd(yy.T @ xx/len(x))
    sign = np.ones(3); sign[-1] = np.linalg.det(u@vt)
    rotation = u @ np.diag(sign) @ vt
    scale = float((d*sign).sum()/np.mean(np.sum(xx*xx, axis=1)))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('invalid similarity scale')
    return scale, rotation, my-scale*rotation@mx


def _rotation(value):
    rotation = np.asarray(value, dtype=np.float64)
    if (rotation.shape != (3, 3) or not np.isfinite(rotation).all()
            or not np.allclose(rotation @ rotation.T, np.eye(3), rtol=0, atol=1e-6)
            or not np.isclose(np.linalg.det(rotation), 1, rtol=0, atol=1e-6)):
        raise ValueError('rotation must be orthonormal and right handed')
    return rotation


def camera_in_source(reference_camera_to_world, scale, rotation, translation):
    """Reference world = s R source world + t; keep camera axes orthonormal."""
    pose = np.asarray(reference_camera_to_world, dtype=np.float64)
    if pose.shape != (4, 4) or not np.isfinite(pose).all():
        raise ValueError('invalid reference camera transform')
    if not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-8):
        raise ValueError('invalid homogeneous transform')
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('invalid similarity scale')
    rotation = _rotation(rotation)
    _rotation(pose[:3, :3])
    translation = np.asarray(translation, dtype=np.float64)
    if translation.shape != (3,) or not np.isfinite(translation).all():
        raise ValueError('invalid similarity translation')
    center = rotation.T @ (pose[:3, 3]-translation)/scale
    camera_to_world = rotation.T @ pose[:3, :3]
    return camera_to_world.T, -camera_to_world.T @ center


def normals_from_depth(view, depth, relative_jump=.01):
    """Estimated normals only. Keep depth validity separate from normal support."""
    validate_view(view)
    depth = np.asarray(depth, dtype=np.float64)
    if depth.shape != (view['height'], view['width']):
        raise ValueError('depth shape differs from view')
    if not math.isfinite(relative_jump) or relative_jump < 0:
        raise ValueError('invalid normal neighborhood jump threshold')
    # Invalid depths have no geometric meaning; zero them before differencing.
    depth = np.where(np.isfinite(depth) & (depth > 0), depth, 0)
    y, x = np.indices(depth.shape)
    points = rays(view, x, y)*depth[..., None]
    valid = np.isfinite(depth) & (depth > 0)
    neighbors = valid[1:-1, 1:-1] & valid[:-2, 1:-1] & valid[2:, 1:-1] & valid[1:-1, :-2] & valid[1:-1, 2:]
    center = depth[1:-1, 1:-1]
    change = np.maximum.reduce([np.abs(depth[:-2, 1:-1]-center), np.abs(depth[2:, 1:-1]-center),
                                np.abs(depth[1:-1, :-2]-center), np.abs(depth[1:-1, 2:]-center)])
    neighbors &= change <= relative_jump*center
    cross = np.cross(points[1:-1, 2:]-points[1:-1, :-2], points[2:, 1:-1]-points[:-2, 1:-1])
    length = np.linalg.norm(cross, axis=-1)
    neighbors &= np.isfinite(length) & (length > 0)
    normal = np.zeros((*depth.shape, 3))
    normal[1:-1, 1:-1] = np.where(neighbors[..., None], cross/np.where(length > 0, length, 1)[..., None], 0)
    normal_valid = np.zeros(depth.shape, bool); normal_valid[1:-1, 1:-1] = neighbors
    return normal @ np.asarray(view['rotation']), normal_valid


def prepare(args):
    output = Path(args.output)
    if output.exists():
        raise ValueError('output must not exist')
    raw_root = Path(args.depth_manifest).parent
    raw = json.loads(Path(args.depth_manifest).read_text(encoding='utf-8'))
    footprints = validate_manifest(json.loads(Path(args.footprints).read_text(encoding='utf-8')))
    data = np.load(args.matched_poses, allow_pickle=False)
    ids = data['colmap_ids']
    reserved = np.isin(ids, [v['id'] for v in footprints['views']])
    fit = (np.arange(len(ids)) % 5 != 0) & ~reserved
    s, r, t = fit_similarity(data['colmap_centers'][fit], data['metashape_centers'][fit])
    meters = raw['meters_per_internal_unit']
    if not math.isfinite(meters) or meters <= 0:
        raise ValueError('invalid reference metric scale')
    residual = np.linalg.norm(s*data['colmap_centers']@r.T+t-data['metashape_centers'], axis=1)*meters
    alignment = dict(scale=s, rotation=r.tolist(), translation=t.tolist(),
        source_to_meters=s*meters, reference_to_meters=meters,
        method='camera-center-only similarity, no surface fitting or per-view adjustment',
        fit_count=int(fit.sum()), validation_count=int((~fit).sum()),
        fit_residual_m=stats(residual[fit]), validation_residual_m=stats(residual[~fit]),
        reserved_colmap_ids=ids[~fit].tolist(), fit_colmap_ids=ids[fit].tolist())
    by_id = {int(c['key']): c for c in raw['cameras']}
    if len(by_id) != len(raw['cameras']):
        raise ValueError('duplicate reference camera key')
    output.mkdir(parents=True); (output/'references').mkdir()
    write_json(output/'alignment.json', alignment)
    views, checks = [], []
    for original in footprints['views']:
        matched = np.flatnonzero(ids == original['id'])
        if len(matched) != 1: raise ValueError('camera match is not unique')
        index = matched[0]; camera = by_id[int(data['metashape_ids'][index])]
        cal = camera['calibration']
        for key in ('b1', 'b2', 'k1', 'k2', 'k3', 'k4', 'p1', 'p2'):
            if cal[key] != 0: raise ValueError('reference depth grid must be undistorted pinhole')
        for key in ('width', 'height'):
            if type(cal[key]) is not int or cal[key] < 1:
                raise ValueError('invalid reference depth grid dimensions')
        for key in ('source_width', 'source_height'):
            if type(camera[key]) is not int or camera[key] < 1:
                raise ValueError('invalid source image dimensions')
        if not math.isfinite(cal['f']) or cal['f'] <= 0 or not all(math.isfinite(cal[k]) for k in ('cx', 'cy')):
            raise ValueError('invalid reference pinhole calibration')
        if not camera['ray_checks']:
            raise ValueError('reference API ray checks are required')
        f = cal['f']; cx = cal['width']/2+cal['cx']; cy = cal['height']/2+cal['cy']
        for check in camera['ray_checks']:
            u, v = check['pixel']
            if not np.allclose([(u-cx)/f, (v-cy)/f, 1], check['ray'], rtol=0, atol=1e-10):
                raise ValueError('reference API ray convention mismatch')
        # Preserve the previous angular crop, clipped to available reference grid.
        # This is frozen before viewing any depth or Gaussian residuals.
        x0 = max(0, math.floor(cx-f*original['cx']/original['fx']))
        y0 = max(0, math.floor(cy-f*original['cy']/original['fy']))
        x1 = min(cal['width'], math.ceil(cx+f*(original['width']-original['cx'])/original['fx']))
        y1 = min(cal['height'], math.ceil(cy+f*(original['height']-original['cy'])/original['fy']))
        if x1-x0 < 3 or y1-y0 < 3: raise ValueError('reference angular crop has insufficient pixels')
        cr, ct = camera_in_source(camera['transform'], s, r, t)
        v = dict(id=original['id'], width=x1-x0, height=y1-y0, fx=f, fy=f, cx=cx-x0, cy=cy-y0,
            native_fx=f*camera['source_width']/cal['width'],
            native_fy=f*camera['source_height']/cal['height'], rotation=cr.tolist(), translation=ct.tolist(),
            reference_camera_id=camera['key'], image_name=camera['label'],
            reference_grid_crop=[x0, y0, x1-x0, y1-y0],
            sampling='existing Metashape depth grid', source_size=[camera['source_width'], camera['source_height']],
            evaluation_pose_source='Metashape camera transformed into unchanged COLMAP model frame')
        validate_view(v)
        path = raw_root/f"{camera['key']}.f32"
        if path.stat().st_size != cal['width']*cal['height']*4:
            raise ValueError('invalid raw depth array shape')
        full = np.memmap(path, dtype='<f4', mode='r', shape=(cal['height'], cal['width']))
        depth = np.asarray(full[y0:y1, x0:x1], dtype=np.float64)/s
        # Preserve missing depths; never inpaint or smooth them for scoring.
        valid = np.isfinite(depth) & (depth > 0)
        depth = np.where(valid, depth, 0)
        normal, normal_valid = normals_from_depth(v, depth)
        np.savez_compressed(output/'references'/f"{v['id']}.npz", depth_z=depth.astype(np.float32),
                            normal_world=normal.astype(np.float32), valid=valid, normal_valid=normal_valid)
        views.append(v)
        checks.append(dict(id=v['id'], raw_depth_sha256=digest(path), valid_fraction=float(valid.mean()),
            normal_valid_fraction=float(normal_valid.mean()), camera_center_disagreement_m=float(residual[index])))
    manifest = dict(schema_version=1, views=views, pairs=footprints['pairs'],
        provenance=dict(reference='Metashape estimated depth; metric scale user-validated',
            source_to_meters=s*meters, adapter_sha256=digest(__file__), alignment_sha256=digest(output/'alignment.json'),
            source_footprints_sha256=digest(args.footprints), raw_manifest_sha256=digest(args.depth_manifest),
            matched_poses_sha256=digest(args.matched_poses), normals='central differences; 1% relative neighbor jump gate; not independent normal ground truth',
            camera_policy='Reference camera evaluation only. COLMAP/CASPER training poses unchanged.'))
    write_json(output/'views.json', validate_manifest(manifest))
    write_json(output/'reference-checks.json', checks)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for option in ('matched-poses', 'depth-manifest', 'footprints', 'output'):
        p.add_argument('--'+option, required=True)
    prepare(p.parse_args())
