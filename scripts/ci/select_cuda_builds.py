"""Select expensive CUDA builds from their complete dependency closure."""

from scripts.ci.cuda_change_scope import select_requirement


def main() -> int:
    return select_requirement("build_required")


if __name__ == "__main__":
    raise SystemExit(main())
