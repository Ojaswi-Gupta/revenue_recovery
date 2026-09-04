"""
Dashboard API routes — serves the web UI and handles HTMX interactions.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings, WorkflowStatus
from ..models.database import get_db, get_db_session
from ..models.events import (
    CheckoutEvent,
    InvoiceEvent,
    PaymentEvent,
    SubscriptionEvent,
)
from ..models.recovery import AuditLog, RecoveryAction, RecoveryWorkflow

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])

templates = Jinja2Templates(directory="recovrai/templates")


# ─── Dashboard Home ──────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, db: AsyncSession = Depends(get_db)):
    """Main dashboard page with overview stats and recent workflows."""
    stats = await _get_dashboard_stats(db)
    workflows = await _get_recent_workflows(db, limit=25)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "workflows": workflows,
    })


@router.get("/workflows", response_class=HTMLResponse)
async def workflows_page(
    request: Request,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Workflows list page with filtering."""
    workflows = await _get_recent_workflows(db, limit=100, status=status, event_type=event_type)
    stats = await _get_dashboard_stats(db)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "workflows": workflows,
        "filter_status": status,
        "filter_event_type": event_type,
    })


@router.get("/workflow/{workflow_id}", response_class=HTMLResponse)
async def workflow_detail(
    request: Request,
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Detailed view of a single recovery workflow."""
    stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
    result = await db.execute(stmt)
    workflow = result.scalar_one_or_none()

    if not workflow:
        return HTMLResponse("<h1>Workflow not found</h1>", status_code=404)

    # Get all actions for this workflow
    stmt_actions = (
        select(RecoveryAction)
        .where(RecoveryAction.workflow_id == workflow_id)
        .order_by(RecoveryAction.created_at)
    )
    result_actions = await db.execute(stmt_actions)
    actions = list(result_actions.scalars().all())

    # Get audit trail for this workflow
    stmt_audit = (
        select(AuditLog)
        .where(AuditLog.workflow_id == workflow_id)
        .order_by(AuditLog.timestamp)
    )
    result_audit = await db.execute(stmt_audit)
    audit_logs = list(result_audit.scalars().all())

    return templates.TemplateResponse("recovery_detail.html", {
        "request": request,
        "workflow": workflow,
        "actions": actions,
        "audit_logs": audit_logs,
    })


# ─── Audit Trail ─────────────────────────────────────────────────────────────

@router.get("/audit", response_class=HTMLResponse)
async def audit_trail(
    request: Request,
    workflow_id: Optional[str] = None,
    category: Optional[str] = None,
    actor: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Audit trail page with filtering and pagination."""
    stmt = select(AuditLog)

    if workflow_id:
        stmt = stmt.where(AuditLog.workflow_id == workflow_id)
    if category and category != "all":
        stmt = stmt.where(AuditLog.category == category)
    if actor and actor != "all":
        stmt = stmt.where(AuditLog.actor == actor)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    # Paginate
    stmt = stmt.order_by(desc(AuditLog.timestamp))
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    logs = list(result.scalars().all())

    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse("audit_trail.html", {
        "request": request,
        "logs": logs,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "filter_workflow_id": workflow_id or "",
        "filter_category": category or "all",
        "filter_actor": actor or "all",
    })


# ─── API Actions (HTMX) ─────────────────────────────────────────────────────

@router.post("/api/seed")
async def seed_data(request: Request):
    """Seed the database with synthetic data."""
    from ..seed.synthetic_data import seed_database
    await seed_database()
    return HTMLResponse(
        '<div class="text-emerald-400 text-sm p-2">'
        '✅ Database seeded with 105 synthetic records</div>'
    )


@router.post("/api/batch")
async def run_batch(request: Request):
    """Run batch processing on all unprocessed events."""
    from ..services.recovery_orchestrator import RecoveryOrchestrator

    orchestrator = RecoveryOrchestrator()
    async with get_db_session() as session:
        results = await orchestrator.process_batch(session)

    return HTMLResponse(
        f'<div class="text-emerald-400 text-sm p-2">'
        f'✅ Batch complete: {results["total_processed"]} processed, '
        f'{results["recovered"]} recovered, '
        f'₹{results["amount_recovered"] / 100:,.2f} recovered</div>'
    )


@router.post("/api/simulate-recovery/{workflow_id}")
async def simulate_recovery(workflow_id: str):
    """Simulate a payment being received for a workflow."""
    from ..services.recovery_orchestrator import RecoveryOrchestrator

    orchestrator = RecoveryOrchestrator()
    async with get_db_session() as session:
        workflow = await orchestrator.simulate_payment_received(session, workflow_id)

    if workflow:
        return HTMLResponse(
            f'<div class="text-emerald-400 text-sm p-2">'
            f'✅ Payment of ₹{workflow.amount_at_risk_inr:,.2f} simulated for '
            f'{workflow.customer_name}</div>'
        )
    return HTMLResponse(
        '<div class="text-red-400 text-sm p-2">❌ Workflow not found</div>'
    )


@router.post("/api/opt-out/{phone}")
async def opt_out_customer(phone: str):
    """Handle customer opt-out."""
    from ..services.recovery_orchestrator import RecoveryOrchestrator

    orchestrator = RecoveryOrchestrator()
    async with get_db_session() as session:
        count = await orchestrator.handle_customer_opt_out(session, phone)

    return HTMLResponse(
        f'<div class="text-amber-400 text-sm p-2">'
        f'⚠️ Customer opted out. {count} workflow(s) stopped.</div>'
    )


@router.post("/api/workflow/{workflow_id}/escalate")
async def escalate_workflow(workflow_id: str):
    """Manually escalate a workflow to human review."""
    from ..services.recovery_orchestrator import RecoveryOrchestrator

    orchestrator = RecoveryOrchestrator()
    async with get_db_session() as session:
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow:
            return HTMLResponse(
                '<div class="text-red-400 text-sm p-2">❌ Workflow not found</div>'
            )

        workflow = await orchestrator._escalate_workflow(
            session, workflow, "Manually escalated by operator"
        )

    return HTMLResponse(
        f'<div class="text-amber-400 text-sm p-2">'
        f'⚠️ Workflow {workflow_id[:8]} escalated to human agent.</div>'
    )


@router.post("/api/workflow/{workflow_id}/stop")
async def stop_workflow(workflow_id: str):
    """Manually stop a workflow (compliance override)."""
    async with get_db_session() as session:
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow:
            return HTMLResponse(
                '<div class="text-red-400 text-sm p-2">❌ Workflow not found</div>'
            )

        workflow.status = "stopped_compliance"
        workflow.stopped_reason = "Manually stopped by operator"

        from ..models.recovery import AuditLog
        import uuid
        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            action="workflow_manually_stopped",
            actor="operator",
            category="compliance",
            details=f"Workflow {workflow_id[:8]} manually stopped by operator.",
        )
        session.add(audit)

    return HTMLResponse(
        f'<div class="text-gray-400 text-sm p-2">'
        f'🛑 Workflow {workflow_id[:8]} stopped.</div>'
    )


