"""
Recovery Orchestrator — the central state machine that drives all recovery workflows.
Detects → Diagnoses → Plans → Executes → Recovers/Escalates

This is the heart of RecovrAI. It processes events through the full lifecycle,
coordinates with the diagnosis engine, compliance engine, and notification service.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Union

from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings, EventType, RecoveryChannel, WorkflowStatus
from ..models.database import get_db_session
from ..models.events import (
    CheckoutEvent,
    InvoiceEvent,
    PaymentEvent,
    SubscriptionEvent,
)
from ..models.metrics import RecoveryMetric
from ..models.recovery import AuditLog, RecoveryAction, RecoveryWorkflow
from .compliance import ComplianceEngine
from .diagnosis_engine import DiagnosisEngine
from .notification import NotificationService
from .promise_tracker import PromiseTracker
from .razorpay_client import RazorpayClient

logger = logging.getLogger(__name__)

EventUnion = Union[PaymentEvent, CheckoutEvent, SubscriptionEvent, InvoiceEvent]


class RecoveryOrchestrator:
    """
    Central orchestrator that manages the full recovery lifecycle.
    Coordinates between diagnosis, compliance, notifications, and payment systems.
    """

    def __init__(self):
        self.settings = get_settings()
        self.diagnosis_engine = DiagnosisEngine()
        self.compliance_engine = ComplianceEngine()
        self.notification_service = NotificationService()
        self.razorpay_client = RazorpayClient()
        self.promise_tracker = PromiseTracker()
        self._opted_out_phones: set[str] = set()

    # ─── Event Ingestion ──────────────────────────────────────────────────────

    async def ingest_event(
        self, session: AsyncSession, event: EventUnion
    ) -> RecoveryWorkflow:
        """
        Ingest a new revenue-at-risk event and create a recovery workflow.
        
        This is the entry point for all events — from webhooks, simulators, or batch processing.
        """
        event_type = self._get_event_type(event)
        customer_info = self._extract_customer_info(event)
        amount_at_risk = self._extract_amount(event)

        workflow = RecoveryWorkflow(
            id=str(uuid.uuid4()),
            event_type=event_type.value,
            event_id=event.id,
            customer_id=customer_info["customer_id"],
            customer_name=customer_info["customer_name"],
            customer_email=customer_info["customer_email"],
            customer_phone=customer_info["customer_phone"],
            status=WorkflowStatus.DETECTED.value,
            amount_at_risk=amount_at_risk,
            currency="INR",
            created_at=datetime.utcnow(),
            amount_recovered=0,
            contact_attempts=0,
            promise_fulfilled=False,
            confidence=0.0,
            urgency="medium",
        )
        session.add(workflow)

        # Audit: event detected
        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action="event_detected",
            actor="system",
            category="action",
            details=(
                f"Revenue at risk detected: {event_type.value} for "
                f"₹{amount_at_risk / 100:.2f} from {customer_info['customer_name']}"
            ),
            metadata_json=json.dumps({
                "event_type": event_type.value,
                "event_id": event.id,
                "amount_paise": amount_at_risk,
                "customer_id": customer_info["customer_id"],
            }),
        )
        session.add(audit)

        # Mark the source event as processed
        event.processed = True

        logger.info(
            f"Event ingested → Workflow {workflow.id[:8]}: "
            f"{event_type.value} ₹{amount_at_risk / 100:.2f}"
        )
        return workflow

    # ─── Diagnosis Phase ──────────────────────────────────────────────────────

    async def diagnose_workflow(
        self, session: AsyncSession, workflow: RecoveryWorkflow, event: EventUnion
    ) -> RecoveryWorkflow:
        """
        Run the diagnosis engine on a detected workflow.
        Updates the workflow with root cause, confidence, and recommended action.
        """
        workflow.status = WorkflowStatus.DIAGNOSING.value

        diagnosis = await self.diagnosis_engine.diagnose(event)

        workflow.diagnosis_rule = diagnosis.diagnosis_rule
        workflow.diagnosis_description = diagnosis.diagnosis_description
        workflow.root_cause = diagnosis.root_cause
        workflow.confidence = diagnosis.confidence
        workflow.urgency = diagnosis.urgency
        workflow.recommended_action = diagnosis.recommended_action

        # Calculate when to act
        next_action_time = self.compliance_engine.calculate_next_action_time(
            diagnosis.delay_minutes
        )
        workflow.next_action_at = next_action_time
        workflow.status = WorkflowStatus.INTERVENTION_PLANNED.value

        # Audit: diagnosis completed
        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action="diagnosis_completed",
            actor="diagnosis_engine",
            category="decision",
            details=(
                f"Root cause: {diagnosis.root_cause}. "
                f"Confidence: {diagnosis.confidence:.2f}. "
                f"Recommended: {diagnosis.recommended_action}. "
                f"Urgency: {diagnosis.urgency}. "
                f"Rule: {diagnosis.diagnosis_rule or 'LLM fallback'}. "
                f"Next action at: {next_action_time.strftime('%Y-%m-%d %H:%M UTC')}"
            ),
            metadata_json=json.dumps({
                "root_cause": diagnosis.root_cause,
                "confidence": diagnosis.confidence,
                "recommended_action": diagnosis.recommended_action,
                "urgency": diagnosis.urgency,
                "diagnosis_rule": diagnosis.diagnosis_rule,
                "delay_minutes": diagnosis.delay_minutes,
            }),
        )
        session.add(audit)

        logger.info(
            f"Diagnosis complete for {workflow.id[:8]}: "
            f"{diagnosis.root_cause} → {diagnosis.recommended_action} "
            f"(confidence: {diagnosis.confidence:.2f})"
        )
        return workflow

    # ─── Execution Phase ──────────────────────────────────────────────────────

    async def execute_intervention(
        self, session: AsyncSession, workflow: RecoveryWorkflow
    ) -> RecoveryWorkflow:
        """
        Execute the planned intervention for a workflow.
        Checks compliance before every action.
        """
        # Check if customer has opted out
        opted_out, opt_reason = self.compliance_engine.check_customer_opted_out(
            workflow.customer_phone, self._opted_out_phones
        )
        if opted_out:
            workflow.status = WorkflowStatus.STOPPED_COMPLIANCE.value
            workflow.stopped_reason = opt_reason
            audit = AuditLog(
                id=str(uuid.uuid4()),
                workflow_id=workflow.id,
                action="stopped_customer_opt_out",
                actor="compliance",
                category="compliance",
                details=opt_reason,
            )
            session.add(audit)
            logger.info(f"Workflow {workflow.id[:8]} stopped: customer opted out")
            return workflow

        # Check if should escalate
        should_escalate, escalate_reason = self.compliance_engine.check_should_escalate(
            workflow
        )
        if should_escalate:
            return await self._escalate_workflow(session, workflow, escalate_reason)

        # Determine the channel to use
        channel = self._map_action_to_channel(workflow.recommended_action)

        # Check compliance for the chosen channel
        can_contact, contact_reason = self.compliance_engine.check_can_contact(
            workflow, channel
        )

        if not can_contact:
            # Log the compliance block but don't fail the workflow
            audit = AuditLog(
                id=str(uuid.uuid4()),
                workflow_id=workflow.id,
                action="contact_blocked_compliance",
                actor="compliance",
                category="compliance",
                details=f"Contact via {channel.value} blocked: {contact_reason}",
            )
            session.add(audit)
            logger.info(
                f"Contact blocked for {workflow.id[:8]}: {contact_reason}"
            )

            # Reschedule for later
            workflow.next_action_at = self.compliance_engine.calculate_next_action_time(
                self.settings.cooldown_between_contacts_hours * 60
            )
            return workflow

        # Execute the intervention
        workflow.status = WorkflowStatus.EXECUTING.value

        action = await self._execute_action(session, workflow, channel)
        session.add(action)

        # Update workflow state
        workflow.contact_attempts += 1
        workflow.current_channel = channel.value
        workflow.last_contact_at = datetime.utcnow()

        if action.status == "success":
            # If it's an auto-retry that succeeded, mark as recovered
            if workflow.recommended_action == "auto_retry":
                workflow.status = WorkflowStatus.RECOVERED.value
                workflow.amount_recovered = workflow.amount_at_risk
                workflow.resolved_at = datetime.utcnow()
            else:
                # For notifications, set to executing and schedule follow-up
                next_channel = self.compliance_engine.get_next_channel(workflow)
                if next_channel:
                    workflow.next_action_at = self.compliance_engine.calculate_next_action_time(
                        self.settings.cooldown_between_contacts_hours * 60
                    )
                    workflow.recommended_action = self._channel_to_action(next_channel)
                else:
                    # All channels exhausted
                    return await self._escalate_workflow(
                        session, workflow, "All recovery channels exhausted"
                    )
        else:
            # Action failed — try next channel
            next_channel = self.compliance_engine.get_next_channel(workflow)
            if next_channel:
                workflow.current_channel = next_channel.value
                workflow.recommended_action = self._channel_to_action(next_channel)
                workflow.next_action_at = self.compliance_engine.calculate_next_action_time(30)
            else:
                return await self._escalate_workflow(
                    session, workflow, f"Action failed and all channels exhausted: {action.error_message}"
                )

        return workflow

    async def _execute_action(
        self,
        session: AsyncSession,
        workflow: RecoveryWorkflow,
        channel: RecoveryChannel,
    ) -> RecoveryAction:
        """Execute a specific recovery action based on channel."""

        # Generate a payment link first (needed for most channels)
        payment_link_url = await self._ensure_payment_link(session, workflow)

        if channel == RecoveryChannel.PAYMENT_LINK:
            action = RecoveryAction(
                id=str(uuid.uuid4()),
                workflow_id=workflow.id,
                action_type="send_payment_link",
                channel=channel.value,
                status="success",
                response_payload=json.dumps({
                    "payment_link_url": payment_link_url,
                    "payment_link_id": workflow.payment_link_id,
                }),
                completed_at=datetime.utcnow(),
            )

        elif channel == RecoveryChannel.SMS:
            message = self.notification_service.build_recovery_sms(
                workflow.customer_name,
                workflow.amount_at_risk_inr,
                payment_link_url,
                workflow.root_cause or "",
            )
            action = await self.notification_service.send_sms(
                workflow.customer_phone, message, workflow.id
            )

        elif channel == RecoveryChannel.EMAIL:
            subject = f"Complete your payment of ₹{workflow.amount_at_risk_inr:.0f}"
            body = (
                f"Dear {workflow.customer_name},\n\n"
                f"We noticed your recent payment of ₹{workflow.amount_at_risk_inr:.2f} "
                f"was not completed. You can retry securely here:\n"
                f"{payment_link_url}\n\n"
                f"If you need assistance, please reply to this email.\n\n"
                f"Best regards,\nRecovrAI"
            )
            action = await self.notification_service.send_email(
                workflow.customer_email, subject, body, workflow.id
            )

        elif channel == RecoveryChannel.WHATSAPP:
            message = (
                f"Hi {workflow.customer_name.split()[0]}, "
                f"your payment of ₹{workflow.amount_at_risk_inr:.0f} is pending. "
                f"Pay securely: {payment_link_url}"
            )
            action = await self.notification_service.send_whatsapp(
                workflow.customer_phone, message, workflow.id
            )

        elif channel == RecoveryChannel.VOICE_CALL:
            # Voice calls are handled separately via WebSocket
            action = RecoveryAction(
                id=str(uuid.uuid4()),
                workflow_id=workflow.id,
                action_type="initiate_voice_call",
                channel=channel.value,
                status="success",
                response_payload=json.dumps({
                    "message": "Voice call workflow initiated. Use /voice/ws/ endpoint.",
                }),
                completed_at=datetime.utcnow(),
            )

        elif channel == RecoveryChannel.HUMAN_ESCALATION:
            return await self._create_escalation_action(session, workflow)

        else:
            action = RecoveryAction(
                id=str(uuid.uuid4()),
                workflow_id=workflow.id,
                action_type="unknown",
                channel=channel.value,
                status="failed",
                error_message=f"Unknown channel: {channel.value}",
                completed_at=datetime.utcnow(),
            )

        # Audit the action
        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action=f"action_{action.action_type}",
            actor="orchestrator",
            category="action",
            details=(
                f"Action: {action.action_type} via {channel.value}. "
                f"Status: {action.status}. "
                f"Attempt #{workflow.contact_attempts + 1}"
            ),
            metadata_json=json.dumps({
                "action_type": action.action_type,
                "channel": channel.value,
                "status": action.status,
                "attempt": workflow.contact_attempts + 1,
            }),
        )
        session.add(audit)

        return action

    async def _ensure_payment_link(
        self, session: AsyncSession, workflow: RecoveryWorkflow
    ) -> str:
        """Ensure a payment link exists for the workflow, creating one if needed."""
        if workflow.payment_link_url:
            return workflow.payment_link_url

        link = self.razorpay_client.create_payment_link(
            amount=workflow.amount_at_risk,
            customer_name=workflow.customer_name,
            customer_email=workflow.customer_email,
            customer_phone=workflow.customer_phone,
            description=f"Recovery payment for {workflow.event_type}",
            notes={"workflow_id": workflow.id, "event_type": workflow.event_type},
        )

        workflow.payment_link_id = link["id"]
        workflow.payment_link_url = link.get("short_url", f"https://rzp.io/i/{link['id']}")

        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action="payment_link_created",
            actor="orchestrator",
            category="action",
            details=(
                f"Payment link created: {workflow.payment_link_url} "
                f"for ₹{workflow.amount_at_risk_inr:.2f}"
            ),
        )
        session.add(audit)

        return workflow.payment_link_url

    async def _escalate_workflow(
        self,
        session: AsyncSession,
        workflow: RecoveryWorkflow,
        reason: str,
    ) -> RecoveryWorkflow:
        """Escalate the workflow to human review."""
        workflow.status = WorkflowStatus.ESCALATED.value
        workflow.stopped_reason = reason
        workflow.resolved_at = datetime.utcnow()

        action = await self._create_escalation_action(session, workflow)
        session.add(action)

        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action="escalated_to_human",
            actor="compliance",
            category="compliance",
            details=f"Workflow escalated to human agent. Reason: {reason}",
            metadata_json=json.dumps({
                "reason": reason,
                "contact_attempts": workflow.contact_attempts,
                "amount_at_risk_inr": workflow.amount_at_risk_inr,
            }),
        )
        session.add(audit)

        logger.info(f"Workflow {workflow.id[:8]} escalated: {reason}")
        return workflow

    async def _create_escalation_action(
        self, session: AsyncSession, workflow: RecoveryWorkflow
    ) -> RecoveryAction:
        """Create an escalation action record."""
        return RecoveryAction(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action_type="escalate_to_human",
            channel=RecoveryChannel.HUMAN_ESCALATION.value,
            status="success",
            response_payload=json.dumps({
                "message": "Escalated to human agent for review",
                "customer": workflow.customer_name,
                "amount_inr": workflow.amount_at_risk_inr,
                "root_cause": workflow.root_cause,
            }),
            completed_at=datetime.utcnow(),
        )

    # ─── Batch Processing ─────────────────────────────────────────────────────

    async def process_batch(self, session: AsyncSession) -> dict:
        """
        Process all unprocessed events in batch.
        Returns a summary of the batch processing results.
        """
        batch_id = str(uuid.uuid4())[:8]
        results = {
            "batch_id": batch_id,
            "total_processed": 0,
            "recovered": 0,
            "escalated": 0,
            "failed": 0,
            "compliance_stopped": 0,
            "amount_at_risk": 0,
            "amount_recovered": 0,
            "details": [],
        }

        logger.info(f"Starting batch processing: {batch_id}")

        # Process all event types
        for EventModel, event_type in [
            (PaymentEvent, EventType.PAYMENT_FAILED),
            (CheckoutEvent, EventType.CHECKOUT_ABANDONED),
            (SubscriptionEvent, EventType.SUBSCRIPTION_FAILED),
            (InvoiceEvent, EventType.INVOICE_OVERDUE),
        ]:
            stmt = select(EventModel).where(EventModel.processed == False)
            result = await session.execute(stmt)
            events = list(result.scalars().all())

            for event in events:
                try:
                    workflow = await self.ingest_event(session, event)
                    workflow = await self.diagnose_workflow(session, workflow, event)
                    workflow = await self.execute_intervention(session, workflow)

                    # Simulate recovery for auto-retry successes
                    if (
                        workflow.recommended_action == "auto_retry"
                        and workflow.status == WorkflowStatus.EXECUTING.value
                    ):
                        workflow.status = WorkflowStatus.RECOVERED.value
                        workflow.amount_recovered = workflow.amount_at_risk
                        workflow.resolved_at = datetime.utcnow()

                    results["total_processed"] += 1
                    results["amount_at_risk"] += workflow.amount_at_risk

                    if workflow.status == WorkflowStatus.RECOVERED.value:
                        results["recovered"] += 1
                        results["amount_recovered"] += workflow.amount_recovered
                    elif workflow.status == WorkflowStatus.ESCALATED.value:
                        results["escalated"] += 1
                    elif workflow.status == WorkflowStatus.STOPPED_COMPLIANCE.value:
                        results["compliance_stopped"] += 1
                    elif workflow.status == WorkflowStatus.FAILED.value:
                        results["failed"] += 1

                    results["details"].append({
                        "workflow_id": workflow.id,
                        "event_type": event_type.value,
                        "status": workflow.status,
                        "amount_at_risk_inr": workflow.amount_at_risk_inr,
                        "amount_recovered_inr": workflow.amount_recovered_inr,
                        "root_cause": workflow.root_cause,
                        "action": workflow.recommended_action,
                        "channel": workflow.current_channel,
                    })

                except Exception as e:
                    logger.error(f"Error processing event {event.id}: {e}")
                    results["failed"] += 1
                    results["details"].append({
                        "event_id": event.id,
                        "event_type": event_type.value,
                        "status": "error",
                        "error": str(e),
                    })

        # Save batch metrics
        await self._save_batch_metrics(session, batch_id, results)

        logger.info(
            f"Batch {batch_id} complete: "
            f"{results['total_processed']} processed, "
            f"{results['recovered']} recovered, "
            f"₹{results['amount_recovered'] / 100:.2f} total recovered"
        )

        return results

    async def _save_batch_metrics(
        self, session: AsyncSession, batch_id: str, results: dict
    ) -> None:
        """Save batch processing metrics to the database."""
        recovery_rate = 0.0
        if results["amount_at_risk"] > 0:
            recovery_rate = (results["amount_recovered"] / results["amount_at_risk"]) * 100

        # Count by event type
        type_counts = {}
        type_recovered = {}
        for detail in results["details"]:
            et = detail.get("event_type", "unknown")
            type_counts[et] = type_counts.get(et, 0) + 1
            if detail.get("status") == WorkflowStatus.RECOVERED.value:
                type_recovered[et] = type_recovered.get(et, 0) + 1

        metric = RecoveryMetric(
            id=str(uuid.uuid4()),
            batch_id=batch_id,
            batch_size=results["total_processed"],
            total_amount_at_risk=results["amount_at_risk"],
            total_amount_recovered=results["amount_recovered"],
            recovery_rate_percent=recovery_rate,
            payment_failures_count=type_counts.get("payment_failed", 0),
            payment_failures_recovered=type_recovered.get("payment_failed", 0),
            checkout_abandonment_count=type_counts.get("checkout_abandoned", 0),
            checkout_abandonment_recovered=type_recovered.get("checkout_abandoned", 0),
            subscription_failures_count=type_counts.get("subscription_failed", 0),
            subscription_failures_recovered=type_recovered.get("subscription_failed", 0),
            invoice_overdue_count=type_counts.get("invoice_overdue", 0),
            invoice_overdue_recovered=type_recovered.get("invoice_overdue", 0),
            escalated_count=results["escalated"],
            stopped_compliance_count=results["compliance_stopped"],
        )
        session.add(metric)

    # ─── Customer Opt-Out ─────────────────────────────────────────────────────

    async def handle_customer_opt_out(
        self, session: AsyncSession, phone: str
    ) -> int:
        """
        Handle customer opt-out. Stops ALL active workflows for this customer.
        Returns the number of workflows stopped.
        """
        self._opted_out_phones.add(phone)

        stmt = select(RecoveryWorkflow).where(
            and_(
                RecoveryWorkflow.customer_phone == phone,
                ~RecoveryWorkflow.status.in_([
                    WorkflowStatus.RECOVERED.value,
                    WorkflowStatus.FAILED.value,
                    WorkflowStatus.ESCALATED.value,
                    WorkflowStatus.STOPPED_COMPLIANCE.value,
                ]),
            )
        )
        result = await session.execute(stmt)
        workflows = list(result.scalars().all())

        for wf in workflows:
            wf.status = WorkflowStatus.STOPPED_COMPLIANCE.value
            wf.stopped_reason = "Customer explicitly opted out of communications"
            wf.resolved_at = datetime.utcnow()

            audit = AuditLog(
                id=str(uuid.uuid4()),
                workflow_id=wf.id,
                action="customer_opted_out",
                actor="compliance",
                category="compliance",
                details=(
                    f"Customer {phone} opted out. "
                    f"Workflow {wf.id[:8]} immediately stopped. "
                    f"₹{wf.amount_at_risk_inr:.2f} at risk left unrecovered."
                ),
            )
            session.add(audit)

        logger.info(
            f"Customer {phone} opted out. {len(workflows)} workflow(s) stopped."
        )
        return len(workflows)

    # ─── Simulate Recovery (for demo) ─────────────────────────────────────────

    async def simulate_payment_received(
        self, session: AsyncSession, workflow_id: str
    ) -> Optional[RecoveryWorkflow]:
        """
        Simulate a successful payment for demo purposes.
        Marks the workflow as recovered.
        """
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow:
            return None

        workflow.status = WorkflowStatus.RECOVERED.value
        workflow.amount_recovered = workflow.amount_at_risk
        workflow.resolved_at = datetime.utcnow()

        # If there was a promise, mark it as fulfilled
        if workflow.promise_date:
            await self.promise_tracker.mark_promise_fulfilled(
                session, workflow_id, workflow.amount_at_risk
            )

        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            action="payment_received",
            actor="system",
            category="action",
            details=(
                f"Payment of ₹{workflow.amount_at_risk_inr:.2f} received! "
                f"Recovery successful. Time to recovery: "
                f"{(workflow.resolved_at - workflow.created_at).total_seconds() / 60:.0f} minutes"
            ),
        )
        session.add(audit)

        logger.info(
            f"Payment received for workflow {workflow_id[:8]}: "
            f"₹{workflow.amount_at_risk_inr:.2f}"
        )
        return workflow

    # ─── Helper Methods ───────────────────────────────────────────────────────

    def _get_event_type(self, event: EventUnion) -> EventType:
        if isinstance(event, PaymentEvent):
            return EventType.PAYMENT_FAILED
        elif isinstance(event, CheckoutEvent):
            return EventType.CHECKOUT_ABANDONED
        elif isinstance(event, SubscriptionEvent):
            return EventType.SUBSCRIPTION_FAILED
        elif isinstance(event, InvoiceEvent):
            return EventType.INVOICE_OVERDUE
        raise ValueError(f"Unknown event type: {type(event)}")

    def _extract_customer_info(self, event: EventUnion) -> dict:
        return {
            "customer_id": event.customer_id,
            "customer_name": event.customer_name,
            "customer_email": event.customer_email,
            "customer_phone": event.customer_phone,
        }

    def _extract_amount(self, event: EventUnion) -> int:
        if isinstance(event, CheckoutEvent):
            return event.cart_value
        return event.amount

    def _map_action_to_channel(self, action: Optional[str]) -> RecoveryChannel:
        mapping = {
            "send_payment_link": RecoveryChannel.PAYMENT_LINK,
            "send_recovery_sms": RecoveryChannel.SMS,
            "send_cart_reminder": RecoveryChannel.SMS,
            "send_update_card_link": RecoveryChannel.SMS,
            "suggest_alternative_method": RecoveryChannel.SMS,
            "send_email": RecoveryChannel.EMAIL,
            "send_invoice_reminder": RecoveryChannel.EMAIL,
            "send_whatsapp": RecoveryChannel.WHATSAPP,
            "send_subscription_recovery_sms": RecoveryChannel.SMS,
            "initiate_voice_call": RecoveryChannel.VOICE_CALL,
            "auto_retry": RecoveryChannel.PAYMENT_LINK,
            "escalate_to_human": RecoveryChannel.HUMAN_ESCALATION,
        }
        return mapping.get(action or "", RecoveryChannel.SMS)

    def _channel_to_action(self, channel: RecoveryChannel) -> str:
        mapping = {
            RecoveryChannel.PAYMENT_LINK: "send_payment_link",
            RecoveryChannel.SMS: "send_recovery_sms",
            RecoveryChannel.EMAIL: "send_email",
            RecoveryChannel.WHATSAPP: "send_whatsapp",
            RecoveryChannel.VOICE_CALL: "initiate_voice_call",
            RecoveryChannel.HUMAN_ESCALATION: "escalate_to_human",
        }
        return mapping.get(channel, "send_recovery_sms")
