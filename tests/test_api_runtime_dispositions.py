"""Exact reviewed API findings; new versions/CVEs cannot inherit a waiver."""
import copy
import datetime as dt
import json
from pathlib import Path

import pytest

from scripts.ci.verify_unfixed_cves import verify_unfixed_cves

ROOT = Path(__file__).resolve().parents[1]


def test_api_dispositions_are_complete_and_expire():
    report = json.loads((ROOT / "tests/fixtures/api_runtime_findings_20260908.json").read_text())
    waivers = json.loads((ROOT / "security/unfixed-cve-waivers.json").read_text())
    arguments = {"image": "drone-dashboard-api", "today": dt.date(2026, 9, 8)}
    assert len(verify_unfixed_cves(report, waivers, **arguments)) == 12
    for index in range(len(waivers["waivers"])):
        missing = copy.deepcopy(waivers)
        missing["waivers"].pop(index)
        with pytest.raises(ValueError, match="unwaived"):
            verify_unfixed_cves(report, missing, **arguments)
    changed = copy.deepcopy(report)
    changed["Results"][0]["Vulnerabilities"][0]["InstalledVersion"] += "+unreviewed"
    with pytest.raises(ValueError, match="unwaived"):
        verify_unfixed_cves(changed, waivers, **arguments)
    with pytest.raises(ValueError, match="expired"):
        verify_unfixed_cves(report, waivers, image=arguments["image"], today=dt.date(2026, 10, 9))
    with pytest.raises(ValueError, match="unwaived"):
        verify_unfixed_cves(report, waivers, image="drone-ia", today=arguments["today"])
