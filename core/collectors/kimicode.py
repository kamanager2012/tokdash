import os
import glob
import json
import math
import hashlib
from datetime import datetime, date

from core.config import (
    KIMI_CODE_DIR,
    _KIMI_CODE_DEFAULT_DIR,
    _KIMI_CODE_LEGACY_DIR,
    _empty_token_day,
    _empty_token_ranges,
    _load_json,
    classify_date,
    parse_ts,
)
from core.storage import (
    _add_token_usage,
    _merge_live_token_day,
    _merge_token_day,
    ledger_reconcile,
    ledger_touch,
)

_KIMI_PARSER_VERSION = 3


def _kimi_roots():
    configured = (os.environ.get("TOKEI_KIMI_DIR") or os.environ.get("KIMI_CODE_HOME")
                  or os.environ.get("KIMI_SHARE_DIR"))
    if configured:
        candidates = [configured]
    elif os.path.normcase(KIMI_CODE_DIR) != os.path.normcase(_KIMI_CODE_DEFAULT_DIR):
        # Tests and embedders may replace KIMI_CODE_DIR after importing this module.
        candidates = [KIMI_CODE_DIR]
    else:
        candidates = [_KIMI_CODE_DEFAULT_DIR, _KIMI_CODE_LEGACY_DIR]
    roots = []
    seen = set()
    for candidate in candidates:
        root = os.path.abspath(os.path.expanduser(candidate))
        key = os.path.normcase(os.path.realpath(root))
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return roots


def _kimi_wire_groups():
    groups = []
    for root in _kimi_roots():
        sessions_dir = os.path.join(root, "sessions")
        for session_dir in glob.glob(os.path.join(sessions_dir, "*", "*")):
            if not os.path.isdir(session_dir):
                continue
            agent_wires = sorted(os.path.abspath(path) for path in glob.glob(
                os.path.join(session_dir, "agents", "*", "wire.jsonl")))
            root_wire = os.path.join(session_dir, "wire.jsonl")
            root_wire = os.path.abspath(root_wire) if os.path.isfile(root_wire) else None
            if agent_wires or root_wire:
                groups.append((agent_wires, root_wire))
    return groups


def _kimi_wire_files(groups=None):
    files = set()
    for agent_wires, root_wire in _kimi_wire_groups() if groups is None else groups:
        files.update(agent_wires)
        if root_wire:
            files.add(root_wire)
    return sorted(files)


def _kimi_mirror_sources(groups):
    return {root_wire: tuple(agent_wires)
            for agent_wires, root_wire in groups
            if root_wire and agent_wires}


def _kimi_group_signature(paths):
    parts = []
    for path in paths:
        try:
            stat = os.stat(path)
        except OSError:
            parts.append(f"{path}:-")
            continue
        parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _kimi_record_counts(paths):
    counts = {}
    for path in paths:
        _scan_kimi_wire(path, seen=counts)
    return counts


def _kimi_project_map():
    result = {}
    for root in _kimi_roots():
        metadata = _load_json(os.path.join(root, "kimi.json"), {})
        for item in metadata.get("work_dirs", []) if isinstance(metadata, dict) else []:
            if not isinstance(item, dict):
                continue
            project = item.get("path")
            if not isinstance(project, str) or not project:
                continue
            digest = hashlib.md5(project.encode("utf-8")).hexdigest()
            result[digest] = project
            kaos = item.get("kaos")
            if isinstance(kaos, str) and kaos:
                result[f"{kaos}_{digest}"] = project
    return result


def _kimi_wire_context(path, legacy_projects):
    agent_dir = os.path.dirname(path)
    agents_dir = os.path.dirname(agent_dir)
    if os.path.basename(agents_dir) == "agents":
        session_dir = os.path.dirname(agents_dir)
        state = _load_json(os.path.join(session_dir, "state.json"), {})
        if not isinstance(state, dict):
            state = {}
        session_id = state.get("id") or os.path.basename(session_dir)
        project = state.get("cwd")
        return {
            "sid": str(session_id),
            "proj": project if isinstance(project, str) and project else None,
            "agent": os.path.basename(agent_dir),
        }
    session_dir = agent_dir
    work_dir_hash = os.path.basename(os.path.dirname(session_dir))
    return {
        "sid": os.path.basename(session_dir),
        "proj": legacy_projects.get(work_dir_hash),
        "agent": "main",
    }


def _kimi_events(message, scope="main"):
    if not isinstance(message, dict):
        return
    msg_type = message.get("type")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return
    if msg_type == "SubagentEvent":
        agent = payload.get("agent_id") or payload.get("parent_tool_call_id") \
            or payload.get("task_tool_call_id")
        child_scope = f"{scope}/{agent}" if isinstance(agent, str) and agent else scope
        yield from _kimi_events(payload.get("event"), child_scope)
    elif msg_type == "StatusUpdate":
        yield scope, payload


def _kimi_token(value):
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _kimi_datetime(record, key):
    value = record.get(key) if isinstance(record, dict) else None
    try:
        epoch = float(value)
        if not math.isfinite(epoch):
            return None
        if epoch > 100_000_000_000:
            epoch /= 1000
        return datetime.fromtimestamp(epoch).astimezone()
    except (TypeError, ValueError, OverflowError, OSError):
        parsed = parse_ts(value) if isinstance(value, str) else None
        return parsed.astimezone() if parsed is not None else None


