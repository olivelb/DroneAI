import torch
import _nvdiffrast_c


def get_log_level():
    return _nvdiffrast_c.get_log_level()


def set_log_level(level):
    _nvdiffrast_c.set_log_level(level)


class RasterizeCudaContext:
    def __init__(self, device=None):
        if device is None:
            cuda_device_idx = torch.cuda.current_device()
        else:
            with torch.cuda.device(device):
                cuda_device_idx = torch.cuda.current_device()
        self.cpp_wrapper = _nvdiffrast_c.RasterizeCRStateWrapper(cuda_device_idx)
        self.active_depth_peeler = None


def rasterize(glctx, pos, tri, resolution, ranges=None, grad_db=True):
    assert isinstance(glctx, RasterizeCudaContext)
    assert isinstance(pos, torch.Tensor)
    assert isinstance(tri, torch.Tensor)
    resolution = tuple(resolution)
    if ranges is None:
        ranges = torch.empty(size=(0, 2), dtype=torch.int32, device="cpu")
    out, out_db = _nvdiffrast_c.rasterize_fwd_cuda(
        glctx.cpp_wrapper,
        pos.contiguous(),
        tri.contiguous(),
        resolution,
        ranges,
        -1,
    )
    return out, out_db


def interpolate(attr, rast, tri, rast_db=None, diff_attrs=None):
    if rast_db is not None or diff_attrs not in (None, [], ()):
        raise NotImplementedError("This local wrapper only supports interpolate() without derivatives")
    return _nvdiffrast_c.interpolate_fwd(attr.contiguous(), rast.contiguous(), tri.contiguous())


def texture_construct_mip(tex, max_mip_level=None, cube_mode=False):
    tex = tex.to(dtype=torch.float32).contiguous()
    if max_mip_level is None:
        max_mip_level = -1
    else:
        max_mip_level = int(max_mip_level)
    return _nvdiffrast_c.texture_construct_mip(tex, max_mip_level, bool(cube_mode))


class _TextureFuncMip(torch.autograd.Function):
    @staticmethod
    def forward(ctx, filter_mode, tex, uv, uv_da, mip_level_bias, mip_wrapper, filter_mode_enum, boundary_mode_enum, *mip_stack):
        empty = torch.empty((0,), dtype=torch.float32, device=tex.device)
        if uv_da is None:
            uv_da = empty
        if mip_level_bias is None:
            mip_level_bias = empty
        if mip_wrapper is None:
            mip_wrapper = _nvdiffrast_c.TextureMipWrapper()
        return _nvdiffrast_c.texture_fwd_mip(
            tex,
            uv,
            uv_da,
            mip_level_bias,
            mip_wrapper,
            mip_stack,
            filter_mode_enum,
            boundary_mode_enum,
        )


def texture(tex, uv, uv_da=None, mip_level_bias=None, mip=None, filter_mode="auto", boundary_mode="wrap", max_mip_level=None):
    if filter_mode == "auto":
        filter_mode = "linear-mipmap-linear" if (uv_da is not None or mip_level_bias is not None) else "linear"
    filter_mode_dict = {
        "nearest": 0,
        "linear": 1,
        "linear-mipmap-nearest": 2,
        "linear-mipmap-linear": 3,
    }
    boundary_mode_dict = {"cube": 0, "wrap": 1, "clamp": 2, "zero": 3}
    if filter_mode not in filter_mode_dict:
        raise NotImplementedError(f"Unsupported filter_mode: {filter_mode}")
    if boundary_mode not in boundary_mode_dict:
        raise NotImplementedError(f"Unsupported boundary_mode: {boundary_mode}")

    tex = tex.to(dtype=torch.float32).contiguous()
    uv = uv.to(dtype=torch.float32).contiguous()
    filter_mode_enum = filter_mode_dict[filter_mode]
    boundary_mode_enum = boundary_mode_dict[boundary_mode]

    if "mipmap" in filter_mode:
        if max_mip_level is None:
            max_mip_level = -1
        else:
            max_mip_level = int(max_mip_level)

        empty = torch.empty((0,), dtype=torch.float32, device=tex.device)
        uv_da_tensor = empty if uv_da is None else uv_da.to(dtype=torch.float32).contiguous()
        mip_level_bias_tensor = empty if mip_level_bias is None else mip_level_bias.to(dtype=torch.float32).contiguous()

        mip_wrapper = _nvdiffrast_c.TextureMipWrapper()
        mip_stack = []
        if mip is not None:
            if isinstance(mip, list):
                mip_stack = [level.to(dtype=torch.float32).contiguous() for level in mip]
            else:
                mip_wrapper = mip
        else:
            mip_wrapper = _nvdiffrast_c.texture_construct_mip(tex, max_mip_level, boundary_mode == "cube")

        uv_da_arg = None if uv_da is None else uv_da_tensor
        mip_bias_arg = None if mip_level_bias is None else mip_level_bias_tensor
        return _TextureFuncMip.apply(
            filter_mode,
            tex,
            uv,
            uv_da_arg,
            mip_bias_arg,
            mip_wrapper,
            filter_mode_enum,
            boundary_mode_enum,
            *mip_stack,
        )

    return _nvdiffrast_c.texture_fwd(tex, uv, filter_mode_enum, boundary_mode_enum)
