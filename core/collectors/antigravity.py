"""Antigravity (AGY) loopback quota collector.

Probes local loopback language server processes for Antigravity IDE and CLI.
Never spawns the app or CLI; performs passive non-blocking local inspection.
"""

import os
import re
import subprocess
from datetime import datetime

from core.config import (
    ANTIGRAVITY_SCAN_CACHE,
    _atomic_write_json,
    _load_json,
)
from core.collectors.quotas import (
    _PROVIDER_QUOTA_FALLBACK_TTL,
    _PROVIDER_QUOTA_TTL,
    _cached_provider_quota,
    _provider_credential_marker,
    _provider_integer,
    _provider_json_request,
    _provider_number,
    _provider_quota_enabled,
    _provider_window,
    _save_provider_quota_cache,
)

_ANTIGRAVITY_SCAN_MISS_TTL = 90


def _antigravity_extract_flag(command, flag):
    match = re.search(re.escape(flag) + r"(?:=|\s+)([^\s]+)", command, re.I)
    return match.group(1) if match else None


def _antigravity_process_kind(command):
    lower = command.lower()
    language_server = re.search(
        r"(^|[/\\])language(?:_|-)server(?:[_-][a-z0-9]+)*(?:\.exe)?(?:\s|$)", lower)
    app_match = ("--app_data_dir" in lower and "antigravity" in lower) or any(
        marker in lower for marker in ("antigravity.app/", "/gemini.app/",
                                       "antigravity ide.app/"))
    if language_server and app_match:
        return "ide"
    if re.search(r"(^|[/\\])(antigravity-cli|antigravity_cli|agy)(?:\s|[/\\]|$)", lower):
        return "cli"
    return None


def _antigravity_process_infos(output):
    results = []
    for line in str(output or "").splitlines():
        match = re.match(r"^\s*(\d+)\s+(.+)$", line)
        if not match:
            continue
        pid, command = int(match.group(1)), match.group(2)
        kind = _antigravity_process_kind(command)
        if not kind:
            continue
        csrf = _antigravity_extract_flag(command, "--csrf_token")
        if kind != "cli" and not csrf:
            continue
        extension_port = _provider_integer(
            _antigravity_extract_flag(command, "--extension_server_port"))
        if extension_port is not None and not 0 < extension_port <= 65535:
            extension_port = None
        results.append({
            "pid": pid, "kind": kind, "csrf_token": csrf or "",
            "extension_port": extension_port,
            "extension_csrf_token": _antigravity_extract_flag(
                command, "--extension_server_csrf_token"),
        })
    return results


def _antigravity_scan_recently_empty(now_epoch=None):
    root = _load_json(ANTIGRAVITY_SCAN_CACHE, {})
    empty_at = _provider_number(root.get("empty_at")) if isinstance(root, dict) else None
    if empty_at is None:
        return False
    now = int(now_epoch if now_epoch is not None else datetime.now().timestamp())
    return 0 <= now - int(empty_at) < _ANTIGRAVITY_SCAN_MISS_TTL


def _record_antigravity_scan(found, now_epoch=None):
    if found:
        try:
            os.remove(ANTIGRAVITY_SCAN_CACHE)
        except OSError:
            pass
        return
    try:
        _atomic_write_json(ANTIGRAVITY_SCAN_CACHE, {
            "empty_at": int(now_epoch if now_epoch is not None else datetime.now().timestamp()),
        })
    except OSError:
        pass


