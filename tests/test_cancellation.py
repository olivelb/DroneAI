from shared.cancellation import AttemptCancellationRegistry


def test_cancellation_is_scoped_to_campaign_attempt():
    registry = AttemptCancellationRegistry()
    registry.cancel("mission-1", "run-1", 0)

    assert registry.is_cancelled("mission-1", "run-1", 0)
    assert not registry.is_cancelled("mission-1", "run-1", 1)
    assert not registry.is_cancelled("mission-1", "run-2", 0)


def test_cancellation_can_be_cleared_without_affecting_other_attempts():
    registry = AttemptCancellationRegistry()
    registry.cancel("mission-1", "run-1", 0)
    registry.cancel("mission-1", "run-1", 1)

    registry.clear("mission-1", "run-1", 1)

    assert registry.is_cancelled("mission-1", "run-1", 0)
    assert not registry.is_cancelled("mission-1", "run-1", 1)


def test_mission_cancel_does_not_cancel_independent_analysis():
    registry = AttemptCancellationRegistry()
    registry.cancel("mission-1")

    assert registry.is_cancelled("mission-1")
    assert not registry.is_cancelled("mission-1", "analysis-1")
