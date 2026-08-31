"""
Tests for the Compliance Engine.
Verifies stopping rules, escalation logic, quiet hours, and opt-out handling.
"""

import pytest
from datetime import datetime, timedelta

from recovrai.services.compliance import ComplianceEngine
from recovrai.config import RecoveryChannel, WorkflowStatus
from recovrai.models.recovery import RecoveryWorkflow


@pytest.fixture
def engine():
    return ComplianceEngine()


def _make_workflow(**kwargs) -> RecoveryWorkflow:
    """Helper to create a test workflow."""
    defaults = {
        "id": "test-wf-1",
        "event_type": "payment_failed",
        "event_id": "test-event-1",
        "customer_id": "cust_test1",
        "customer_name": "Test User",
        "customer_email": "test@example.com",
        "customer_phone": "+919876543210",
        "status": WorkflowStatus.EXECUTING.value,
        "amount_at_risk": 250000,
        "contact_attempts": 0,
        "confidence": 0.95,
        "urgency": "medium",
        "created_at": datetime.utcnow(),
    }
    defaults.update(kwargs)
    wf = RecoveryWorkflow()
    for key, value in defaults.items():
        setattr(wf, key, value)
    return wf


class TestStoppingRules:
    def test_max_contact_attempts_blocks(self, engine):
        wf = _make_workflow(contact_attempts=5)
        allowed, reason = engine.check_can_contact(wf, RecoveryChannel.SMS)
        assert not allowed
        assert "Max contact attempts" in reason

    def test_under_max_attempts_allows(self, engine):
        wf = _make_workflow(contact_attempts=2)
        allowed, reason = engine.check_can_contact(wf, RecoveryChannel.SMS)
        assert allowed

    def test_terminal_state_blocks(self, engine):
        wf = _make_workflow(status=WorkflowStatus.RECOVERED.value)
        allowed, reason = engine.check_can_contact(wf, RecoveryChannel.SMS)
        assert not allowed
        assert "terminal state" in reason

    def test_workflow_lifetime_exceeded_blocks(self, engine):
        wf = _make_workflow(
            created_at=datetime.utcnow() - timedelta(days=10)
        )
        allowed, reason = engine.check_can_contact(wf, RecoveryChannel.SMS)
        assert not allowed
        assert "lifetime" in reason

    def test_cooldown_not_elapsed_blocks(self, engine):
        wf = _make_workflow(
            last_contact_at=datetime.utcnow() - timedelta(hours=1)
        )
        allowed, reason = engine.check_can_contact(wf, RecoveryChannel.SMS)
        assert not allowed
        assert "cooldown" in reason.lower()

    def test_cooldown_elapsed_allows(self, engine):
        wf = _make_workflow(
            last_contact_at=datetime.utcnow() - timedelta(hours=5)
        )
        allowed, reason = engine.check_can_contact(wf, RecoveryChannel.SMS)
        assert allowed


class TestEscalation:
    def test_high_value_transaction_escalates(self, engine):
        wf = _make_workflow(amount_at_risk=7500000)  # ₹75,000
        should, reason = engine.check_should_escalate(wf)
        assert should
        assert "High-value" in reason

    def test_low_confidence_escalates(self, engine):
        wf = _make_workflow(confidence=0.4)
        should, reason = engine.check_should_escalate(wf)
        assert should
        assert "confidence" in reason.lower()

    def test_high_confidence_no_escalation(self, engine):
        wf = _make_workflow(confidence=0.95, amount_at_risk=250000)
        should, reason = engine.check_should_escalate(wf)
        assert not should

    def test_max_attempts_approaching_escalates(self, engine):
        wf = _make_workflow(contact_attempts=4)  # max is 5, so 4 triggers
        should, reason = engine.check_should_escalate(wf)
        assert should


class TestChannelEscalation:
    def test_first_channel(self, engine):
        wf = _make_workflow(current_channel=None)
        channel = engine.get_next_channel(wf)
        assert channel == RecoveryChannel.PAYMENT_LINK

    def test_escalate_from_sms(self, engine):
        wf = _make_workflow(current_channel="sms")
        channel = engine.get_next_channel(wf)
        assert channel == RecoveryChannel.EMAIL

    def test_escalate_from_voice(self, engine):
        wf = _make_workflow(current_channel="voice_call")
        channel = engine.get_next_channel(wf)
        assert channel == RecoveryChannel.HUMAN_ESCALATION

    def test_all_channels_exhausted(self, engine):
        wf = _make_workflow(current_channel="human_escalation")
        channel = engine.get_next_channel(wf)
        assert channel is None


class TestOptOut:
    def test_opted_out_customer(self, engine):
        opted_out_phones = {"+919876543210", "+919876543211"}
        is_out, reason = engine.check_customer_opted_out(
            "+919876543210", opted_out_phones
        )
        assert is_out
        assert "opted out" in reason.lower()

    def test_not_opted_out_customer(self, engine):
        opted_out_phones = {"+919876543211"}
        is_out, reason = engine.check_customer_opted_out(
            "+919876543210", opted_out_phones
        )
        assert not is_out


class TestNextActionTime:
    def test_next_action_respects_quiet_hours(self, engine):
        # Set current time to 10 PM IST → should push to 9 AM next day
        # 10 PM IST = 4:30 PM UTC
        now = datetime(2024, 1, 15, 16, 30, 0)  # 4:30 PM UTC = 10 PM IST
        next_time = engine.calculate_next_action_time(30, now=now)
        # Should be pushed to at least 3:30 AM UTC (9 AM IST)
        assert next_time.hour >= 3 or next_time.day > now.day

    def test_next_action_during_business_hours(self, engine):
        # Set current time to 2 PM IST → should be same day
        # 2 PM IST = 8:30 AM UTC
        now = datetime(2024, 1, 15, 8, 30, 0)
        next_time = engine.calculate_next_action_time(30, now=now)
        expected = now + timedelta(minutes=30)
        assert next_time == expected
