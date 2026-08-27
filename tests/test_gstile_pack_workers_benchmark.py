from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from benchmark_gstile_pack_workers import bounded_ordered_map, compress, encode_pack, synthetic_records  # noqa: E402


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_order_and_exact_encoder_output(workers):
    records, ids = synthetic_records(128)
    def job(index):
        raw, quantization, errors = encode_pack(records, ids, node_id=str(index))
        return raw, compress(raw), quantization, errors
    assert list(bounded_ordered_map(job, range(9), workers)) == [job(i) for i in range(9)]
    assert not records.flags.writeable
    assert not ids.flags.writeable


def test_submissions_are_lazy_and_bounded():
    consumed = []
    def values():
        for value in range(100):
            consumed.append(value)
            yield value
    mapped = bounded_ordered_map(lambda value: value, values(), 2)
    assert next(mapped) == 0
    assert consumed == [0, 1]
    mapped.close()
    assert consumed == [0, 1]


def test_failure_is_propagated_without_submitting_the_rest():
    consumed = []
    def values():
        for value in range(100):
            consumed.append(value)
            yield value
    def fail(value):
        raise ValueError("encode failed")
    with pytest.raises(ValueError, match="encode failed"):
        list(bounded_ordered_map(fail, values(), 2))
    assert consumed == [0, 1]


@pytest.mark.parametrize("workers", [0, 3, 5])
def test_invalid_worker_counts(workers):
    with pytest.raises(ValueError, match="workers"):
        list(bounded_ordered_map(lambda value: value, [], workers))