def _antigravity_running_processes(now_epoch=None):
    if _antigravity_scan_recently_empty(now_epoch):
        return []
    try:
        result = subprocess.run(
            ["/bin/ps", "-ax", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    processes = _antigravity_process_infos(result.stdout)
    _record_antigravity_scan(bool(processes), now_epoch)
    return processes


def _antigravity_listening_ports(pid):
    lsof = next((path for path in ("/usr/sbin/lsof", "/usr/bin/lsof")
                 if os.path.isfile(path) and os.access(path, os.X_OK)), None)
    if not lsof:
        return []
    try:
        result = subprocess.run(
            [lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
            capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({int(value) for value in re.findall(r":(\d+)\s+\(LISTEN\)", result.stdout)})


def _antigravity_endpoints(process):
    endpoints = []
    extension_port = process.get("extension_port")
    if extension_port:
        for token in (process.get("extension_csrf_token"), process.get("csrf_token")):
            if token is not None:
                endpoints.append(("http", extension_port, token, True))
    for port in _antigravity_listening_ports(process["pid"]):
        endpoints.append(("https", port, process.get("csrf_token") or "",
                          process.get("kind") != "cli"))
    unique = []
    for endpoint in endpoints:
        if endpoint not in unique:
            unique.append(endpoint)
    return unique


def _antigravity_request(endpoint, path, body):
    scheme, port, csrf, requires_csrf = endpoint
    headers = {"Connect-Protocol-Version": "1"}
    if requires_csrf:
        headers["X-Codeium-Csrf-Token"] = csrf
    return _provider_json_request(
        f"{scheme}://127.0.0.1:{port}{path}", headers=headers, method="POST", body=body,
        timeout=2, allow_insecure_loopback_tls=True)


def _antigravity_remaining(value):
    if not isinstance(value, dict):
        return None
    raw = value.get("remainingFraction")
    if raw is None and value.get("case") == "remainingFraction":
        raw = value.get("value")
    number = _provider_number(raw)
    return max(0.0, min(1.0, number)) if number is not None else None


def _normalize_antigravity_quota_summary(payload, updated=None):
    root = payload.get("response") if isinstance(payload, dict) \
        and isinstance(payload.get("response"), dict) else payload
    groups = root.get("groups") if isinstance(root, dict) and isinstance(root.get("groups"), list) else []
    windows = []
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        display = str(group.get("displayName") or f"Group {group_index + 1}")
        lower_group = display.lower()
        if "gemini" in lower_group:
            family, family_order = "Gemini", 0
        elif "claude" in lower_group or "gpt" in lower_group or "third" in lower_group:
            family, family_order = "Claude/GPT", 1
        else:
            family, family_order = display, 2 + group_index
        for bucket_index, bucket in enumerate(group.get("buckets") or []):
            if not isinstance(bucket, dict) or bucket.get("disabled") is True:
                continue
            bucket_id = str(bucket.get("bucketId") or f"bucket-{bucket_index}")
            cadence = (bucket_id + " " + str(bucket.get("displayName") or "")).lower()
            normalized = cadence.replace("_", "-")
            if any(marker in normalized for marker in ("5h", "5-hour", "five hour",
                                                        "five-hour", "session")):
                cadence_title, minutes, cadence_order = "5h", 300, 0
            elif any(marker in normalized for marker in ("weekly", "week", "7d")):
                cadence_title, minutes, cadence_order = "周", 10080, 1
            else:
                cadence_title, minutes, cadence_order = str(
                    bucket.get("displayName") or bucket_id), None, 2
            remaining = _antigravity_remaining(bucket.get("remaining"))
            windows.append((family_order, cadence_order, bucket_index, _provider_window(
                "antigravity-" + bucket_id, f"{family} {cadence_title}",
                (1 - remaining) * 100 if remaining is not None else None,
                bucket.get("resetTime"), minutes, bucket.get("description"),
                usage_known=remaining is not None)))
    windows.sort(key=lambda item: item[:3])
    rows = [item[3] for item in windows]
    return {
        "available": bool(rows),
        "plan": None, "account": None, "windows": rows, "details": [],
        "source": "antigravity-local",
        "updated": int(updated if updated is not None else datetime.now().timestamp()),
        "stale": False,
    }


def _normalize_antigravity_user_status(payload, updated=None):
    if not isinstance(payload, dict):
        return {}
    status = payload.get("userStatus") if isinstance(payload.get("userStatus"), dict) else payload
    config_data = status.get("cascadeModelConfigData") if isinstance(
        status.get("cascadeModelConfigData"), dict) else {}
    configs = config_data.get("clientModelConfigs")
    if not isinstance(configs, list):
        configs = payload.get("clientModelConfigs") if isinstance(
            payload.get("clientModelConfigs"), list) else []
    windows = []
    for index, config in enumerate(configs):
        if not isinstance(config, dict) or not isinstance(config.get("quotaInfo"), dict):
            continue
        info = config["quotaInfo"]
        remaining = _provider_number(info.get("remainingFraction"))
        if remaining is None:
            continue
        label = config.get("label")
        model = config.get("modelOrAlias") if isinstance(config.get("modelOrAlias"), dict) else {}
        model_id = model.get("model") or f"model-{index}"
        windows.append(_provider_window(
            "antigravity-model-" + str(model_id), str(label or model_id),
            (1 - max(0, min(1, remaining))) * 100, info.get("resetTime")))
    windows.sort(key=lambda row: (-(row.get("used_pct") or 0), row["title"]))
    tier = status.get("userTier") if isinstance(status.get("userTier"), dict) else {}
    plan_status = status.get("planStatus") if isinstance(status.get("planStatus"), dict) else {}
    plan_info = plan_status.get("planInfo") if isinstance(plan_status.get("planInfo"), dict) else {}
    plan = next((value.strip() for value in (
        tier.get("name"), plan_info.get("planName"), plan_info.get("planDisplayName"),
        plan_info.get("displayName"), plan_info.get("productName"),
        plan_info.get("planShortName")) if isinstance(value, str) and value.strip()), None)
    account = status.get("email") if isinstance(status.get("email"), str) else None
    return {
        "available": bool(windows), "plan": plan, "account": account,
        "windows": windows[:12], "details": [], "source": "antigravity-local",
        "updated": int(updated if updated is not None else datetime.now().timestamp()),
        "stale": False,
    }


def fetch_antigravity_quota():
    paths = {
        "summary": "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary",
        "status": "/exa.language_server_pb.LanguageServerService/GetUserStatus",
        "models": "/exa.language_server_pb.LanguageServerService/GetCommandModelConfigs",
    }
    metadata = {"metadata": {
        "ideName": "antigravity", "extensionName": "antigravity",
        "ideVersion": "unknown", "locale": "en",
    }}
    last_error = None
    for process in _antigravity_running_processes():
        endpoints = _antigravity_endpoints(process)
        if not endpoints:
            continue
        marker = _provider_credential_marker(
            "antigravity", process.get("pid"), process.get("csrf_token"), endpoints)
        cached = _cached_provider_quota("antigravity", marker, _PROVIDER_QUOTA_TTL)
        if cached:
            return cached
        for endpoint in endpoints:
            try:
                summary_payload = _antigravity_request(
                    endpoint, paths["summary"], {"forceRefresh": True})
                quota = _normalize_antigravity_quota_summary(summary_payload)
                if quota.get("available"):
                    try:
                        identity_payload = _antigravity_request(endpoint, paths["status"], metadata)
                        identity = _normalize_antigravity_user_status(identity_payload)
                        quota["plan"] = identity.get("plan")
                        quota["account"] = identity.get("account")
                    except Exception:
                        pass
                    _save_provider_quota_cache("antigravity", marker, quota)
                    return quota
            except Exception as error:
                last_error = error
        for path, body in ((paths["status"], metadata), (paths["models"], metadata)):
            for endpoint in endpoints:
                try:
                    quota = _normalize_antigravity_user_status(
                        _antigravity_request(endpoint, path, body))
                    if quota.get("available"):
                        _save_provider_quota_cache("antigravity", marker, quota)
                        return quota
                except Exception as error:
                    last_error = error
        fallback = _cached_provider_quota(
            "antigravity", marker, _PROVIDER_QUOTA_FALLBACK_TTL, stale=True)
        if fallback:
            return fallback
    if last_error:
        raise last_error
    return {}


def scan_antigravity_quota():
    return fetch_antigravity_quota() if _provider_quota_enabled("antigravity") else {}


__all__ = [
    "_ANTIGRAVITY_SCAN_MISS_TTL",
    "_antigravity_extract_flag",
    "_antigravity_process_kind",
    "_antigravity_process_infos",
    "_antigravity_scan_recently_empty",
    "_record_antigravity_scan",
    "_antigravity_running_processes",
    "_antigravity_listening_ports",
    "_antigravity_endpoints",
    "_antigravity_request",
    "_antigravity_remaining",
    "_normalize_antigravity_quota_summary",
    "_normalize_antigravity_user_status",
    "fetch_antigravity_quota",
    "scan_antigravity_quota",
]
