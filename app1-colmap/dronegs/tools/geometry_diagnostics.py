#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline geometry diagnostics. No training, registration, or parameter fitting.

Pixel centers are (x+.5,y+.5), R,t transform world to camera, depth is camera Z.
The view manifest is the fixed evaluation footprint, independent of cell layout.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np

CHANNELS = ['coverage', 'center_z', 'center_std_z', 'covariance_sigma_z',
            'minimum_axis_sigma', 'normal_x', 'normal_y', 'normal_z',
            'normal_support_coherence', 'plane_z', 'plane_std_z', 'plane_support']


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def validate_view(v):
    for key in ('id', 'width', 'height'):
        if type(v[key]) is not int or v[key] < (0 if key == 'id' else 1):
            raise ValueError(f'invalid {key}')
    if v['width'] * v['height'] > 16777216:
        raise ValueError('view exceeds 16M pixels; use a fixed crop or explicit downsampling')
    for k in ('fx', 'fy', 'cx', 'cy', 'native_fx', 'native_fy'):
        if not math.isfinite(v[k]) or (k not in ('cx', 'cy') and v[k] <= 0):
            raise ValueError(f'invalid {k}')
    r = np.asarray(v['rotation'], dtype=np.float64)
    t = np.asarray(v['translation'], dtype=np.float64)
    if r.shape != (3, 3) or t.shape != (3,) or not np.isfinite(r).all() or not np.isfinite(t).all():
        raise ValueError('invalid camera extrinsics')
    if not np.allclose(r @ r.T, np.eye(3), rtol=0, atol=1e-6) or not np.isclose(np.linalg.det(r), 1, atol=1e-6):
        raise ValueError('camera rotation must be orthonormal and right handed')
    if v['native_fx'] < v['fx'] or v['native_fy'] < v['fy']:
        raise ValueError('upsampling is not supported for diagnostic evaluation')
    return v


def validate_manifest(manifest):
    if manifest['schema_version'] != 1 or not 1 <= len(manifest['views']) <= 32:
        raise ValueError('expected schema 1 and 1..32 fixed views')
    ids = set()
    for v in manifest['views']:
        validate_view(v)
        if v['id'] in ids:
            raise ValueError('duplicate view id')
        ids.add(v['id'])
    pairs = manifest.get('pairs', [])
    if len(pairs) > 128 or len(set(map(tuple, pairs))) != len(pairs):
        raise ValueError('at most 128 unique directed pairs')
    for a, b in pairs:
        if a == b or a not in ids or b not in ids:
            raise ValueError('invalid directed pair')
    return manifest


def colmap_view(row, factor, crop=None):
    """Convert the native COLMAP export; crop is in undistorted source pixels."""
    if len(row) != 19 or factor < 1 or int(factor) != factor:
        raise ValueError('invalid camera row/downsample factor')
    view_id, width, height = map(int, row[:3])
    if crop is None:
        crop = [0, 0, width, height]
    x, y, w, h = crop
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
        raise ValueError('crop outside undistorted image')
    ow, oh = max(1, math.ceil(w / factor)), max(1, math.ceil(h / factor))
    sx, sy = ow / w, oh / h
    fx, fy, cx, cy = row[3:7]
    return validate_view(dict(id=view_id, width=ow, height=oh, fx=fx*sx, fy=fy*sy,
        cx=(cx-x)*sx, cy=(cy-y)*sy, native_fx=fx, native_fy=fy,
        rotation=np.asarray(row[7:16]).reshape(3, 3).tolist(), translation=row[16:19],
        source_size=[width, height], source_crop=crop, downsample_factor=factor))


def stats(values):
    a = np.asarray(values, dtype=np.float64).reshape(-1)
    if not np.isfinite(a).all():
        raise ValueError('nonfinite metric values')
    if not len(a):
        return {'count': 0, 'mean': None, 'p50': None, 'p90': None, 'p95': None}
    return dict(count=len(a), mean=float(a.mean()),
                **{f'p{p}': float(np.percentile(a, p)) for p in (50, 90, 95)})