@router.post("/api/workflow/{workflow_id}/send-email")
async def send_email_reminder(workflow_id: str, to_vendor: bool = False):
    """
    Vendor 1-click trigger: Dispatch a professional email reminder
    with a live Razorpay payment link directly to the customer (or vendor test inbox).
    """
    from ..models.events import InvoiceEvent
    from ..services.recovery_orchestrator import RecoveryOrchestrator
    import uuid

    orchestrator = RecoveryOrchestrator()
    settings = get_settings()

    async with get_db_session() as session:
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow:
            return HTMLResponse(
                '<div class="text-red-400 text-sm p-2">❌ Workflow not found</div>',
                status_code=404,
            )

        # 1. Ensure a Razorpay payment link exists
        try:
            payment_link_url = await orchestrator._ensure_payment_link(session, workflow)
        except Exception as e:
            logger.warning(f"Could not generate Razorpay payment link: {e}")
            payment_link_url = workflow.payment_link_url or f"https://rzp.io/l/inv_{workflow.id[:8]}"

        # 2. Extract context if linked to an InvoiceEvent
        company_name = "Vendor Partner"
        invoice_number = f"INV-{workflow.id[:8].upper()}"
        days_overdue = 14
        amount_due_inr = workflow.amount_at_risk_inr

        if workflow.event_type == "invoice_overdue":
            stmt_inv = select(InvoiceEvent).where(InvoiceEvent.id == workflow.event_id)
            inv_result = await session.execute(stmt_inv)
            inv = inv_result.scalar_one_or_none()
            if inv:
                company_name = inv.company_name or company_name
                invoice_number = inv.invoice_number or invoice_number
                days_overdue = getattr(inv, "days_overdue", 14)
                amount_due_inr = inv.amount_inr

        # 3. Determine target recipient: customer email vs vendor test email
        target_email = settings.smtp_user if (to_vendor and settings.smtp_user) else workflow.customer_email

        # 4. Build rich HTML email
        subject, plain_text, html_body = orchestrator.notification_service.build_invoice_html_email(
            customer_name=workflow.customer_name,
            company_name=company_name,
            invoice_number=invoice_number,
            amount_due_inr=amount_due_inr,
            days_overdue=days_overdue,
            payment_link_url=payment_link_url,
        )

        if to_vendor:
            subject = f"[TEST PREVIEW] {subject}"

        # 5. Dispatch email
        action = await orchestrator.notification_service.send_email(
            email=target_email,
            subject=subject,
            body=plain_text,
            workflow_id=workflow.id,
            html_body=html_body,
        )
        session.add(action)

        # 6. Update workflow state & metrics
        workflow.contact_attempts += 1
        workflow.current_channel = "email"
        workflow.last_contact_at = datetime.utcnow()
        if workflow.status in ("detected", "diagnosing", "intervention_planned"):
            workflow.status = "executing"

        # 7. Audit trail
        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action="vendor_email_reminder_sent",
            actor="vendor_operator",
            category="action",
            details=(
                f"Vendor dispatched email reminder to {target_email} "
                f"for ₹{amount_due_inr:,.2f} (Invoice {invoice_number}). Razorpay link: {payment_link_url}"
            ),
            metadata_json=json.dumps({
                "to": target_email,
                "invoice_number": invoice_number,
                "amount_inr": amount_due_inr,
                "payment_link": payment_link_url,
                "attempt": workflow.contact_attempts,
                "status": action.status,
            }),
        )
        session.add(audit)

    if action.status == "failed":
        return HTMLResponse(
            f'<div class="bg-red-500/10 border border-red-500/30 text-red-400 text-xs px-3 py-2 rounded-md shadow-sm">'
            f'❌ Email delivery failed: {action.error_message}'
            f'</div>'
        )

    return HTMLResponse(
        f'<div class="bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs px-3 py-2 rounded-md shadow-sm">'
        f'✉️ Reminder dispatched to <strong>{target_email}</strong> '
        f'(Attempt #{workflow.contact_attempts}) with payment link.'
        f'</div>'
    )


