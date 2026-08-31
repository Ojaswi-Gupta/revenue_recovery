"""
Metrics API — provides structured recovery metrics and CSV export.
"""

import csv
import io
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import WorkflowStatus
from ..models.database import get_db
from ..models.metrics import RecoveryMetric
from ..models.recovery import AuditLog, RecoveryAction, RecoveryWorkflow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/summary")
async def get_metrics_summary(db: AsyncSession = Depends(get_db)):
    """Get a comprehensive metrics summary for the dashboard."""
    # Overall stats
    stmt = select(
        func.count(RecoveryWorkflow.id),
        func.sum(RecoveryWorkflow.amount_at_risk),
        func.sum(RecoveryWorkflow.amount_recovered),
    )
    result = await db.execute(stmt)
    row = result.one()
    total = row[0] or 0
    at_risk = (row[1] or 0) / 100
    recovered = (row[2] or 0) / 100

    # By status
    status_counts = {}
    for status in WorkflowStatus:
        stmt = select(func.count()).select_from(RecoveryWorkflow).where(
            RecoveryWorkflow.status == status.value
        )
        count = (await db.execute(stmt)).scalar() or 0
        status_counts[status.value] = count

    # By event type
    event_type_stats = {}
    for event_type in ["payment_failed", "checkout_abandoned", "subscription_failed", "invoice_overdue"]:
        stmt = select(
            func.count(RecoveryWorkflow.id),
            func.sum(RecoveryWorkflow.amount_at_risk),
            func.sum(RecoveryWorkflow.amount_recovered),
        ).where(RecoveryWorkflow.event_type == event_type)
        result = await db.execute(stmt)
        row = result.one()
        event_type_stats[event_type] = {
            "count": row[0] or 0,
            "amount_at_risk_inr": (row[1] or 0) / 100,
            "amount_recovered_inr": (row[2] or 0) / 100,
            "recovery_rate": round(
                ((row[2] or 0) / (row[1] or 1)) * 100, 1
            ),
        }

    # Mean time to recovery
    stmt = select(RecoveryWorkflow).where(
        RecoveryWorkflow.status == WorkflowStatus.RECOVERED.value,
        RecoveryWorkflow.resolved_at.isnot(None),
    )
    result = await db.execute(stmt)
    recovered_workflows = list(result.scalars().all())

    mttr_minutes = 0.0
    if recovered_workflows:
        total_seconds = sum(
            (w.resolved_at - w.created_at).total_seconds()
            for w in recovered_workflows
            if w.resolved_at and w.created_at
        )
        mttr_minutes = (total_seconds / len(recovered_workflows)) / 60

    return {
        "overview": {
            "total_workflows": total,
            "total_at_risk_inr": round(at_risk, 2),
            "total_recovered_inr": round(recovered, 2),
            "recovery_rate_percent": round(
                (recovered / at_risk * 100) if at_risk > 0 else 0, 1
            ),
            "mean_time_to_recovery_minutes": round(mttr_minutes, 1),
        },
        "by_status": status_counts,
        "by_event_type": event_type_stats,
    }


@router.get("/export/csv")
async def export_csv(db: AsyncSession = Depends(get_db)):
    """Export all recovery workflows as a CSV file."""
    stmt = select(RecoveryWorkflow).order_by(desc(RecoveryWorkflow.created_at))
    result = await db.execute(stmt)
    workflows = list(result.scalars().all())

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Workflow ID",
        "Event Type",
        "Customer Name",
        "Customer Phone",
        "Customer Email",
        "Amount at Risk (INR)",
        "Amount Recovered (INR)",
        "Recovery Rate (%)",
        "Status",
        "Root Cause",
        "Diagnosis Rule",
        "Confidence",
        "Urgency",
        "Recommended Action",
        "Channel",
        "Contact Attempts",
        "Promise Date",
        "Promise Fulfilled",
        "Created At",
        "Resolved At",
        "Stopped Reason",
    ])

    for w in workflows:
        writer.writerow([
            w.id,
            w.event_type,
            w.customer_name,
            w.customer_phone,
            w.customer_email,
            f"{w.amount_at_risk_inr:.2f}",
            f"{w.amount_recovered_inr:.2f}",
            f"{w.recovery_rate:.1f}",
            w.status,
            w.root_cause or "",
            w.diagnosis_rule or "",
            f"{w.confidence:.2f}",
            w.urgency,
            w.recommended_action or "",
            w.current_channel or "",
            w.contact_attempts,
            w.promise_date.isoformat() if w.promise_date else "",
            w.promise_fulfilled,
            w.created_at.isoformat() if w.created_at else "",
            w.resolved_at.isoformat() if w.resolved_at else "",
            w.stopped_reason or "",
        ])

    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=recovrai_report_{timestamp}.csv"
        },
    )


@router.get("/audit/export/csv")
async def export_audit_csv(db: AsyncSession = Depends(get_db)):
    """Export the full audit trail as a CSV file."""
    stmt = select(AuditLog).order_by(desc(AuditLog.timestamp))
    result = await db.execute(stmt)
    logs = list(result.scalars().all())

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Timestamp",
        "Workflow ID",
        "Action",
        "Actor",
        "Category",
        "Details",
    ])

    for log in logs:
        writer.writerow([
            log.timestamp.isoformat() if log.timestamp else "",
            log.workflow_id or "",
            log.action,
            log.actor,
            log.category,
            log.details,
        ])

    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=recovrai_audit_{timestamp}.csv"
        },
    )
