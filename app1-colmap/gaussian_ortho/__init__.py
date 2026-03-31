"""
gaussian_ortho — 3D Gaussian Splatting-based True Orthophoto (TDOM) Generation

Inspired by Tortho-Gaussian (Wang et al. 2024):
  - Orthogonal splatting of optimized anisotropic Gaussian kernels
  - Divide-and-conquer for large-scale scenes
  - Fully Anisotropic Gaussian Kernel (FAGK) for anti-aliasing

Integrates with the existing COLMAP dense stereo pipeline, consuming the same
workspace structure (dense/sparse/, depth_maps, alignment_transform.json).
"""

__version__ = "0.1.0"
