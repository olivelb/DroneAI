"""Reject unwaived HIGH/CRITICAL vulnerabilities without an upstream fix."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


def _unfixed_findings(report: Any) -> set[str]:
    if not isinstance(report, dict):
        raise ValueError("Trivy report must be a JSON object")
    findings: set[str] = set()
    results = report.get("Results") or []
    if not isinstance(results, list):
        raise ValueError("Trivy report Results must be an array")
    for result in results:
        vulnerabilities = result.get("Vulnerabilities") or [] if isinstance(result, dict) else []
        if not isinstance(vulnerabilities, list):
            raise ValueError("Trivy Vulnerabilities must be an array")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                continue
            identifier = vulnerability.get("VulnerabilityID")
            severity = vulnerability.get("Severity")
            fixed_version = vulnerability.get("FixedVersion")
            if (
                isinstance(identifier, str)
                and severity in {"HIGH", "CRITICAL"}
                and not fixed_version
            ):
                findings.add(identifier)
    return findings


def verify_unfixed_cves(
    report: Any,
    waiver_document: Any,
    *,
    image: str,
    today: dt.date,
) -> set[str]:
    if not isinstance(waiver_document, dict) or not isinstance(
        waiver_document.get("waivers"), list
    ):
        raise ValueError("Waiver document must contain a waivers array")
    active: set[tuple[str, str]] = set()
    for index, waiver in enumerate(waiver_document["waivers"]):
        if not isinstance(waiver, dict):
            raise ValueError(f"waivers[{index}] must be an object")
        required = {"id", "image", "owner", "reason", "expires"}
        if set(waiver) != required:
            raise ValueError(f"waivers[{index}] must contain exactly {sorted(required)}")
        if not all(isinstance(waiver[key], str) and waiver[key].strip() for key in required):
            raise ValueError(f"waivers[{index}] fields must be non-empty strings")
        if len(waiver["reason"].strip()) < 10:
            raise ValueError(f"waivers[{index}].reason must explain the acceptance")
        try:
            expires = dt.date.fromisoformat(waiver["expires"])
        except ValueError as error:
            raise ValueError(f"waivers[{index}].expires must be YYYY-MM-DD") from error
        if expires < today:
            raise ValueError(f"waiver {waiver['id']} for {waiver['image']} expired on {expires}")
        key = (waiver["id"], waiver["image"])
        if key in active:
            raise ValueError(f"duplicate waiver for {waiver['id']} and {waiver['image']}")
        active.add(key)

    findings = _unfixed_findings(report)
    unwaived = sorted(identifier for identifier in findings if (identifier, image) not in active)
    if unwaived:
        raise ValueError(
            f"{image}: unwaived unfixed HIGH/CRITICAL vulnerabilities: {', '.join(unwaived)}"
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--waivers", type=Path, required=True)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.datetime.now(dt.UTC).date())
    args = parser.parse_args()

    findings = verify_unfixed_cves(
        json.loads(args.report.read_text(encoding="utf-8")),
        json.loads(args.waivers.read_text(encoding="utf-8")),
        image=args.image,
        today=args.today,
    )
    print(f"{args.image}: {len(findings)} unfixed HIGH/CRITICAL finding(s), all waived")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