def rays(view, x, y):
    return np.stack(((x+.5-view['cx'])/view['fx'],
                     (y+.5-view['cy'])/view['fy'], np.ones_like(x)), axis=-1)


def surface_footprint(view, ray, depth, normals):
    """Geometric mean linear footprint of native pixels on candidate tangent plane.

    This is not triangulation/depth accuracy. Grazing incidence is unsupported.
    """
    dot = np.sum(normals*ray, axis=-1)
    supported = (np.abs(dot) > 1e-4*np.linalg.norm(ray, axis=-1)) & (depth > 0)
    safe = np.where(supported, dot, 1)
    du = (np.array([1., 0, 0])-ray*(normals[..., 0]/safe)[..., None]) * (depth/view['native_fx'])[..., None]
    dv = (np.array([0., 1, 0])-ray*(normals[..., 1]/safe)[..., None]) * (depth/view['native_fy'])[..., None]
    footprint = np.sqrt(np.linalg.norm(np.cross(du, dv), axis=-1))
    return footprint, supported & np.isfinite(footprint) & (footprint > 0)


def load_frame(root, view):
    shape = (view['height'], view['width'], len(CHANNELS))
    path = Path(root)/f"{view['id']}.geometry.f32"
    if path.stat().st_size != math.prod(shape)*4:
        raise ValueError(f'wrong array size: {path}')
    a = np.memmap(path, dtype='<f4', mode='r', shape=shape)
    if not np.isfinite(a).all() or (a[..., 0] < 0).any() or (a[..., 0] > 1.00001).any():
        raise ValueError('invalid geometry values')
    if (a[..., 11] < 0).any() or (a[..., 11] > a[..., 0]+1e-5).any():
        raise ValueError('invalid plane support')
    if (a[..., [1, 2, 3, 4, 8, 9, 10]] < 0).any() or (a[..., 8] > 1.00001).any():
        raise ValueError('invalid geometry moments')
    if ((a[..., 0] > 0) & (a[..., 1] <= 0)).any() or ((a[..., 11] > 0) & (a[..., 9] <= 0)).any():
        raise ValueError('supported depth must be positive')
    return a


def view_summary(view, a, coverage_threshold):
    supported = a[..., 0] >= coverage_threshold
    plane = a[..., 11] >= coverage_threshold
    # Bound temporary arrays to sampled pixels; original maps stay on disk.
    step = max(1, math.ceil(a.shape[0]*a.shape[1]/100000))
    indices = np.arange(0, a.shape[0]*a.shape[1], step)
    y, x = np.divmod(indices, a.shape[1])
    samples = a[y, x]
    footprint, valid = surface_footprint(view, rays(view, x, y), samples[:, 9], samples[:, 5:8])
    valid &= samples[:, 11] >= coverage_threshold
    return dict(id=view['id'], total_pixels=int(supported.size),
        coverage_mean=float(a[..., 0].mean(dtype=np.float64)),
        coverage_fraction=float(supported.mean()), plane_support_fraction=float(plane.mean()),
        metrics={CHANNELS[i]: stats(a[..., i][supported if i < 9 else plane])
                 for i in (1, 2, 3, 4, 8, 9, 10)},
        footprint_sample_count=int(valid.sum()),
        native_surface_footprint=stats(footprint[valid]),
        minimum_axis_sigma_per_native_surface_pixel=stats(samples[valid, 4]/footprint[valid]))


