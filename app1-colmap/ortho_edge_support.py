"""Helpers for conservative edge handling in the TrueOrtho pipeline."""


def build_edge_assignment_paint_mask(valid_mask, edge_assignment_mask, support_distance_px, max_support_distance_px):
    """Limit direct image warps in the wide edge band to pixels with close real support."""
    if float(max_support_distance_px) < 0:
        return valid_mask & True, edge_assignment_mask & False

    edge_paint_rejected_mask = edge_assignment_mask & (support_distance_px > float(max_support_distance_px))
    return valid_mask & (~edge_paint_rejected_mask), edge_paint_rejected_mask


def build_mixed_depth_rejection_mask(
    raw_valid_mask,
    edge_assignment_mask,
    sample_count,
    raw_depth_min,
    raw_depth_max,
    max_depth_spread_m,
    min_samples,
):
    """Reject edge-band cells whose raw depth samples contain conflicting surfaces."""
    if float(max_depth_spread_m) <= 0:
        return raw_valid_mask & False

    return (
        raw_valid_mask
        & edge_assignment_mask
        & (sample_count >= float(min_samples))
        & ((raw_depth_max - raw_depth_min) >= float(max_depth_spread_m))
    )