"""
Metrics models for batch reporting and dashboard display.
Provides aggregate views of recovery performance.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class RecoveryMetric(Base):
    """
    Snapshot of recovery metrics at a point in time.
    Generated during batch processing for reporting.
    """

    __tablename__ = "recovery_metrics"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Batch info
    batch_id: Mapped[str] = mapped_column(String(64), index=True)
    batch_size: Mapped[int] = mapped_column(Integer)
    
    # Aggregate metrics
    total_amount_at_risk: Mapped[int] = mapped_column(Integer)  # paise
    total_amount_recovered: Mapped[int] = mapped_column(Integer)  # paise
    recovery_rate_percent: Mapped[float] = mapped_column(Float)
    
    # Per-event-type breakdown
    payment_failures_count: Mapped[int] = mapped_column(Integer, default=0)
    payment_failures_recovered: Mapped[int] = mapped_column(Integer, default=0)
    checkout_abandonment_count: Mapped[int] = mapped_column(Integer, default=0)
    checkout_abandonment_recovered: Mapped[int] = mapped_column(Integer, default=0)
    subscription_failures_count: Mapped[int] = mapped_column(Integer, default=0)
    subscription_failures_recovered: Mapped[int] = mapped_column(Integer, default=0)
    invoice_overdue_count: Mapped[int] = mapped_column(Integer, default=0)
    invoice_overdue_recovered: Mapped[int] = mapped_column(Integer, default=0)
    
    # Per-channel effectiveness
    sms_sent: Mapped[int] = mapped_column(Integer, default=0)
    sms_recovered: Mapped[int] = mapped_column(Integer, default=0)
    email_sent: Mapped[int] = mapped_column(Integer, default=0)
    email_recovered: Mapped[int] = mapped_column(Integer, default=0)
    voice_calls_made: Mapped[int] = mapped_column(Integer, default=0)
    voice_calls_recovered: Mapped[int] = mapped_column(Integer, default=0)
    payment_links_sent: Mapped[int] = mapped_column(Integer, default=0)
    payment_links_recovered: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timing
    mean_time_to_recovery_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Compliance
    escalated_count: Mapped[int] = mapped_column(Integer, default=0)
    stopped_compliance_count: Mapped[int] = mapped_column(Integer, default=0)
    false_positive_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Promise tracking
    promises_made: Mapped[int] = mapped_column(Integer, default=0)
    promises_fulfilled: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timestamps
    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def total_at_risk_inr(self) -> float:
        return self.total_amount_at_risk / 100

    @property
    def total_recovered_inr(self) -> float:
        return self.total_amount_recovered / 100

    @property
    def promise_fulfillment_rate(self) -> float:
        if self.promises_made == 0:
            return 0.0
        return (self.promises_fulfilled / self.promises_made) * 100

    def __repr__(self) -> str:
        return (
            f"<RecoveryMetric batch={self.batch_id[:8]} "
            f"₹{self.total_recovered_inr}/₹{self.total_at_risk_inr} "
            f"({self.recovery_rate_percent:.1f}%)>"
        )