def compare_pair(source, target, a, b, kind='plane', coverage_threshold=.5,
                 max_samples=100000, relative_depth_tolerance=.01):
    """Model self-consistency, NOT geometric ground truth.

    All comparable residuals are retained, including possible occlusions. Nearest
    target pixel sampling introduces up to sqrt(.5) pixel quantization in target.
    Categories are diagnostics, never a mask that silently removes large errors.
    """
    depth, support = (9, 11) if kind == 'plane' else (1, 0)
    step = max(1, math.ceil(a.shape[0]*a.shape[1]/max_samples))
    index = np.arange(0, a.shape[0]*a.shape[1], step)
    y, x = np.divmod(index, a.shape[1])
    valid = (a[y, x, support] >= coverage_threshold) & (a[y, x, depth] > 0)
    sampled, supported = len(index), int(valid.sum())
    y, x = y[valid], x[valid]
    r1, r2 = np.asarray(source['rotation']), np.asarray(target['rotation'])
    t1, t2 = np.asarray(source['translation']), np.asarray(target['translation'])
    world = (rays(source, x, y)*a[y, x, depth, None]-t1) @ r1
    camera = world @ r2.T + t2
    z = camera[:, 2]
    positive = z > 1e-4
    safe_z = np.where(positive, z, 1)
    u = camera[:, 0]/safe_z*target['fx']+target['cx']
    v = camera[:, 1]/safe_z*target['fy']+target['cy']
    inside = positive & (u >= 0) & (v >= 0) & (u < target['width']) & (v < target['height'])
    in_frame = int(inside.sum())
    x, y, z = x[inside], y[inside], z[inside]
    tx, ty = np.floor(u[inside]).astype(int), np.floor(v[inside]).astype(int)
    available = (b[ty, tx, support] >= coverage_threshold) & (b[ty, tx, depth] > 0)
    x, y, z, tx, ty = (item[available] for item in (x, y, z, tx, ty))
    target_z = b[ty, tx, depth]
    signed = (z-target_z)/z
    target_world = (rays(target, tx, ty)*target_z[:, None]-t2) @ r2
    back = target_world @ r1.T+t1
    back_valid = back[:, 2] > 1e-4
    back_u = back[back_valid, 0]/back[back_valid, 2]*source['fx']+source['cx']
    back_v = back[back_valid, 1]/back[back_valid, 2]*source['fy']+source['cy']
    roundtrip = np.hypot(back_u-(x[back_valid]+.5), back_v-(y[back_valid]+.5))
    n1, n2 = a[y, x, 5:8] @ r1, b[ty, tx, 5:8] @ r2
    normal_valid = (a[y, x, 8] >= coverage_threshold) & (b[ty, tx, 8] >= coverage_threshold)
    angle = np.degrees(np.arccos(np.clip(np.abs(np.sum(n1[normal_valid]*n2[normal_valid], axis=1)), 0, 1)))
    return dict(source=source['id'], target=target['id'], depth_kind=kind,
        sampled_pixels=sampled, source_supported=supported, projected_in_frame=in_frame,
        target_supported=len(z), evaluated_fraction=len(z)/sampled,
        source_unsupported=sampled-supported, out_of_frame_or_behind=supported-in_frame,
        target_unsupported=in_frame-len(z),
        possible_occlusion_count=int((signed > relative_depth_tolerance).sum()),
        foreground_conflict_count=int((signed < -relative_depth_tolerance).sum()),
        back_projection_behind_count=int((~back_valid).sum()),
        absolute_relative_z_error=stats(np.abs(signed)),
        roundtrip_pixels=stats(roundtrip), unoriented_normal_degrees=stats(angle))


