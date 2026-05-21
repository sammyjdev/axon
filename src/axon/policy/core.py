from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from axon.config.runtime import RuntimeConfig, is_corporate_context, load_runtime_config
from axon.observability.compliance import ComplianceEvent, emit_compliance_event
from axon.observability.trace_store import TracePayload, TraceStore


class SensitivityLevel(str, Enum):
    PUBLIC = "PUBLIC"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class RouteType(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"


class ReasonCode(str, Enum):
    ALLOW_PUBLIC = "ALLOW_PUBLIC"
    ALLOW_LOCAL = "ALLOW_LOCAL"
    DENY_CORPORATE_CLOUD = "DENY_CORPORATE_CLOUD"
    DENY_FORCE_CLOUD = "DENY_FORCE_CLOUD"
    DENY_RESTRICTED_CLOUD = "DENY_RESTRICTED_CLOUD"
    DENY_CONFIDENTIAL_CLOUD = "DENY_CONFIDENTIAL_CLOUD"
    DENY_BUDGET_PRE_SEND = "DENY_BUDGET_PRE_SEND"
    DENY_BREAKER_OPEN = "DENY_BREAKER_OPEN"


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: str
    allowed: bool
    reason_code: ReasonCode
    policy_version: str
    route: RouteType
    model: str
    ctx: str | None
    sensitivity: SensitivityLevel
    metadata: dict[str, str] = field(default_factory=dict)


class PolicyRegistry:
    def __init__(self, runtime: RuntimeConfig | None = None) -> None:
        self._runtime = runtime or load_runtime_config()

    @property
    def policy_version(self) -> str:
        return self._runtime.policy_version

    def decide(
        self,
        *,
        ctx: str | None,
        model: str,
        caller: str | None = None,
        force_cloud: bool = False,
        sensitivity: SensitivityLevel | None = None,
        trace_store: TraceStore | None = None,
        trace_id: str | None = None,
        trace_payload: TracePayload | None = None,
    ) -> PolicyDecision:
        route = RouteType.LOCAL if model.startswith("ollama/") else RouteType.CLOUD
        effective_sensitivity = sensitivity or self._sensitivity_from_ctx(ctx)
        decision_id = str(uuid.uuid4())

        if route is RouteType.LOCAL:
            decision = PolicyDecision(
                decision_id=decision_id,
                allowed=True,
                reason_code=ReasonCode.ALLOW_LOCAL,
                policy_version=self.policy_version,
                route=route,
                model=model,
                ctx=ctx,
                sensitivity=effective_sensitivity,
            )
            self._emit(
                decision,
                caller,
                trace_store=trace_store,
                trace_id=trace_id,
                trace_payload=trace_payload,
            )
            return decision

        if force_cloud and is_corporate_context(ctx):
            decision = PolicyDecision(
                decision_id=decision_id,
                allowed=False,
                reason_code=ReasonCode.DENY_FORCE_CLOUD,
                policy_version=self.policy_version,
                route=route,
                model=model,
                ctx=ctx,
                sensitivity=effective_sensitivity,
            )
            self._emit(
                decision,
                caller,
                trace_store=trace_store,
                trace_id=trace_id,
                trace_payload=trace_payload,
            )
            return decision

        if is_corporate_context(ctx):
            decision = PolicyDecision(
                decision_id=decision_id,
                allowed=False,
                reason_code=ReasonCode.DENY_CORPORATE_CLOUD,
                policy_version=self.policy_version,
                route=route,
                model=model,
                ctx=ctx,
                sensitivity=effective_sensitivity,
            )
            self._emit(
                decision,
                caller,
                trace_store=trace_store,
                trace_id=trace_id,
                trace_payload=trace_payload,
            )
            return decision

        if effective_sensitivity is SensitivityLevel.RESTRICTED:
            decision = PolicyDecision(
                decision_id=decision_id,
                allowed=False,
                reason_code=ReasonCode.DENY_RESTRICTED_CLOUD,
                policy_version=self.policy_version,
                route=route,
                model=model,
                ctx=ctx,
                sensitivity=effective_sensitivity,
            )
            self._emit(
                decision,
                caller,
                trace_store=trace_store,
                trace_id=trace_id,
                trace_payload=trace_payload,
            )
            return decision

        if effective_sensitivity is SensitivityLevel.CONFIDENTIAL:
            decision = PolicyDecision(
                decision_id=decision_id,
                allowed=False,
                reason_code=ReasonCode.DENY_CONFIDENTIAL_CLOUD,
                policy_version=self.policy_version,
                route=route,
                model=model,
                ctx=ctx,
                sensitivity=effective_sensitivity,
            )
            self._emit(
                decision,
                caller,
                trace_store=trace_store,
                trace_id=trace_id,
                trace_payload=trace_payload,
            )
            return decision

        decision = PolicyDecision(
            decision_id=decision_id,
            allowed=True,
            reason_code=ReasonCode.ALLOW_PUBLIC,
            policy_version=self.policy_version,
            route=route,
            model=model,
            ctx=ctx,
            sensitivity=effective_sensitivity,
        )
        self._emit(
            decision,
            caller,
            trace_store=trace_store,
            trace_id=trace_id,
            trace_payload=trace_payload,
        )
        return decision

    def _emit(
        self,
        decision: PolicyDecision,
        caller: str | None,
        *,
        trace_store: TraceStore | None = None,
        trace_id: str | None = None,
        trace_payload: TracePayload | None = None,
    ) -> None:
        emit_compliance_event(
            ComplianceEvent(
                decision_id=decision.decision_id,
                reason_code=decision.reason_code.value,
                policy_version=decision.policy_version,
                route=decision.route.value,
                model=decision.model,
                caller=caller,
                ctx=decision.ctx,
                allowed=decision.allowed,
            )
        )
        if trace_store is not None and trace_id is not None and caller is not None:
            trace_store.recorder(
                trace_id=trace_id,
                caller=caller,
                ctx=decision.ctx,
            ).append_policy_decision(decision, payload=trace_payload)

    @staticmethod
    def _sensitivity_from_ctx(ctx: str | None) -> SensitivityLevel:
        if is_corporate_context(ctx):
            return SensitivityLevel.RESTRICTED
        return SensitivityLevel.PUBLIC
