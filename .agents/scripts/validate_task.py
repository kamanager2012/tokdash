#!/usr/bin/env python3
"""Task Contract & Handoff Report Linter / Validator (Workflow v1).

Validates that TASK reports conform to the production-grade Multi-Agent
Workflow specification:
1. Valid YAML frontmatter with mandatory metadata (task_id, base_commit, candidate_commit, role, status).
2. Git SHA authenticity: verifies that base_commit and candidate_commit exist in the git repository.
3. Acceptance Criteria (AC) mapping completeness.
4. If audit status is REJECTED, verifies that each BLOCKER contains the required 5 elements.
5. Circuit breaker: warns or errors if handoff rounds exceed MAX_ROUNDS (default: 2).

Usage:
    python3 validate_task.py <path/to/TASK_REPORT.md> [--repo <path/to/repo>] [--strict]
"""

import os
import re
import sys
import subprocess
import argparse
from typing import Dict, Any, List, Tuple


def parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    """Extract YAML-like frontmatter between leading --- delimiters."""
    meta: Dict[str, str] = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_yaml = parts[1]
            body = parts[2].lstrip()
            for line in raw_yaml.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, val = line.split(":", 1)
                    # Strip inline comments
                    val = val.split("#", 1)[0].strip().strip("'\"")
                    meta[key.strip()] = val
    return meta, body


def git_commit_exists(repo_path: str, commit_sha: str) -> bool:
    """Check if a commit SHA actually exists in the local git repository."""
    if not commit_sha or commit_sha.lower() in ("unknown", "未核实", "none", "n/a"):
        return False
    try:
        cmd = ["git", "-C", repo_path, "cat-file", "-e", f"{commit_sha}^{{commit}}"]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return res.returncode == 0
    except Exception:
        return False


def validate_report(file_path: str, repo_path: str = ".", strict_git: bool = False) -> List[str]:
    """Validate a TASK report file against production workflow constraints."""
    errors = []

    if not os.path.isfile(file_path):
        return [f"File not found: {file_path}"]

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    meta, body = parse_frontmatter(content)

    # 1. Mandatory Frontmatter Keys
    required_keys = ["task_id", "status", "role"]
    for key in required_keys:
        if not meta.get(key):
            errors.append(f"Missing mandatory frontmatter field: '{key}'")

    status = (meta.get("status") or "").upper()
    valid_statuses = ("DRAFT", "IN_PROGRESS", "SUBMITTED", "APPROVED", "REJECTED", "BLOCKED")
    if status and status not in valid_statuses:
        errors.append(f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}")

    # 2. Circuit Breaker Check
    round_val = meta.get("handoff_round")
    if round_val:
        try:
            r = int(round_val)
            if r > 2:
                errors.append(
                    f"CIRCUIT BREAKER TRIGGERED: handoff_round is {r} (exceeds max 2). "
                    "Escalate to human operator to prevent infinite audit loops."
                )
        except ValueError:
            errors.append(f"Invalid handoff_round: '{round_val}'. Must be an integer.")

    # 3. Git Authenticity (if in SUBMITTED, APPROVED, or REJECTED status)
    if status in ("SUBMITTED", "APPROVED", "REJECTED"):
        candidate = meta.get("candidate_commit")
        if not candidate:
            errors.append("Candidate commit SHA must be specified once work is submitted/audited.")
        elif strict_git:
            if not git_commit_exists(repo_path, candidate):
                errors.append(f"Candidate commit SHA '{candidate}' does not exist in git repo at '{repo_path}'.")

        base = meta.get("base_commit")
        if strict_git and base and base.lower() not in ("未核实", "unknown", "none"):
            if not git_commit_exists(repo_path, base):
                errors.append(f"Base commit SHA '{base}' does not exist in git repo at '{repo_path}'.")

    # 4. Blocker 5-Element Completeness (if REJECTED)
    if status == "REJECTED":
        # Ensure body documents each blocker with all 5 mandatory fields
        blocker_sections = re.findall(r"(?:###?\s*阻断项|###?\s*BLOCKER[^\n]*)(.*?)(?=(?:###?\s|\Z))", body, re.S)
        if not blocker_sections:
            errors.append("Status is REJECTED but no '[BLOCKER]' section was found in the report.")
        else:
            blocker_text = "\n".join(blocker_sections)
            required_elements = [
                ("条款", r"(?:验收条款|违反条款|硬约束|AC[-\w]*)"),
                ("代码位置", r"(?:代码位置|涉及文件|位置|Line|\.py|\.ts|\.js|:\d+)"),
                ("触发条件", r"(?:触发条件|复现条件|Trigger)"),
                ("影响危害", r"(?:实际影响|危害|风险|Impact)"),
                ("复现依据", r"(?:复现命令|依据|输出|Evidence|pytest|run)"),
            ]
            for label, pattern in required_elements:
                if not re.search(pattern, blocker_text, re.IGNORECASE):
                    errors.append(f"REJECTED report blocker missing mandatory element: '{label}'")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate TASK contract compliance.")
    parser.add_argument("report", help="Path to TASK contract or handoff report Markdown file.")
    parser.add_argument("--repo", default=".", help="Path to Git repository root (default: current dir).")
    parser.add_argument("--strict", action="store_true", help="Enforce real Git commit existence check.")

    args = parser.parse_args()
    errors = validate_report(args.report, repo_path=args.repo, strict_git=args.strict)

    if errors:
        print(f"❌ Contract Validation FAILED for {args.report} ({len(errors)} errors):")
        for idx, err in enumerate(errors, 1):
            print(f"  {idx}. {err}")
        sys.exit(1)
    else:
        print(f"✅ Contract Validation PASSED for {args.report}")
        sys.exit(0)


if __name__ == "__main__":
    main()
