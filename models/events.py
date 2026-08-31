"""
Event models representing revenue-at-risk scenarios.
Each event type maps to a class of revenue loss that the system can detect and recover.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class PaymentEvent(Base):
    """A failed or degraded payment event from Razorpay."""

    __tablename__ = "payment_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    payment_id: Mapped[str] = mapped_column(String(64), index=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_name: Mapped[str] = mapped_column(String(128))
    customer_email: Mapped[str] = mapped_column(String(256))
    customer_phone: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)  # Amount in paise
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(32))  # failed, authorized, captured
    method: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # card, upi, netbanking, wallet
    bank: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    wallet: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # gateway, bank, customer
    error_step: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # payment_authorization, payment_capture
    error_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    international: Mapped[bool] = mapped_column(default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed: Mapped[bool] = mapped_column(default=False)

    @property
    def amount_inr(self) -> float:
        """Amount in INR (from paise)."""
        return self.amount / 100

    def __repr__(self) -> str:
        return f"<PaymentEvent {self.payment_id} ₹{self.amount_inr} {self.status}>"


class CheckoutEvent(Base):
    """A checkout abandonment event — user started but didn't complete payment."""

    __tablename__ = "checkout_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_name: Mapped[str] = mapped_column(String(128))
    customer_email: Mapped[str] = mapped_column(String(256))
    customer_phone: Mapped[str] = mapped_column(String(20))
    cart_value: Mapped[int] = mapped_column(Integer)  # Amount in paise
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    items_count: Mapped[int] = mapped_column(Integer, default=1)
    items_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stage_reached: Mapped[str] = mapped_column(String(32))  # cart, address, payment, confirmation
    time_spent_seconds: Mapped[int] = mapped_column(Integer, default=0)
    device_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # mobile, desktop, tablet
    abandoned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed: Mapped[bool] = mapped_column(default=False)

    @property
    def cart_value_inr(self) -> float:
        """Cart value in INR (from paise)."""
        return self.cart_value / 100

    def __repr__(self) -> str:
        return f"<CheckoutEvent {self.session_id} ₹{self.cart_value_inr} at {self.stage_reached}>"


class SubscriptionEvent(Base):
    """A failed subscription/recurring payment event."""

    __tablename__ = "subscription_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    subscription_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_id: Mapped[str] = mapped_column(String(64))
    plan_name: Mapped[str] = mapped_column(String(128))
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_name: Mapped[str] = mapped_column(String(128))
    customer_email: Mapped[str] = mapped_column(String(256))
    customer_phone: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)  # Amount in paise
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(32))  # halted, pending, cancelled
    failure_count: Mapped[int] = mapped_column(Integer, default=1)
    last_failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    billing_cycle_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    billing_cycle_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed: Mapped[bool] = mapped_column(default=False)

    @property
    def amount_inr(self) -> float:
        return self.amount / 100

    def __repr__(self) -> str:
        return f"<SubscriptionEvent {self.subscription_id} ₹{self.amount_inr} {self.status}>"


class InvoiceEvent(Base):
    """An overdue invoice event — B2B receivable past its due date."""

    __tablename__ = "invoice_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    invoice_id: Mapped[str] = mapped_column(String(64), index=True)
    invoice_number: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_name: Mapped[str] = mapped_column(String(128))
    customer_email: Mapped[str] = mapped_column(String(256))
    customer_phone: Mapped[str] = mapped_column(String(20))
    company_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    amount: Mapped[int] = mapped_column(Integer)  # Amount in paise
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(32))  # issued, overdue, partially_paid, paid
    amount_paid: Mapped[int] = mapped_column(Integer, default=0)
    due_date: Mapped[datetime] = mapped_column(DateTime)
    days_overdue: Mapped[int] = mapped_column(Integer, default=0)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed: Mapped[bool] = mapped_column(default=False)

    @property
    def amount_inr(self) -> float:
        return self.amount / 100

    @property
    def amount_due_inr(self) -> float:
        return (self.amount - self.amount_paid) / 100

    def __repr__(self) -> str:
        return f"<InvoiceEvent {self.invoice_number} ₹{self.amount_inr} {self.days_overdue}d overdue>"