def _scan_kimi_wire(path, exclude=None, seen=None):
    days = {}
    seen_messages = set()
    track = exclude is not None or seen is not None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if ('"usage.record"' not in line and '"StatusUpdate"' not in line
                        and '"SubagentEvent"' not in line):
                    continue
                try:
                    record = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") == "usage.record":
                    usage = record.get("usage")
                    dt = _kimi_datetime(record, "time")
                    if not isinstance(usage, dict) or dt is None:
                        continue
                    inp = _kimi_token(usage.get("inputOther"))
                    out = _kimi_token(usage.get("output"))
                    cr = _kimi_token(usage.get("inputCacheRead"))
                    cw = _kimi_token(usage.get("inputCacheCreation"))
                    if inp + out + cr + cw == 0:
                        continue
                    model = record.get("model")
                    if not isinstance(model, str) or not model.strip():
                        model = None
                    if track:
                        key = ("u", round(dt.timestamp() * 1000), model, inp, out, cr, cw)
                        if exclude:
                            left = exclude.get(key, 0)
                            if left > 0:
                                exclude[key] = left - 1
                                continue
                        if seen is not None:
                            seen[key] = seen.get(key, 0) + 1
                    day = days.setdefault(dt.date().isoformat(), _empty_token_day())
                    _add_token_usage(day, inp, out, cr, cw, model=model)
                    day["hours"][dt.hour] += inp + out + cr + cw
                    continue

                dt = _kimi_datetime(record, "timestamp")
                if dt is None:
                    continue
                message = record.get("message")
                for scope, payload in _kimi_events(message):
                    usage = payload.get("token_usage")
                    if not isinstance(usage, dict):
                        continue
                    message_id = payload.get("message_id")
                    if isinstance(message_id, str) and message_id:
                        dedup_key = f"{scope}:{message_id}"
                        if dedup_key in seen_messages:
                            continue
                        seen_messages.add(dedup_key)
                    else:
                        message_id = None
                    inp = _kimi_token(usage.get("input_other"))
                    out = _kimi_token(usage.get("output"))
                    cr = _kimi_token(usage.get("input_cache_read"))
                    cw = _kimi_token(usage.get("input_cache_creation"))
                    if inp + out + cr + cw == 0:
                        continue
                    if track:
                        key = (("m", scope, message_id) if message_id else
                               ("t", round(dt.timestamp() * 1000), scope, inp, out, cr, cw))
                        if exclude:
                            left = exclude.get(key, 0)
                            if left > 0:
                                exclude[key] = left - 1
                                continue
                        if seen is not None:
                            seen[key] = seen.get(key, 0) + 1
                    day = days.setdefault(dt.date().isoformat(), _empty_token_day())
                    _add_token_usage(day, inp, out, cr, cw)
                    day["hours"][dt.hour] += inp + out + cr + cw
    except OSError:
        return {}
    return days


def scan_kimicode(bounds, cache):
    ledger_touch("kimicode")
    fc = cache.setdefault("kimicode", {})
    B = _empty_token_ranges()
    groups = _kimi_wire_groups()
    files = _kimi_wire_files(groups)
    if not files:
        if fc:
            fc.clear()
            cache["_dirty"] = True

    projects = _kimi_project_map()
    mirrors = _kimi_mirror_sources(groups)
    stale = set(fc)
    changed = False
    for path in files:
        stale.discard(path)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        signature = f"{stat.st_mtime_ns}:{stat.st_size}"
        mirror_of = mirrors.get(path)
        if mirror_of:
            signature = f"{signature}:{_kimi_group_signature(mirror_of)}"
        entry = fc.get(path)
        context = _kimi_wire_context(path, projects)
        if (not isinstance(entry, dict) or entry.get("sig") != signature
                or entry.get("parser_version") != _KIMI_PARSER_VERSION):
            fc[path] = {
                "sig": signature,
                "days": _scan_kimi_wire(
                    path, exclude=_kimi_record_counts(mirror_of) if mirror_of else None),
                "sid": context["sid"],
                "proj": context["proj"],
                "agent": context["agent"],
                "parser_version": _KIMI_PARSER_VERSION,
            }
            changed = True
        elif any(entry.get(key) != context[key] for key in ("sid", "proj", "agent")):
            entry.update(context)
            changed = True

    for path in stale:
        fc.pop(path, None)
        changed = True

    live_days = {}
    live_sessions = {}
    live_projects = {}
    for path, entry in fc.items():
        if not isinstance(entry, dict):
            continue
        for day_key, day in entry.get("days", {}).items():
            try:
                date.fromisoformat(day_key)
            except (TypeError, ValueError):
                continue
            _merge_live_token_day(live_days.setdefault(day_key, _empty_token_day()), day)
            session = entry.get("sid") or path
            live_sessions.setdefault(day_key, set()).add(session)
            project = entry.get("proj")
            if isinstance(project, str) and project:
                live_projects.setdefault(day_key, set()).add(project)

    for day_key, day in live_days.items():
        day["sessions"] = sorted(live_sessions.get(day_key, set()))
        day["projects"] = sorted(live_projects.get(day_key, set()))

    for day_key, day in ledger_reconcile("kimicode", live_days).items():
        try:
            local_day = date.fromisoformat(day_key)
        except (TypeError, ValueError):
            continue
        for range_key in classify_date(local_day, bounds):
            _merge_token_day(B[range_key], day)
            B[range_key]["sessions"].update(day.get("sessions", []))
    if changed:
        cache["_dirty"] = True
    return {"ranges": B}