# ─── HTMX Partials ──────────────────────────────────────────────────────────


@router.get("/partials/workflows-table")
async def workflows_table_partial(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """HTMX partial: just the workflows table rows for auto-refresh."""
    workflows = await _get_recent_workflows(db, limit=25)
    stats = await _get_dashboard_stats(db)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "workflows": workflows,
    })


# ─── Report ──────────────────────────────────────────────────────────────────

@router.get("/report", response_class=HTMLResponse)
async def batch_report(request: Request, db: AsyncSession = Depends(get_db)):
    """Batch report page with detailed recovery metrics."""
    from ..models.metrics import RecoveryMetric

    stmt = select(RecoveryMetric).order_by(desc(RecoveryMetric.generated_at))
    result = await db.execute(stmt)
    metrics = list(result.scalars().all())

    stats = await _get_dashboard_stats(db)

    # Get channel effectiveness
    channel_stats = await _get_channel_stats(db)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "workflows": [],
        "metrics": metrics,
        "channel_stats": channel_stats,
        "show_report": True,
    })


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _get_dashboard_stats(db: AsyncSession) -> dict:
    """Calculate dashboard statistics."""
    # Total workflows
    stmt = select(func.count()).select_from(RecoveryWorkflow)
    total = (await db.execute(stmt)).scalar() or 0

    # Recovered
    stmt = select(func.count()).select_from(RecoveryWorkflow).where(
        RecoveryWorkflow.status == WorkflowStatus.RECOVERED.value
    )
    recovered = (await db.execute(stmt)).scalar() or 0

    # Active (non-terminal)
    stmt = select(func.count()).select_from(RecoveryWorkflow).where(
        ~RecoveryWorkflow.status.in_([
            WorkflowStatus.RECOVERED.value,
            WorkflowStatus.FAILED.value,
            WorkflowStatus.ESCALATED.value,
            WorkflowStatus.STOPPED_COMPLIANCE.value,
        ])
    )
    active = (await db.execute(stmt)).scalar() or 0

    # Financial totals
    stmt = select(
        func.sum(RecoveryWorkflow.amount_at_risk),
        func.sum(RecoveryWorkflow.amount_recovered),
    )
    result = await db.execute(stmt)
    row = result.one()
    total_at_risk = (row[0] or 0) / 100
    total_recovered = (row[1] or 0) / 100
    recovery_rate = (total_recovered / total_at_risk * 100) if total_at_risk > 0 else 0

    # Per event type counts
    event_type_counts = {}
    for event_type in ["payment_failed", "checkout_abandoned", "subscription_failed", "invoice_overdue"]:
        stmt = select(func.count()).select_from(RecoveryWorkflow).where(
            RecoveryWorkflow.event_type == event_type
        )
        event_type_counts[event_type] = (await db.execute(stmt)).scalar() or 0

    # Escalated count
    stmt = select(func.count()).select_from(RecoveryWorkflow).where(
        RecoveryWorkflow.status == WorkflowStatus.ESCALATED.value
    )
    escalated = (await db.execute(stmt)).scalar() or 0

    return {
        "total_workflows": total,
        "recovered_count": recovered,
        "active_workflows": active,
        "escalated_count": escalated,
        "total_at_risk_inr": total_at_risk,
        "total_recovered_inr": total_recovered,
        "recovery_rate": round(recovery_rate, 1),
        "payment_count": event_type_counts.get("payment_failed", 0),
        "checkout_count": event_type_counts.get("checkout_abandoned", 0),
        "subscription_count": event_type_counts.get("subscription_failed", 0),
        "invoice_count": event_type_counts.get("invoice_overdue", 0),
    }


