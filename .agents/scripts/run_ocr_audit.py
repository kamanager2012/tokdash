#!/usr/bin/env python3
"""Alibaba Open Code Review (OCR) Delegate Audit Harness.

Automates code review delegation, file grouping, rule resolution,
workspace integrity verification, and gate validation under Workflow v2.

Usage:
    python3 .agents/scripts/run_ocr_audit.py [--from <base_commit>] [--to <candidate_commit>] [--task <task_file>] [--json]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, Any, List, Optional, Tuple


def find_ocr_binary() -> Optional[str]:
    """Locate the Alibaba open-code-review binary in common paths or PATH."""
    bin_name = "ocr"
    found = shutil.which(bin_name)
    if found:
        return found
    candidates = [
        os.path.expanduser("~/.local/bin/ocr"),
        os.path.expanduser("~/.npm-global/bin/ocr"),
        "/usr/local/bin/ocr",
        "/usr/bin/ocr",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def get_ocr_version(ocr_bin: str) -> str:
    """Retrieve version banner of the OCR CLI."""
    try:
        res = subprocess.run([ocr_bin, "--version"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return res.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return "unknown"


def parse_ocr_preview(preview_text: str) -> Dict[str, Any]:
    """Parse output of 'ocr delegate preview' into structured data."""
    reviewable_files = []
    excluded_files = []
    total_insertions = 0
    total_deletions = 0

    for line in preview_text.splitlines():
        line = line.strip()
        m_ins = re.match(r"- total_insertions:\s*(\d+)", line)
        if m_ins:
            total_insertions = int(m_ins.group(1))
            continue
        m_del = re.match(r"- total_deletions:\s*(\d+)", line)
        if m_del:
            total_deletions = int(m_del.group(1))
            continue

        # Check excluded file lines: ~~- `path` [status] (excluded: reason)~~
        m_ex = re.match(r"~~-\s*`([^`]+)`\s*\[[^\]]+\]\s*(?:\+\d+/-\d+)?\s*\(excluded:\s*([^)]+)\)~~", line)
        if m_ex:
            excluded_files.append({"path": m_ex.group(1), "reason": m_ex.group(2)})
            continue

        # Check reviewable file lines: - `path` [status] +X/-Y
        m_rev = re.match(r"-\s*`([^`]+)`\s*\[([^\]]+)\](?:\s*\+(\d+)/-(\d+))?", line)
        if m_rev:
            reviewable_files.append({
                "path": m_rev.group(1),
                "status": m_rev.group(2),
                "insertions": int(m_rev.group(3) or 0),
                "deletions": int(m_rev.group(4) or 0),
            })

    return {
        "reviewable_files": reviewable_files,
        "excluded_files": excluded_files,
        "total_insertions": total_insertions,
        "total_deletions": total_deletions,
    }


def run_ocr_preview(ocr_bin: str, repo_path: str, from_ref: str, to_ref: str) -> Tuple[int, str, Dict[str, Any]]:
    """Execute 'ocr delegate preview --from <from> --to <to>' and parse output."""
    cmd = [ocr_bin, "delegate", "preview", "--from", from_ref, "--to", to_ref]
    try:
        res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=15)
        raw_output = res.stdout
        parsed = parse_ocr_preview(raw_output)
        return res.returncode, raw_output, parsed
    except Exception as e:
        return 1, f"Failed to run ocr preview: {e}", {"reviewable_files": [], "excluded_files": []}


def run_ocr_rules(ocr_bin: str, repo_path: str, files: List[str]) -> Tuple[int, str]:
    """Execute 'ocr delegate rule <files>' and return resolved rule groups."""
    if not files:
        return 0, "No reviewable files to check rules for."
    cmd = [ocr_bin, "delegate", "rule", *files]
    try:
        res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=15)
        return res.returncode, res.stdout
    except Exception as e:
        return 1, f"Failed to run ocr rule: {e}"


def check_git_clean(repo_path: str) -> Tuple[bool, List[str]]:
    """Verify if the working tree has uncommitted modifications."""
    try:
        res = subprocess.run(
            ["git", "-C", repo_path, "status", "--porcelain"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
        return len(lines) == 0, lines
    except Exception as e:
        return False, [f"git status error: {e}"]


def run_g1_tests(repo_path: str) -> Dict[str, Any]:
    """Execute the canonical G-1 test suite: python3 -m unittest discover -s tests -v."""
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    try:
        res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=60)
        output = res.stderr or res.stdout
        m_ran = re.search(r"Ran (\d+) tests in ([\d\.]+)s", output)
        test_count = int(m_ran.group(1)) if m_ran else 0
        duration = float(m_ran.group(2)) if m_ran else 0.0
        passed = (res.returncode == 0) and ("OK" in output)
        return {
            "passed": passed,
            "exit_code": res.returncode,
            "test_count": test_count,
            "duration_seconds": duration,
            "output": output,
        }
    except Exception as e:
        return {
            "passed": False,
            "exit_code": 1,
            "test_count": 0,
            "duration_seconds": 0.0,
            "output": f"Test runner execution failed: {e}",
        }


def main():
    parser = argparse.ArgumentParser(description="Alibaba Open Code Review (OCR) Delegate Audit Harness")
    parser.add_argument("--from", dest="from_ref", default="HEAD~1", help="Base commit SHA or ref (default: HEAD~1)")
    parser.add_argument("--to", dest="to_ref", default="HEAD", help="Candidate commit SHA or ref (default: HEAD)")
    parser.add_argument("--task", dest="task_file", help="Path to TASK contract markdown file to validate")
    parser.add_argument("--repo", dest="repo_path", default=".", help="Repository root path (default: .)")
    parser.add_argument("--json", action="store_true", help="Output audit report in structured JSON")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running G-1 unittest suite")
    parser.add_argument("--strict", action="store_true", help="Enforce strict checks (fail on warnings)")

    args = parser.parse_args()
    repo_path = os.path.abspath(args.repo_path)

    report: Dict[str, Any] = {
        "status": "RUNNING",
        "harness": "alibaba/open-code-review delegate harness",
        "repo": repo_path,
        "from_ref": args.from_ref,
        "to_ref": args.to_ref,
    }

    # 1. OCR Binary Detection
    ocr_bin = find_ocr_binary()
    if not ocr_bin:
        report["ocr_available"] = False
        report["status"] = "BLOCKED"
        report["error"] = "Alibaba open-code-review ('ocr') CLI not found in PATH or ~/.local/bin/ocr"
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"❌ {report['error']}")
        sys.exit(1 if args.strict else 0)

    report["ocr_available"] = True
    report["ocr_binary"] = ocr_bin
    report["ocr_version"] = get_ocr_version(ocr_bin)

    # 2. Preview Scope Analysis
    preview_code, preview_raw, preview_parsed = run_ocr_preview(
        ocr_bin, repo_path, args.from_ref, args.to_ref
    )
    report["preview"] = preview_parsed

    # 3. Rule Resolution
    reviewable_paths = [f["path"] for f in preview_parsed.get("reviewable_files", [])]
    rule_code, rule_text = run_ocr_rules(ocr_bin, repo_path, reviewable_paths)
    report["rule_resolution"] = {
        "files_checked": reviewable_paths,
        "raw_rules": rule_text,
    }

    # 4. Working Tree Integrity
    is_clean, dirty_files = check_git_clean(repo_path)
    report["working_tree_clean"] = is_clean
    report["dirty_files"] = dirty_files

    # 5. G-1 Test Suite
    if not args.skip_tests:
        test_result = run_g1_tests(repo_path)
        report["g1_tests"] = test_result
        if not test_result["passed"]:
            report["status"] = "FAILED"
            report["failure_reason"] = "G-1 unit test suite failed"
    else:
        report["g1_tests"] = {"skipped": True}

    # 6. Task Contract Validation (if provided)
    if args.task_file:
        task_script = os.path.join(repo_path, ".agents/scripts/validate_task.py")
        if os.path.isfile(task_script):
            res = subprocess.run(
                [sys.executable, task_script, args.task_file, "--repo", repo_path],
                capture_output=True, text=True
            )
            report["task_contract"] = {
                "file": args.task_file,
                "passed": res.returncode == 0,
                "output": (res.stdout + res.stderr).strip(),
            }
            if res.returncode != 0:
                report["status"] = "FAILED"
                report["failure_reason"] = f"Task contract validation failed for {args.task_file}"
        else:
            report["task_contract"] = {"file": args.task_file, "error": "validate_task.py not found"}

    if report.get("status") not in ("FAILED", "BLOCKED"):
        report["status"] = "PASS"

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        status_icon = "✅" if report["status"] == "PASS" else "❌"
        print(f"\n{status_icon} [OCR Delegate Audit] Overall Status: {report['status']}")
        print(f"• Tool: {report['ocr_version']} ({ocr_bin})")
        print(f"• Scope: {args.from_ref}..{args.to_ref}")
        print(f"• Reviewable Files: {len(reviewable_paths)} | Excluded Files: {len(preview_parsed.get('excluded_files', []))}")
        print(f"• Changes: +{preview_parsed.get('total_insertions', 0)} / -{preview_parsed.get('total_deletions', 0)} lines")
        if reviewable_paths:
            print("• Reviewable targets:")
            for rf in preview_parsed["reviewable_files"]:
                print(f"  - `{rf['path']}` [{rf['status']}] +{rf['insertions']}/-{rf['deletions']}")
        if not args.skip_tests and "g1_tests" in report:
            g1 = report["g1_tests"]
            t_icon = "✅" if g1.get("passed") else "❌"
            print(f"• G-1 Tests: {t_icon} {g1.get('test_count', 0)} tests in {g1.get('duration_seconds', 0):.2f}s")
        if args.task_file and "task_contract" in report:
            tc = report["task_contract"]
            tc_icon = "✅" if tc.get("passed") else "❌"
            print(f"• Task Contract: {tc_icon} {args.task_file}")
        print()

    sys.exit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
