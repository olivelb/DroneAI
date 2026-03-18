import numpy as np
import os

def generate_ortho_from_ply(ply_path, out_tif_path, resolution=0.1):
    with open(ply_path, 'rb') as f:
        header = []
        while True:
            line = f.readline().decode('ascii').strip()
            header.append(line)
            if line == 'end_header':
                break
        
        # Parse header to find vertex count and properties
        num_vertices = 0
        for line in header:
            if line.startswith('element vertex'):
                num_vertices = int(line.split()[-1])
        
        print(f"Reading {num_vertices} vertices...")
        
        # COLMAP fused.ply format:
        # float32 x, y, z
        # float32 nx, ny, nz
        # uint8 r, g, b
        
        dtype = np.dtype([
            ('x', 'f4'), ('y', 'f4'), ('z', 'f4'),
            ('nx', 'f4'), ('ny', 'f4'), ('nz', 'f4'),
            ('r', 'u1'), ('g', 'u1'), ('b', 'u1')
        ])
        
        data = np.fromfile(f, dtype=dtype, count=num_vertices)
        
        x = data['x']
        y = data['y']
        z = data['z']
        r = data['r']
        g = data['g']
        b = data['b']
        
        min_x, max_x = np.min(x), np.max(x)
        min_y, max_y = np.min(y), np.max(y)
        
        width = int(np.ceil((max_x - min_x) / resolution))
        height = int(np.ceil((max_y - min_y) / resolution))
        
        print(f"Creating ortho image of size {width}x{height}...")
        
        # Initialize with white or transparent
        img = np.zeros((height, width, 3), dtype=np.uint8)
        # Z-buffer to keep the highest point
        z_buffer = np.full((height, width), -np.inf, dtype=np.float32)
        
        px = ((x - min_x) / resolution).astype(np.int32)
        py = ((max_y - y) / resolution).astype(np.int32) # Invert Y for image
        
        # Clip just in case
        px = np.clip(px, 0, width - 1)
        py = np.clip(py, 0, height - 1)
        
        # We need to sort by Z so that highest Z points are drawn last (overwriting lower Z)
        # Or we can do a simple loop, but loop in python is slow.
        # Let's do an argsort on Z
        order = np.argsort(z)
        px = px[order]
        py = py[order]
        r = r[order]
        g = g[order]
        b = b[order]
        
        img[py, px, 0] = r
        img[py, px, 1] = g
        img[py, px, 2] = b
        
        print("Image created!")

# generate_ortho_from_ply('workspace/vol_test_gpu_fix/dense/fused.ply', 'test.tif')
