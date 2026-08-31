"""
Recovery workflow models — the core state machine tracking every recovery attempt.
Includes RecoveryWorkflow, RecoveryAction, and AuditLog for full traceability.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class RecoveryWorkflow(Base):
    """
    A single recovery workflow tracking the lifecycle of recovering one revenue event.
    
    States: detected → diagnosing → intervention_planned → executing →
            recovered | awaiting_promise | escalated | failed | stopped_compliance
    """

    __tablename__ = "recovery_workflows"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Link to the source event
    event_type: Mapped[str] = mapped_column(String(32), index=True)  # payment_failed, checkout_abandoned, etc.
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    
    # Customer info (denormalized for fast access)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_name: Mapped[str] = mapped_column(String(128))
    customer_email: Mapped[str] = mapped_column(String(256))
    customer_phone: Mapped[str] = mapped_column(String(20))
    
    # Workflow state
    status: Mapped[str] = mapped_column(String(32), default="detected", index=True)
    
    # Diagnosis
    diagnosis_rule: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    diagnosis_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    root_cause: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    urgency: Mapped[str] = mapped_column(String(16), default="medium")
    recommended_action: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    # Financial
    amount_at_risk: Mapped[int] = mapped_column(Integer)  # in paise
    amount_recovered: Mapped[int] = mapped_column(Integer, default=0)  # in paise
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    
    # Execution tracking
    contact_attempts: Mapped[int] = mapped_column(Integer, default=0)
    current_channel: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_contact_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_action_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Promise tracking
    promise_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    promise_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    promise_fulfilled: Mapped[bool] = mapped_column(default=False)
    
    # Recovery link
    payment_link_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payment_link_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Compliance
    stopped_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def amount_at_risk_inr(self) -> float:
        return self.amount_at_risk / 100

    @property
    def amount_recovered_inr(self) -> float:
        return self.amount_recovered / 100

    @property
    def recovery_rate(self) -> float:
        if self.amount_at_risk == 0:
            return 0.0
        return (self.amount_recovered / self.amount_at_risk) * 100

    @property
    def is_terminal(self) -> bool:
        """Whether this workflow has reached a final state."""
        return self.status in ("recovered", "failed", "escalated", "stopped_compliance")

    def __repr__(self) -> str:
        return (
            f"<RecoveryWorkflow {self.id[:8]} "
            f"₹{self.amount_at_risk_inr} {self.event_type} [{self.status}]>"
        )


class RecoveryAction(Base):
    """
    An individual action taken within a recovery workflow.
    Each contact attempt, retry, or escalation is recorded as an action.
    """

    __tablename__ = "recovery_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id: Mapped[str] = mapped_column(String(64), index=True)
    
    # Action details
    action_type: Mapped[str] = mapped_column(String(32))  # send_sms, send_email, voice_call, payment_retry, etc.
    channel: Mapped[str] = mapped_column(String(32))  # sms, email, whatsapp, voice_call, payment_link, system
    
    # Execution
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, executing, success, failed, skipped
    request_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # For voice calls
    call_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    call_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    customer_intent: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # will_pay, need_time, dispute, confused
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<RecoveryAction {self.action_type} via {self.channel} [{self.status}]>"


class AuditLog(Base):
    """
    Immutable audit trail — every single action, decision, and state change is recorded.
    This is the compliance backbone of the system.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    
    # What happened
    action: Mapped[str] = mapped_column(String(64))  # event_detected, diagnosis_completed, sms_sent, etc.
    actor: Mapped[str] = mapped_column(String(32))  # system, diagnosis_engine, voice_agent, compliance, human
    category: Mapped[str] = mapped_column(String(32), default="action")  # action, decision, compliance, error
    
    # Details
    details: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON blob for structured data
    
    # Timestamp
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<AuditLog [{self.category}] {self.action} by {self.actor}>"
