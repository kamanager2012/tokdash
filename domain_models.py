"""Canonical Resource Read Model for Cognitally.

Frozen, immutable domain representations for usage snapshots, agent telemetry,
pricing provenance, and doctor diagnostics.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime


@dataclass(frozen=True)
class ModelUsageEntity:
    name: str
    cost: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    pricing_provenance: str = "unknown"
    pricing_source: str = ""
    cost_kind: str = "standard"


@dataclass(frozen=True)
class RangeUsageEntity:
    hit_rate: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost: float
    sessions_count: int
    models: List[ModelUsageEntity] = field(default_factory=list)


@dataclass(frozen=True)
class AgentUsageEntity:
    agent_id: str
    ranges: Dict[str, RangeUsageEntity]
    limits: Optional[Dict[str, Any]] = None
    plan: Optional[str] = None


@dataclass(frozen=True)
class DailyCostEntity:
    date: str
    total: float
    tokens: int
    tool_costs: Dict[str, float]


@dataclass(frozen=True)
class ProjectAttributionEntity:
    path: str
    cost: float
    tokens: int


@dataclass(frozen=True)
class CanonicalSnapshotEntity:
    snapshot_id: str
    generation: str
    generated_at: str
    agents: Dict[str, AgentUsageEntity]
    daily_costs: List[DailyCostEntity]
    projects: List[ProjectAttributionEntity]
    pricing_metadata: Dict[str, Any]

    @property
    def is_valid(self) -> bool:
        return bool(self.snapshot_id and len(self.generation) == 16)