def compare_reference(view, a, reference, threshold, kind="plane"):
    if kind not in ('plane', 'center'):
        raise ValueError('reference depth kind must be plane or center')
    if not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError('invalid reference coverage threshold')
    if a.shape != (view['height'], view['width'], len(CHANNELS)):
        raise ValueError('geometry shape differs from the fixed view')
    depth_channel, support_channel = (9, 11) if kind == "plane" else (1, 0)
    z = reference['depth_z']
    normal = reference['normal_world']
    valid = reference['valid']
    normal_valid = reference['normal_valid'] if 'normal_valid' in reference else valid
    shape = (view['height'], view['width'])
    if z.shape != shape or normal.shape != (*shape, 3) or valid.shape != shape or valid.dtype != bool:
        raise ValueError('reference shapes/types differ from the fixed view')
    if normal_valid.shape != shape or normal_valid.dtype != bool or (normal_valid & ~valid).any():
        raise ValueError('invalid reference normal support')
    if not np.isfinite(z[valid]).all() or (z[valid] <= 0).any() or not np.isfinite(normal[normal_valid]).all():
        raise ValueError('invalid reference depths/normals')
    if not np.allclose(np.linalg.norm(normal[normal_valid], axis=-1), 1, atol=1e-5):
        raise ValueError('reference normals must be unit vectors')
    hit = valid & (a[..., support_channel] >= threshold)
    tangent_hit = hit & normal_valid
    y, x = np.nonzero(tangent_hit)
    delta = (a[y, x, depth_channel]-z[y, x])[:, None]*rays(view, x, y)
    camera_normal = normal[y, x] @ np.asarray(view['rotation']).T
    distance = np.abs(np.sum(delta*camera_normal, axis=1))
    normal_hit = tangent_hit & (a[..., 8] >= threshold)
    ours = a[..., 5:8][normal_hit] @ np.asarray(view['rotation'])
    angle = np.degrees(np.arccos(np.clip(np.abs(np.sum(ours*normal[normal_hit], axis=1)), 0, 1)))
    return dict(depth_kind=kind, reference_pixels=int(valid.sum()), matched_pixels=int(hit.sum()),
        reference_normal_pixels=int(normal_valid.sum()), tangent_matched_pixels=int(tangent_hit.sum()),
        reference_coverage_fraction=float(hit.sum()/valid.sum()) if valid.any() else None,
        point_to_reference_tangent_distance=stats(distance),
        absolute_z_error=stats(np.abs(a[..., depth_channel][hit]-z[hit])),
        unoriented_normal_degrees=stats(angle))


def prepare(args):
    output = Path(args.output)
    if output.exists():
        raise ValueError('output must not exist')
    output.mkdir(parents=True)
    table = output/'colmap-cameras.txt'
    subprocess.run([args.binary, '--export-colmap', args.colmap_path, str(table)], check=True)
    rows = [list(map(float, row.split())) for row in table.read_text().splitlines()]
    lookup = {int(row[0]): row for row in rows}
    if len(set(args.image_ids)) != len(args.image_ids):
        raise ValueError('duplicate image ids')
    views = [colmap_view(lookup[i], args.downsample, args.crop) for i in args.image_ids]
    # Explicit bounded pairs, determined by the chosen cameras, never by scores.
    pairs = [[views[i]['id'], views[j]['id']] for i in range(len(views))
             for j in range(len(views)) if i != j]
    result = validate_manifest(dict(schema_version=1, views=views, pairs=pairs,
        provenance=dict(colmap_path=str(Path(args.colmap_path).resolve()),
                        camera_export_sha256=digest(table), binary_sha256=digest(args.binary))))
    write_json(output/'views.json', result)


