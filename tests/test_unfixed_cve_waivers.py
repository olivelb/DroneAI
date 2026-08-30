from __future__ import annotations

import datetime as dt

import pytest

from scripts.ci.verify_unfixed_cves import verify_unfixed_cves


def _report(*vulnerabilities):
    return {"Results": [{"Target": "image", "Vulnerabilities": list(vulnerabilities)}]}


def _vulnerability(identifier: str, *, severity: str = "HIGH", fixed: str = ""):
    return {
        "VulnerabilityID": identifier,
        "Severity": severity,
        "FixedVersion": fixed,
    }


def _waiver(identifier: str, *, image: str = "drone-ia", expires: str = "2026-09-30"):
    return {
        "id": identifier,
        "image": image,
        "owner": "security@example.invalid",
        "reason": "No upstream fix; compensating controls reviewed.",
        "expires": expires,
    }


def test_rejects_unwaived_unfixed_high_or_critical_findings() -> None:
    report = _report(
        _vulnerability("CVE-2026-0001"),
        _vulnerability("CVE-2026-0002", severity="CRITICAL"),
        _vulnerability("CVE-2026-0003", fixed="1.2.3"),
        _vulnerability("CVE-2026-0004", severity="MEDIUM"),
    )

    with pytest.raises(ValueError, match="CVE-2026-0001, CVE-2026-0002"):
        verify_unfixed_cves(
            report,
            {"waivers": []},
            image="drone-ia",
            today=dt.date(2026, 8, 30),
        )


def test_accepts_only_an_active_image_scoped_waiver() -> None:
    report = _report(_vulnerability("CVE-2026-0001"))
    assert verify_unfixed_cves(
        report,
        {"waivers": [_waiver("CVE-2026-0001")]},
        image="drone-ia",
        today=dt.date(2026, 8, 30),
    ) == {"CVE-2026-0001"}

    with pytest.raises(ValueError, match="unwaived"):
        verify_unfixed_cves(
            report,
            {"waivers": [_waiver("CVE-2026-0001", image="drone-colmap")]},
            image="drone-ia",
            today=dt.date(2026, 8, 30),
        )


def test_rejects_expired_waivers_even_when_the_report_is_clean() -> None:
    with pytest.raises(ValueError, match="expired"):
        verify_unfixed_cves(
            _report(),
            {"waivers": [_waiver("CVE-2026-0001", expires="2026-08-29")]},
            image="drone-ia",
            today=dt.date(2026, 8, 30),
        )
