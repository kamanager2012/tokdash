"""Standard Read-Only Stdio Model Context Protocol (MCP) Server for Cognitally.

Exposes read-only agent token usage, cost accounting, subscription quotas, and doctor
diagnostics to MCP-compatible AI clients (Claude Code, Cursor, Codex, OpenCode, etc.).

Adheres strictly to the 2024-11-05 MCP Specification over stdio JSON-RPC 2.0.
Guarantees zero-mutation, zero-hallucinated recommendations, and single-flight snapshot reuse.
"""

import sys
import os
import json
from typing import Dict, Any, Optional

# Dynamically import canonical snapshot engine from usage script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
USAGE_SCRIPT = os.path.join(SCRIPT_DIR, "usage.30s.py")

import importlib.util
spec = importlib.util.spec_from_file_location("cognitally_core", USAGE_SCRIPT)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

TOOLS_METADATA = [
    {
        "name": "get_usage",
        "description": "Read-only snapshot of current agent token consumption (in, out, cache read/write, reasoning) and session counts across all 14 supported AI coding agents for a specified period.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["today", "yesterday", "week", "month", "all"],
                    "description": "Time window for token usage",
                    "default": "today"
                },
                "agent": {
                    "type": "string",
                    "description": "Optional specific agent filter (e.g. 'claude', 'codex', 'cursor', 'grok', 'antigravity')"
                }
            }
        }
    },
    {
        "name": "get_cost",
        "description": "Read-only financial cost breakdown across AI coding agents and historical daily spending trends with strict pricing provenance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["today", "yesterday", "week", "month", "all"],
                    "description": "Time window for cost aggregation",
                    "default": "today"
                }
            }
        }
    },
    {
        "name": "get_quota",
        "description": "Read-only subscription quota status, rate limit windows, and reset schedules for active AI providers (Codex, Claude, Grok, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Optional provider filter (e.g. 'codex', 'claude', 'grok')"
                }
            }
        }
    },
    {
        "name": "get_projects",
        "description": "Read-only workspace directory token and cost attribution breakdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max number of projects to return (sorted by cost)",
                    "default": 20
                }
            }
        }
    },
    {
        "name": "get_models",
        "description": "Read-only breakdown of token usage and spending grouped by individual LLM model, including exact pricing provenance and cost classification.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["today", "yesterday", "week", "month", "all"],
                    "description": "Time window for model breakdown",
                    "default": "today"
                }
            }
        }
    },
    {
        "name": "get_accounting_status",
        "description": "Read-only system diagnostics: health status of all 14 local coding agent log collectors, pricing catalog version, and ledger integrity.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]


def _handle_get_usage(arguments: Dict[str, Any]) -> Dict[str, Any]:
    period = arguments.get("period", "today")
    agent_filter = arguments.get("agent")
    
    snapshot = core.get_canonical_snapshot()
    usage = snapshot.get("usage", {})
    
    result = {}
    for tool_key, tool_data in usage.items():
        if tool_key.startswith("_"):
            continue
        if agent_filter and tool_key.lower() != agent_filter.lower():
            continue
        ranges = tool_data.get("ranges", {})
        r = ranges.get(period, {})
        result[tool_key] = {
            "period": period,
            "in": r.get("in", 0),
            "out": r.get("out", 0),
            "cr": r.get("cr", 0),
            "cw": r.get("cw", 0),
            "reason": r.get("reason", 0),
            "cost": r.get("cost", 0.0),
            "sessions": r.get("sessions", 0),
            "hit_rate": r.get("hit", 0.0),
        }
        
    return {
        "generation": snapshot.get("generation"),
        "generated_at": snapshot.get("generated_at"),
        "period": period,
        "agents": result
    }


def _handle_get_cost(arguments: Dict[str, Any]) -> Dict[str, Any]:
    period = arguments.get("period", "today")
    snapshot = core.get_canonical_snapshot()
    daily_costs = snapshot.get("daily_costs", {})
    usage = snapshot.get("usage", {})
    
    agent_costs = {}
    total_cost = 0.0
    for tool_key, tool_data in usage.items():
        if tool_key.startswith("_"):
            continue
        r = tool_data.get("ranges", {}).get(period, {})
        cost = r.get("cost", 0.0)
        agent_costs[tool_key] = round(cost, 4)
        total_cost += cost
        
    return {
        "generation": snapshot.get("generation"),
        "period": period,
        "total_cost_usd": round(total_cost, 4),
        "agent_costs": agent_costs,
        "daily_trends": daily_costs.get("daily", [])[-7:] if isinstance(daily_costs, dict) else []
    }


def _handle_get_quota(arguments: Dict[str, Any]) -> Dict[str, Any]:
    provider_filter = arguments.get("provider")
    snapshot = core.get_canonical_snapshot()
    usage = snapshot.get("usage", {})
    
    quotas = {}
    for tool_key, tool_data in usage.items():
        if tool_key.startswith("_"):
            continue
        if provider_filter and tool_key.lower() != provider_filter.lower():
            continue
        limits = tool_data.get("limits")
        plan = tool_data.get("plan")
        if limits or plan:
            quotas[tool_key] = {
                "limits": limits,
                "plan": plan,
                "limits_updated": tool_data.get("limits_updated"),
                "limits_consumed": tool_data.get("limits_consumed")
            }
            
    return {
        "generation": snapshot.get("generation"),
        "quotas": quotas
    }


def _handle_get_projects(arguments: Dict[str, Any]) -> Dict[str, Any]:
    limit = int(arguments.get("limit", 20))
    snapshot = core.get_canonical_snapshot()
    projects = snapshot.get("projects", [])
    
    sorted_projects = sorted(projects, key=lambda p: p.get("cost", 0.0), reverse=True)[:limit]
    return {
        "generation": snapshot.get("generation"),
        "total_projects": len(projects),
        "projects": sorted_projects
    }


def _handle_get_models(arguments: Dict[str, Any]) -> Dict[str, Any]:
    period = arguments.get("period", "today")
    snapshot = core.get_canonical_snapshot()
    usage = snapshot.get("usage", {})
    
    models_map = {}
    for tool_key, tool_data in usage.items():
        if tool_key.startswith("_"):
            continue
        r = tool_data.get("ranges", {}).get(period, {})
        for m in r.get("models", []):
            name = m.get("name") or m.get("model_id", "unknown")
            key = f"{name} ({tool_key})"
            if key not in models_map:
                models_map[key] = {
                    "model": name,
                    "agent": tool_key,
                    "cost": m.get("cost", 0.0),
                    "in": m.get("in", 0),
                    "out": m.get("out", 0),
                    "cr": m.get("cr", 0),
                    "cw": m.get("cw", 0),
                    "reason": m.get("reason", 0),
                    "pricing_provenance": m.get("pricing_provenance", "unknown"),
                    "pricing_source": m.get("pricing_source", ""),
                    "cost_kind": m.get("cost_kind", "standard")
                }
            else:
                models_map[key]["cost"] += m.get("cost", 0.0)
                models_map[key]["in"] += m.get("in", 0)
                models_map[key]["out"] += m.get("out", 0)
                models_map[key]["cr"] += m.get("cr", 0)
                models_map[key]["cw"] += m.get("cw", 0)
                models_map[key]["reason"] += m.get("reason", 0)
                
    sorted_models = sorted(models_map.values(), key=lambda x: x["cost"], reverse=True)
    return {
        "generation": snapshot.get("generation"),
        "period": period,
        "models": sorted_models
    }


def _handle_get_accounting_status(_arguments: Dict[str, Any]) -> Dict[str, Any]:
    report = core.doctor(return_dict=True)
    snapshot = core.get_canonical_snapshot()
    pricing_meta = snapshot.get("usage", {}).get("_pricing", {})
    
    return {
        "generation": snapshot.get("generation"),
        "pricing_metadata": pricing_meta,
        "doctor_report": report
    }


TOOL_HANDLERS = {
    "get_usage": _handle_get_usage,
    "get_cost": _handle_get_cost,
    "get_quota": _handle_get_quota,
    "get_projects": _handle_get_projects,
    "get_models": _handle_get_models,
    "get_accounting_status": _handle_get_accounting_status,
}


def process_jsonrpc_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process a single JSON-RPC 2.0 request or notification according to MCP spec."""
    msg_id = msg.get("id")
    method = msg.get("method")
    
    # Notifications (no id)
    if msg_id is None:
        if method == "notifications/initialized":
            return None
        return None

    # Requests
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "cognitally",
                    "version": "1.0.0"
                }
            }
        }
        
    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {}
        }
        
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": TOOLS_METADATA
            }
        }
        
    if method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: Unknown tool '{tool_name}'"}],
                    "isError": True
                }
            }
            
        try:
            data = handler(arguments)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
                    "isError": False
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Execution error in {tool_name}: {str(e)}"}],
                    "isError": True
                }
            }

    # Unknown method
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}"
        }
    }


def serve_stdio():
    """Run the MCP server reading line-delimited JSON-RPC from stdin and writing to stdout."""
    # Ensure stdout does not buffer
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            sys.stderr.write(f"Invalid JSON input: {e}\n")
            parse_err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": f"Parse error: {str(e)}"
                }
            }
            sys.stdout.write(json.dumps(parse_err, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
            
        resp = process_jsonrpc_message(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve_stdio()