def run(args):
    manifest_path = Path(args.views_json)
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding='utf-8')))
    if not 0 < args.coverage_threshold <= 1 or not 0 < args.relative_depth_tolerance < 1:
        raise ValueError('invalid coverage/depth classification thresholds')
    root = Path(args.output)
    if root.exists():
        raise ValueError('output must not exist; preserve prior runs')
    root.mkdir(parents=True)
    started = time.time()
    status = dict(status='running', schema_version=1, started_unix=started)
    write_json(root/'status.json', status)
    try:
        write_json(root/'views.json', manifest)
        model_hash = digest(args.ply)
        provenance = dict(model_path=str(Path(args.ply).resolve()), model_sha256=model_hash,
                          binary_sha256=digest(args.binary), tool_sha256=digest(__file__),
                          views_sha256=digest(manifest_path))
        write_json(root/'provenance.json', provenance)
        rows = []
        for v in manifest['views']:
            row = [v['id'], v['width'], v['height'], v['fx'], v['fy'], v['cx'], v['cy'],
                   *np.asarray(v['rotation']).reshape(-1), *v['translation']]
            rows.append(' '.join(str(value) for value in row))
        camera_path = root/'cameras.txt'
        camera_path.write_text('\n'.join(rows)+'\n')
        command = [args.binary, args.ply, str(camera_path), str(root/'native'), args.raster_profile]
        write_json(root/'command.json', command)
        with (root/'native.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        complete = json.loads((root/'native/complete.json').read_text())
        normal_path = root/'native/gaussian_normals.f32'
        if normal_path.stat().st_size != complete['gaussians']*6*4:
            raise ValueError('Gaussian normal sidecar shape mismatch')
        views = {v['id']: v for v in manifest['views']}
        summaries = []
        for view in views.values():
            frame = load_frame(root/'native', view)
            summary = view_summary(view, frame, args.coverage_threshold)
            if args.reference_dir:
                ref_path = Path(args.reference_dir)/f"{view['id']}.npz"
                with np.load(ref_path, allow_pickle=False) as reference:
                    summary['reference'] = compare_reference(view, frame, reference, args.coverage_threshold)
                    summary['center_reference'] = compare_reference(view, frame, reference, args.coverage_threshold, kind='center')
                summary['reference_sha256'] = digest(ref_path)
            summaries.append(summary)
        pairs = []
        for i, j in manifest.get('pairs', []):
            a, b = load_frame(root/'native', views[i]), load_frame(root/'native', views[j])
            for kind in ('center', 'plane'):
                pairs.append(compare_pair(views[i], views[j], a, b, kind, args.coverage_threshold,
                                          relative_depth_tolerance=args.relative_depth_tolerance))
        if digest(args.ply) != model_hash:
            raise ValueError('input model changed during diagnostic run')
        result = dict(schema_version=1, provenance=provenance, native=complete, channels=CHANNELS,
            policy=dict(coverage_threshold=args.coverage_threshold,
                        relative_depth_tolerance=args.relative_depth_tolerance,
                        target_sampling='nearest_pixel', max_pair_samples=100000,
                        occlusion_policy='report_categories_keep_all_comparable_residuals'),
            gaussian_normals=dict(file='native/gaussian_normals.f32', dtype='little_endian_float32',
                row_order='input_PLY_vertex_order', sha256=digest(normal_path),
                channels=['nx_world', 'ny_world', 'nz_world', 'planarity_support', 'sigma_min', 'sigma_mid'],
                orientation='unoriented_canonical_sign', model_sha256=model_hash),
            views=summaries, pairs=pairs,
            limitations=['Self-consistency is not ground-truth accuracy.',
                         'Tangent-plane depth is an experimental proxy with explicit support and spread.',
                         'Native surface footprint is not depth uncertainty.',
                         'Minimum-axis sigma is intrinsic thinness, not distance to a true surface.',
                         'No alignment, pose adjustment, training, or scene-specific threshold fitting.'])
        write_json(root/'report.json', result)
        status.update(status='completed', wall_seconds=time.time()-started)
    except Exception as error:
        status.update(status='failed', error=str(error), wall_seconds=time.time()-started)
        write_json(root/'status.json', status)
        raise
    write_json(root/'status.json', status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare-colmap')
    p.add_argument('--binary', required=True)
    p.add_argument('--colmap-path', required=True)
    p.add_argument('--image-ids', type=int, nargs='+', required=True)
    p.add_argument('--downsample', type=int, default=1)
    p.add_argument('--crop', type=int, nargs=4, metavar=('X', 'Y', 'W', 'H'))
    p.add_argument('--output', required=True)
    p.set_defaults(function=prepare)
    p = sub.add_parser('run')
    for name in ('binary', 'ply', 'views-json', 'output'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--raster-profile', choices=['reference', 'fastgs'], default='fastgs')
    p.add_argument('--coverage-threshold', type=float, default=.5)
    p.add_argument('--relative-depth-tolerance', type=float, default=.01)
    p.add_argument('--reference-dir')
    p.set_defaults(function=run)
    args = parser.parse_args()
    args.function(args)


if __name__ == '__main__':
    main()
