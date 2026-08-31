"""
Tests for the Diagnosis Engine.
Verifies deterministic rules and LLM fallback behavior.
"""

import pytest
from datetime import datetime, timedelta

from recovrai.services.diagnosis_engine import DiagnosisEngine, DiagnosisResult
from recovrai.models.events import (
    PaymentEvent,
    CheckoutEvent,
    SubscriptionEvent,
    InvoiceEvent,
)


@pytest.fixture
def engine():
    return DiagnosisEngine()


# ─── Payment Event Diagnosis ─────────────────────────────────────────────────

class TestPaymentDiagnosis:
    def _make_payment(self, error_code: str, **kwargs) -> PaymentEvent:
        return PaymentEvent(
            id="test-pay-1",
            payment_id="pay_test123",
            order_id="order_test123",
            customer_id="cust_test1",
            customer_name="Test User",
            customer_email="test@example.com",
            customer_phone="+919876543210",
            amount=250000,
            status="failed",
            method=kwargs.get("method", "upi"),
            error_code=error_code,
            error_description=f"Payment failed: {error_code}",
            error_source="gateway",
            error_reason=error_code.lower(),
            **{k: v for k, v in kwargs.items() if k != "method"},
        )

    def test_insufficient_funds(self, engine):
        event = self._make_payment("INSUFFICIENT_FUNDS")
        result = engine._diagnose_payment(event)
        assert result is not None
        assert result.root_cause == "Customer lacks funds"
        assert result.recommended_action == "send_payment_link"
        assert result.urgency == "medium"
        assert result.confidence == 1.0
        assert result.delay_minutes == 1440  # 24 hours

    def test_gateway_error(self, engine):
        event = self._make_payment("GATEWAY_ERROR")
        result = engine._diagnose_payment(event)
        assert result is not None
        assert result.recommended_action == "auto_retry"
        assert result.urgency == "high"
        assert result.delay_minutes == 30

    def test_card_expired(self, engine):
        event = self._make_payment("CARD_EXPIRED")
        result = engine._diagnose_payment(event)
        assert result is not None
        assert result.recommended_action == "send_update_card_link"

    def test_international_card(self, engine):
        event = self._make_payment("INTERNATIONAL_CARD_DECLINED", international=True)
        result = engine._diagnose_payment(event)
        assert result is not None
        assert result.recommended_action == "suggest_alternative_method"

    def test_upi_timeout(self, engine):
        event = self._make_payment("UPI_TIMEOUT", method="upi")
        result = engine._diagnose_payment(event)
        assert result is not None
        assert result.recommended_action == "auto_retry"
        assert result.delay_minutes == 5

    def test_unknown_error_returns_none(self, engine):
        event = self._make_payment("TOTALLY_UNKNOWN_ERROR_XYZ")
        result = engine._diagnose_payment(event)
        # Should return None for unknown errors → triggers LLM fallback
        assert result is None


# ─── Checkout Event Diagnosis ────────────────────────────────────────────────

class TestCheckoutDiagnosis:
    def _make_checkout(self, stage: str) -> CheckoutEvent:
        return CheckoutEvent(
            id="test-checkout-1",
            session_id="sess_test123",
            customer_id="cust_test1",
            customer_name="Test User",
            customer_email="test@example.com",
            customer_phone="+919876543210",
            cart_value=150000,
            items_count=3,
            stage_reached=stage,
            time_spent_seconds=120,
        )

    def test_payment_stage_abandonment(self, engine):
        event = self._make_checkout("payment")
        result = engine._diagnose_checkout(event)
        assert result is not None
        assert result.recommended_action == "send_recovery_sms"
        assert result.urgency == "high"
        assert result.delay_minutes == 15

    def test_cart_stage_abandonment(self, engine):
        event = self._make_checkout("cart")
        result = engine._diagnose_checkout(event)
        assert result is not None
        assert result.recommended_action == "send_cart_reminder"
        assert result.urgency == "low"

    def test_confirmation_stage_abandonment(self, engine):
        event = self._make_checkout("confirmation")
        result = engine._diagnose_checkout(event)
        assert result is not None
        assert result.urgency == "critical"


# ─── Subscription Event Diagnosis ────────────────────────────────────────────

class TestSubscriptionDiagnosis:
    def _make_subscription(self, failure_count: int) -> SubscriptionEvent:
        return SubscriptionEvent(
            id="test-sub-1",
            subscription_id="sub_test123",
            plan_id="plan_test1",
            plan_name="Pro Monthly",
            customer_id="cust_test1",
            customer_name="Test User",
            customer_email="test@example.com",
            customer_phone="+919876543210",
            amount=99900,
            status="halted",
            failure_count=failure_count,
            last_failure_reason="Card declined",
        )

    def test_first_failure_auto_retry(self, engine):
        event = self._make_subscription(1)
        result = engine._diagnose_subscription(event)
        assert result is not None
        assert result.recommended_action == "auto_retry"

    def test_second_failure_payment_link(self, engine):
        event = self._make_subscription(2)
        result = engine._diagnose_subscription(event)
        assert result is not None
        assert result.recommended_action == "send_payment_link"

    def test_third_failure_escalate(self, engine):
        event = self._make_subscription(3)
        result = engine._diagnose_subscription(event)
        assert result is not None
        assert result.recommended_action == "escalate_to_human"
        assert result.urgency == "critical"


# ─── Invoice Event Diagnosis ─────────────────────────────────────────────────

class TestInvoiceDiagnosis:
    def _make_invoice(self, days_overdue: int) -> InvoiceEvent:
        return InvoiceEvent(
            id="test-inv-1",
            invoice_id="inv_test123",
            invoice_number="INV-2024-001",
            customer_id="cust_test1",
            customer_name="Test User",
            customer_email="test@example.com",
            customer_phone="+919876543210",
            company_name="Test Corp",
            amount=500000,
            status="overdue",
            days_overdue=days_overdue,
            due_date=datetime.utcnow() - timedelta(days=days_overdue),
        )

    def test_mildly_overdue(self, engine):
        event = self._make_invoice(5)
        result = engine._diagnose_invoice(event)
        assert result is not None
        assert result.recommended_action == "send_invoice_reminder"
        assert result.urgency == "low"

    def test_moderately_overdue(self, engine):
        event = self._make_invoice(20)
        result = engine._diagnose_invoice(event)
        assert result is not None
        assert result.urgency == "medium"

    def test_very_overdue_gets_voice_call(self, engine):
        event = self._make_invoice(45)
        result = engine._diagnose_invoice(event)
        assert result is not None
        assert result.urgency == "high"

    def test_severely_overdue_is_critical(self, engine):
        event = self._make_invoice(70)
        result = engine._diagnose_invoice(event)
        assert result is not None
        assert result.recommended_action == "initiate_voice_call"
        assert result.urgency == "critical"
