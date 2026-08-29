"""Select physical GPU qualification from inputs exercised by that suite."""

from scripts.ci.cuda_change_scope import gpu_validation_reason, select_requirement


def main() -> int:
    return select_requirement("gpu_required", gpu_validation_reason)


if __name__ == "__main__":
    raise SystemExit(main())
