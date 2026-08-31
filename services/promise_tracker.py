"""
Promise-to-Pay Tracker — tracks customer commitments and triggers follow-ups.
When a customer says "I'll pay tomorrow", this system holds them to it.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings, WorkflowStatus
from ..models.recovery import RecoveryWorkflow, AuditLog

logger = logging.getLogger(__name__)


class PromiseTracker:
    """
    Manages promise-to-pay commitments extracted from customer conversations.
    Automatically identifies overdue promises and triggers follow-up workflows.
    """

    def __init__(self):
        self.settings = get_settings()

    async def record_promise(
        self,
        session: AsyncSession,
        workflow_id: str,
        promise_date: datetime,
        promise_amount: Optional[int] = None,
        source: str = "voice_call",
    ) -> None:
        """
        Record a promise-to-pay commitment from the customer.
        
        Args:
            session: Database session
            workflow_id: The recovery workflow ID
            promise_date: When the customer promised to pay
            promise_amount: Amount promised (in paise), or None for full amount
            source: Where the promise was made (voice_call, sms_reply, etc.)
        """
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow:
            logger.error(f"Workflow {workflow_id} not found for promise recording")
            return

        workflow.status = WorkflowStatus.AWAITING_PROMISE.value
        workflow.promise_date = promise_date
        workflow.promise_amount = promise_amount or workflow.amount_at_risk
        workflow.promise_fulfilled = False

        # Schedule next action for the day after the promise date
        workflow.next_action_at = promise_date + timedelta(hours=12)

        # Log to audit trail
        audit_log = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            action="promise_recorded",
            actor="promise_tracker",
            category="decision",
            details=(
                f"Customer promised to pay ₹{(promise_amount or workflow.amount_at_risk) / 100:.2f} "
                f"by {promise_date.strftime('%Y-%m-%d %H:%M')}. Source: {source}. "
                f"Follow-up scheduled for {workflow.next_action_at.strftime('%Y-%m-%d %H:%M')}"
            ),
            metadata_json=json.dumps({
                "promise_date": promise_date.isoformat(),
                "promise_amount_paise": promise_amount or workflow.amount_at_risk,
                "source": source,
                "follow_up_at": workflow.next_action_at.isoformat(),
            }),
        )
        session.add(audit_log)

        logger.info(
            f"Promise recorded for workflow {workflow_id[:8]}: "
            f"₹{(promise_amount or workflow.amount_at_risk) / 100:.2f} "
            f"by {promise_date.strftime('%Y-%m-%d')}"
        )

    async def check_overdue_promises(
        self,
        session: AsyncSession,
        now: Optional[datetime] = None,
    ) -> list[RecoveryWorkflow]:
        """
        Find all promises that are past their due date and not yet fulfilled.
        
        Returns:
            List of workflows with overdue promises
        """
        now = now or datetime.utcnow()

        stmt = select(RecoveryWorkflow).where(
            and_(
                RecoveryWorkflow.status == WorkflowStatus.AWAITING_PROMISE.value,
                RecoveryWorkflow.promise_date < now,
                RecoveryWorkflow.promise_fulfilled == False,
            )
        )
        result = await session.execute(stmt)
        overdue = list(result.scalars().all())

        if overdue:
            logger.info(f"Found {len(overdue)} overdue promises")
            for wf in overdue:
                audit_log = AuditLog(
                    id=str(uuid.uuid4()),
                    workflow_id=wf.id,
                    action="promise_overdue",
                    actor="promise_tracker",
                    category="action",
                    details=(
                        f"Promise to pay ₹{wf.promise_amount / 100:.2f} "
                        f"by {wf.promise_date.strftime('%Y-%m-%d')} is overdue. "
                        f"Triggering follow-up."
                    ),
                )
                session.add(audit_log)

        return overdue

    async def mark_promise_fulfilled(
        self,
        session: AsyncSession,
        workflow_id: str,
        amount_recovered: int,
    ) -> None:
        """
        Mark a promise as fulfilled when payment is received.
        
        Args:
            session: Database session
            workflow_id: The recovery workflow ID
            amount_recovered: Amount actually received (in paise)
        """
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow:
            logger.error(f"Workflow {workflow_id} not found")
            return

        workflow.promise_fulfilled = True
        workflow.amount_recovered = amount_recovered
        workflow.status = WorkflowStatus.RECOVERED.value
        workflow.resolved_at = datetime.utcnow()

        audit_log = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            action="promise_fulfilled",
            actor="promise_tracker",
            category="action",
            details=(
                f"Promise fulfilled! ₹{amount_recovered / 100:.2f} received. "
                f"Recovery rate: {workflow.recovery_rate:.1f}%"
            ),
            metadata_json=json.dumps({
                "amount_recovered_paise": amount_recovered,
                "recovery_rate": workflow.recovery_rate,
            }),
        )
        session.add(audit_log)

        logger.info(
            f"Promise fulfilled for workflow {workflow_id[:8]}: "
            f"₹{amount_recovered / 100:.2f}"
        )

    async def get_promise_stats(self, session: AsyncSession) -> dict:
        """Get aggregate promise-to-pay statistics."""
        # Total promises
        stmt_total = select(RecoveryWorkflow).where(
            RecoveryWorkflow.promise_date.isnot(None)
        )
        result = await session.execute(stmt_total)
        all_promises = list(result.scalars().all())

        # Fulfilled promises
        fulfilled = [w for w in all_promises if w.promise_fulfilled]

        # Overdue (unfulfilled and past date)
        now = datetime.utcnow()
        overdue = [
            w for w in all_promises
            if not w.promise_fulfilled and w.promise_date and w.promise_date < now
        ]

        # Pending (not yet due)
        pending = [
            w for w in all_promises
            if not w.promise_fulfilled and w.promise_date and w.promise_date >= now
        ]

        total_promised_amount = sum(w.promise_amount or 0 for w in all_promises)
        fulfilled_amount = sum(w.amount_recovered for w in fulfilled)

        return {
            "total_promises": len(all_promises),
            "fulfilled": len(fulfilled),
            "overdue": len(overdue),
            "pending": len(pending),
            "fulfillment_rate": (
                (len(fulfilled) / len(all_promises) * 100) if all_promises else 0
            ),
            "total_promised_inr": total_promised_amount / 100,
            "fulfilled_amount_inr": fulfilled_amount / 100,
        }