async def _get_recent_workflows(
    db: AsyncSession,
    limit: int = 25,
    status: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[RecoveryWorkflow]:
    """Fetch recent recovery workflows with optional filtering."""
    stmt = select(RecoveryWorkflow)

    if status:
        stmt = stmt.where(RecoveryWorkflow.status == status)
    if event_type:
        stmt = stmt.where(RecoveryWorkflow.event_type == event_type)

    stmt = stmt.order_by(desc(RecoveryWorkflow.created_at)).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _get_channel_stats(db: AsyncSession) -> list[dict]:
    """Calculate per-channel recovery effectiveness."""
    channels = ["sms", "email", "whatsapp", "voice_call", "payment_link"]
    stats = []

    for channel in channels:
        # Total actions on this channel
        stmt_total = select(func.count()).select_from(RecoveryAction).where(
            RecoveryAction.channel == channel
        )
        total = (await db.execute(stmt_total)).scalar() or 0

        # Successful actions on this channel
        stmt_success = select(func.count()).select_from(RecoveryAction).where(
            and_(
                RecoveryAction.channel == channel,
                RecoveryAction.status == "success",
            )
        )
        success = (await db.execute(stmt_success)).scalar() or 0

        stats.append({
            "channel": channel,
            "total_actions": total,
            "successful_actions": success,
            "success_rate": round((success / total * 100) if total > 0 else 0, 1),
        })

    return stats
