"""
Compliance engine — stopping rules, escalation logic, quiet hours, and rate limiting.
Ensures every recovery action is bounded, compliant, and auditable.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pytz

from ..config import get_settings, RecoveryChannel, WorkflowStatus
from ..models.recovery import RecoveryWorkflow

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class ComplianceViolation(Exception):
    """Raised when an action would violate compliance rules."""

    def __init__(self, rule: str, reason: str):
        self.rule = rule
        self.reason = reason
        super().__init__(f"Compliance violation [{rule}]: {reason}")


class ComplianceEngine:
    """
    Enforces all stopping rules, escalation policies, and contact restrictions.
    Every action must pass through this engine before execution.
    """

    def __init__(self):
        self.settings = get_settings()

    def check_can_contact(
        self,
        workflow: RecoveryWorkflow,
        channel: RecoveryChannel,
        now: Optional[datetime] = None,
    ) -> tuple[bool, str]:
        """
        Check if contacting the customer is allowed right now.
        
        Args:
            workflow: The recovery workflow
            channel: The proposed contact channel
            now: Current time (for testing). Defaults to UTC now.
            
        Returns:
            (allowed: bool, reason: str)
        """
        now = now or datetime.utcnow()
        ist_now = now.replace(tzinfo=pytz.utc).astimezone(IST)

        # Rule 1: Workflow in terminal state
        if workflow.is_terminal:
            return False, f"Workflow is in terminal state: {workflow.status}"

        # Rule 2: Max contact attempts exceeded
        if workflow.contact_attempts >= self.settings.max_contact_attempts:
            return False, (
                f"Max contact attempts ({self.settings.max_contact_attempts}) "
                f"reached. Current: {workflow.contact_attempts}"
            )

        # Rule 3: Workflow lifetime exceeded
        if workflow.created_at:
            workflow_age = now - workflow.created_at
            max_lifetime = timedelta(days=self.settings.max_workflow_lifetime_days)
            if workflow_age > max_lifetime:
                return False, (
                    f"Workflow lifetime ({self.settings.max_workflow_lifetime_days} days) "
                    f"exceeded. Age: {workflow_age.days} days"
                )

        # Rule 4: Quiet hours (no contact between 9 PM and 9 AM IST)
        if channel in (RecoveryChannel.SMS, RecoveryChannel.VOICE_CALL, RecoveryChannel.WHATSAPP):
            hour = ist_now.hour
            if hour >= self.settings.quiet_hours_start or hour < self.settings.quiet_hours_end:
                return False, (
                    f"Quiet hours: no {channel.value} between "
                    f"{self.settings.quiet_hours_start}:00 and "
                    f"{self.settings.quiet_hours_end}:00 IST. "
                    f"Current IST hour: {hour}"
                )

        # Rule 5: Cooldown between contacts
        if workflow.last_contact_at:
            cooldown = timedelta(hours=self.settings.cooldown_between_contacts_hours)
            time_since_last = now - workflow.last_contact_at
            if time_since_last < cooldown:
                remaining = cooldown - time_since_last
                return False, (
                    f"Contact cooldown ({self.settings.cooldown_between_contacts_hours}h) "
                    f"not elapsed. Remaining: {remaining}"
                )

        # Rule 6: Voice call frequency limit
        if channel == RecoveryChannel.VOICE_CALL:
            if workflow.last_contact_at and workflow.current_channel == "voice_call":
                voice_cooldown = timedelta(hours=48)
                time_since_last = now - workflow.last_contact_at
                if time_since_last < voice_cooldown:
                    remaining = voice_cooldown - time_since_last
                    return False, (
                        f"Max 1 voice call per 48 hours. "
                        f"Remaining: {remaining}"
                    )

        return True, "Contact permitted"

    def check_should_escalate(self, workflow: RecoveryWorkflow) -> tuple[bool, str]:
        """
        Determine if the workflow should be escalated to a human agent.
        
        Returns:
            (should_escalate: bool, reason: str)
        """
        # Rule 1: High-value transactions always escalate
        if workflow.amount_at_risk >= self.settings.high_value_threshold_inr * 100:
            return True, (
                f"High-value transaction: ₹{workflow.amount_at_risk_inr} "
                f"exceeds threshold of ₹{self.settings.high_value_threshold_inr}"
            )

        # Rule 2: Low confidence diagnosis
        if workflow.confidence < self.settings.min_confidence_for_auto_action:
            return True, (
                f"Low diagnosis confidence: {workflow.confidence:.2f} "
                f"(threshold: {self.settings.min_confidence_for_auto_action})"
            )

        # Rule 3: Max contact attempts nearly exhausted
        if workflow.contact_attempts >= self.settings.max_contact_attempts - 1:
            return True, (
                f"Approaching max contact attempts: "
                f"{workflow.contact_attempts}/{self.settings.max_contact_attempts}"
            )

        return False, "No escalation needed"

    def get_next_channel(
        self, workflow: RecoveryWorkflow
    ) -> Optional[RecoveryChannel]:
        """
        Determine the next channel in the escalation ladder.
        
        Returns:
            The next channel to use, or None if all channels exhausted.
        """
        ladder = self.settings.escalation_ladder
        current = workflow.current_channel

        if current is None:
            return ladder[0]

        try:
            current_enum = RecoveryChannel(current)
            current_idx = ladder.index(current_enum)
            if current_idx + 1 < len(ladder):
                return ladder[current_idx + 1]
            return None  # All channels exhausted
        except (ValueError, IndexError):
            return ladder[0]

    def check_customer_opted_out(
        self, customer_phone: str, opted_out_phones: set[str]
    ) -> tuple[bool, str]:
        """
        Check if the customer has opted out of all communications.
        
        Args:
            customer_phone: Customer's phone number
            opted_out_phones: Set of phone numbers that have opted out
            
        Returns:
            (opted_out: bool, reason: str)
        """
        if customer_phone in opted_out_phones:
            return True, (
                f"Customer {customer_phone} has explicitly opted out of communications"
            )
        return False, "Customer has not opted out"

    def calculate_next_action_time(
        self,
        delay_minutes: int,
        now: Optional[datetime] = None,
    ) -> datetime:
        """
        Calculate when the next action should execute, respecting quiet hours.
        
        If the calculated time falls within quiet hours, it's pushed to
        the next available window (9 AM IST).
        """
        now = now or datetime.utcnow()
        next_time = now + timedelta(minutes=delay_minutes)

        # Convert to IST to check quiet hours
        ist_next = next_time.replace(tzinfo=pytz.utc).astimezone(IST)
        hour = ist_next.hour

        if hour >= self.settings.quiet_hours_start or hour < self.settings.quiet_hours_end:
            # Push to next 9 AM IST
            if hour >= self.settings.quiet_hours_start:
                # After 9 PM today → 9 AM tomorrow
                next_day = ist_next.date() + timedelta(days=1)
            else:
                # Before 9 AM today → 9 AM today
                next_day = ist_next.date()

            ist_next = ist_next.replace(
                year=next_day.year,
                month=next_day.month,
                day=next_day.day,
                hour=self.settings.quiet_hours_end,
                minute=0,
                second=0,
                microsecond=0,
            )
            next_time = ist_next.astimezone(pytz.utc).replace(tzinfo=None)
            logger.info(
                f"Next action pushed out of quiet hours to {ist_next.strftime('%Y-%m-%d %H:%M IST')}"
            )

        return next_time

    def generate_compliance_report(
        self, workflow: RecoveryWorkflow
    ) -> dict:
        """Generate a compliance summary for a workflow."""
        can_contact, contact_reason = self.check_can_contact(
            workflow, RecoveryChannel.SMS
        )
        should_escalate, escalate_reason = self.check_should_escalate(workflow)

        return {
            "workflow_id": workflow.id,
            "status": workflow.status,
            "contact_attempts": workflow.contact_attempts,
            "max_attempts": self.settings.max_contact_attempts,
            "can_contact": can_contact,
            "contact_restriction_reason": contact_reason if not can_contact else None,
            "should_escalate": should_escalate,
            "escalation_reason": escalate_reason if should_escalate else None,
            "workflow_age_days": (
                (datetime.utcnow() - workflow.created_at).days
                if workflow.created_at else 0
            ),
            "max_lifetime_days": self.settings.max_workflow_lifetime_days,
            "amount_at_risk_inr": workflow.amount_at_risk_inr,
            "high_value_threshold_inr": self.settings.high_value_threshold_inr,
        }
