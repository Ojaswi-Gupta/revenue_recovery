"""
Dashboard API routes — serves the web UI and handles HTMX interactions.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Form, HTTPException
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
    channel_stats = await _get_channel_stats(db)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "workflows": workflows,
        "channel_stats": channel_stats,
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


# ─── Hosted Customer Checkout Portal ───────────────────────────────────────

@router.get("/pay/{workflow_id}", response_class=HTMLResponse)
async def customer_checkout_page(request: Request, workflow_id: str, db: AsyncSession = Depends(get_db)):
    """
    Customer-facing hosted payment portal.
    Accessible from the email reminder link to settle outstanding invoices.
    """
    from ..models.events import InvoiceEvent

    stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
    result = await db.execute(stmt)
    workflow = result.scalar_one_or_none()

    if not workflow:
        return HTMLResponse("<div style='font-family:sans-serif;padding:40px;text-align:center;'><h2>Invoice or Payment Link Not Found</h2><p>This recovery link may have expired or was removed.</p></div>", status_code=404)

    company_name = "Vendor Partner"
    invoice_number = f"INV-{workflow.id[:8].upper()}"
    days_overdue = 14

    if workflow.event_type == "invoice_overdue":
        stmt_inv = select(InvoiceEvent).where(InvoiceEvent.id == workflow.event_id)
        inv = (await db.execute(stmt_inv)).scalar_one_or_none()
        if inv:
            company_name = inv.company_name or company_name
            invoice_number = inv.invoice_number or invoice_number
            days_overdue = getattr(inv, "days_overdue", 14)

    settings = get_settings()

    return templates.TemplateResponse("checkout.html", {
        "request": request,
        "workflow": workflow,
        "company_name": company_name,
        "invoice_number": invoice_number,
        "days_overdue": days_overdue,
        "razorpay_key_id": settings.razorpay_key_id,
    })


@router.post("/api/pay/{workflow_id}")
async def process_checkout_payment(workflow_id: str):
    """
    Customer payment confirmation from the hosted checkout portal.
    Simulates live settlement, marks workflow as recovered, and updates ledger.
    """
    from ..services.recovery_orchestrator import RecoveryOrchestrator
    import random

    orchestrator = RecoveryOrchestrator()
    async with get_db_session() as session:
        workflow = await orchestrator.simulate_payment_received(session, workflow_id)

    if not workflow:
        return HTMLResponse('<div class="text-red-400 p-4">Workflow not found</div>', status_code=404)

    txn_id = f"pay_rzp_{random.randint(10000000, 99999999)}"

    return HTMLResponse(f"""
    <div class="p-8 text-center bg-slate-900/90 rounded-2xl border border-emerald-500/40 shadow-xl">
        <div class="w-16 h-16 bg-emerald-500/20 text-emerald-400 rounded-full flex items-center justify-center mx-auto mb-4 text-3xl font-bold">✓</div>
        <h3 class="text-2xl font-bold text-white mb-1">Payment Successful!</h3>
        <p class="text-sm text-emerald-400 font-medium mb-6">₹{workflow.amount_at_risk_inr:,.2f} settled securely via Razorpay</p>
        <div class="bg-slate-950/80 p-4 rounded-xl border border-slate-800 text-xs text-slate-400 space-y-2 mb-6 max-w-sm mx-auto font-mono text-left">
            <div class="flex justify-between"><span>Payment ID:</span><span class="text-slate-200">{txn_id}</span></div>
            <div class="flex justify-between"><span>Customer:</span><span class="text-slate-200">{workflow.customer_name}</span></div>
            <div class="flex justify-between"><span>Status:</span><span class="text-emerald-400 font-bold">SETTLED</span></div>
            <div class="flex justify-between"><span>Ledger:</span><span class="text-blue-400 font-bold">RECOVERED</span></div>
        </div>
        <p class="text-xs text-slate-400 mb-6">A confirmation receipt has been issued and the vendor's ledger has been updated in real-time.</p>
        <a href="/workflow/{workflow.id}" class="inline-block text-xs bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-5 py-2.5 rounded-lg transition-colors shadow-md">
            View Live Workflow Audit Trail →
        </a>
    </div>
    """)


@router.post("/api/workflow/{workflow_id}/send-email")
async def send_email_reminder(request: Request, workflow_id: str, to_vendor: bool = False):
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

        # 1. Primary link: Hosted high-converting customer checkout portal
        payment_link_url = f"{request.base_url}pay/{workflow.id}"

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


@router.post("/api/workflow/{workflow_id}/call")
async def trigger_voice_call(workflow_id: str, to_verified: bool = False, to_test: bool = False):
    """
    Trigger an automated outbound voice call via Twilio.
    If to_verified=True or to_test=True, dials the developer's verified phone number (+917991924011).
    """
    from ..services.recovery_orchestrator import RecoveryOrchestrator
    import uuid

    orchestrator = RecoveryOrchestrator()
    settings = get_settings()

    async with get_db_session() as session:
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow:
            return HTMLResponse('<div class="text-red-400 text-xs p-2">Workflow not found</div>', status_code=404)

        target_phone = settings.test_phone_number if (to_verified or to_test) else workflow.customer_phone
        first_name = workflow.customer_name.split()[0]
        amount_val = int(workflow.amount_at_risk_inr)
        event_name = workflow.event_type.replace('_', ' ')
        spoken_message = (
            f"Main Alfeus Tech se bol rahi hoon. Aapka {amount_val} rupees ka payment process nahi ho paya."
        )

        action = await orchestrator.notification_service.make_voice_call(
            phone=target_phone,
            message=spoken_message,
            workflow_id=workflow.id,
        )
        session.add(action)

        workflow.contact_attempts += 1
        workflow.current_channel = "voice_call"
        workflow.last_contact_at = datetime.utcnow()
        if workflow.status in ("detected", "diagnosing", "intervention_planned"):
            workflow.status = "executing"

        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action="twilio_voice_call_dispatched",
            actor="vendor_operator",
            category="action",
            details=f"Twilio Voice call placed to {target_phone} for workflow {workflow.id[:8]}",
            metadata_json=json.dumps({"to": target_phone, "status": action.status}),
        )
        session.add(audit)

    if action.status == "failed":
        return HTMLResponse(
            f'<div class="bg-red-500/10 border border-red-500/30 text-red-400 text-xs px-3 py-2 rounded-md shadow-sm">'
            f'❌ Voice Call failed: {action.error_message}'
            f'</div>'
        )

    return HTMLResponse(
        f'<div class="bg-purple-500/10 border border-purple-500/30 text-purple-300 text-xs px-3 py-2 rounded-md shadow-sm">'
        f'📞 Twilio voice call ringing <strong>{target_phone}</strong>!'
        f'</div>'
    )


@router.post("/api/workflow/{workflow_id}/send-sms")
async def send_sms_reminder(
    request: Request,
    workflow_id: str,
    to_test: bool = False,
    to_verified: bool = False,
):
    """
    Trigger SMS dispatch via Twilio.
    If to_test=True or to_verified=True, sends to configured test phone (+917991924011).
    """
    from ..services.recovery_orchestrator import RecoveryOrchestrator
    import uuid

    orchestrator = RecoveryOrchestrator()
    settings = get_settings()

    async with get_db_session() as session:
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow:
            return HTMLResponse('<div class="text-red-400 text-xs p-2">Workflow not found</div>', status_code=404)

        target_phone = settings.test_phone_number if (to_test or to_verified) else workflow.customer_phone
        payment_link_url = f"{request.base_url}pay/{workflow.id}"

        message = orchestrator.notification_service.build_recovery_sms(
            customer_name=workflow.customer_name,
            amount_inr=workflow.amount_at_risk_inr,
            payment_link_url=payment_link_url,
            failure_reason=workflow.root_cause or "",
        )

        action = await orchestrator.notification_service.send_sms(
            phone=target_phone,
            message=message,
            workflow_id=workflow.id,
        )
        session.add(action)

        workflow.contact_attempts += 1
        workflow.current_channel = "sms"
        workflow.last_contact_at = datetime.utcnow()
        if workflow.status in ("detected", "diagnosing", "intervention_planned"):
            workflow.status = "executing"

        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action="twilio_sms_dispatched",
            actor="vendor_operator",
            category="action",
            details=f"Twilio SMS reminder sent to {target_phone} for ₹{workflow.amount_at_risk_inr:.2f}",
            metadata_json=json.dumps({"to": target_phone, "status": action.status}),
        )
        session.add(audit)

    if action.status == "failed":
        return HTMLResponse(
            f'<div class="bg-red-500/10 border border-red-500/30 text-red-400 text-xs px-3 py-2 rounded-md shadow-sm">'
            f'❌ SMS delivery failed: {action.error_message}'
            f'</div>'
        )

    return HTMLResponse(
        f'<div class="bg-sky-500/10 border border-sky-500/30 text-sky-300 text-xs px-3 py-2 rounded-md shadow-sm">'
        f'💬 SMS dispatched via Twilio to <strong>{target_phone}</strong> with payment link!'
        f'</div>'
    )


@router.post("/api/workflow/{workflow_id}/send-whatsapp")
async def send_whatsapp_reminder(
    request: Request,
    workflow_id: str,
    to_test: bool = False,
    to_verified: bool = False,
):
    """
    Trigger WhatsApp reminder via Twilio API and provide 1-click wa.me instant link.
    If to_test=True or to_verified=True, sends to configured test phone (+917991924011).
    """
    from ..services.recovery_orchestrator import RecoveryOrchestrator
    import uuid

    orchestrator = RecoveryOrchestrator()
    settings = get_settings()

    async with get_db_session() as session:
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow:
            return HTMLResponse('<div class="text-red-400 text-xs p-2">Workflow not found</div>', status_code=404)

        target_phone = settings.test_phone_number if (to_test or to_verified) else workflow.customer_phone
        payment_link_url = f"{request.base_url}pay/{workflow.id}"
        first_name = workflow.customer_name.split()[0]

        message = (
            f"Namaste {first_name}! Your payment of ₹{workflow.amount_at_risk_inr:,.2f} "
            f"could not be processed for {workflow.event_type.replace('_', ' ')}. "
            f"Please complete it securely here: {payment_link_url} - RecovrAI"
        )
        wa_link = orchestrator.notification_service.build_whatsapp_direct_link(target_phone, message)

        action = await orchestrator.notification_service.send_whatsapp(
            phone=target_phone,
            message=message,
            workflow_id=workflow.id,
        )
        session.add(action)

        workflow.contact_attempts += 1
        workflow.current_channel = "whatsapp"
        workflow.last_contact_at = datetime.utcnow()
        if workflow.status in ("detected", "diagnosing", "intervention_planned"):
            workflow.status = "executing"

        audit = AuditLog(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            action="whatsapp_reminder_dispatched",
            actor="vendor_operator",
            category="action",
            details=f"WhatsApp reminder dispatched to {target_phone}",
            metadata_json=json.dumps({"to": target_phone, "status": action.status, "wa_link": wa_link}),
        )
        session.add(audit)

    if action.status == "success":
        dispatch_badge = f'<span class="text-emerald-400 font-medium">✅ Twilio API Sent</span>'
        hint = ""
    else:
        dispatch_badge = f'<span class="text-amber-400 font-medium">⚠️ Twilio Sandbox Join Required</span>'
        hint = (
            f'<div class="text-[11px] text-gray-300 mt-1.5 pt-1.5 border-t border-emerald-500/20">'
            f'ℹ️ Meta requires a 1-time opt-in for Twilio WhatsApp test accounts. '
            f'Click <strong>Open WhatsApp Chat</strong> on the right to test right away, or send your Twilio console join code to <code>+1 415 523 8886</code>.'
            f'</div>'
        )

    return HTMLResponse(
        f'<div class="bg-emerald-950/60 border border-emerald-500/40 text-emerald-200 text-xs px-3.5 py-2.5 rounded-lg shadow-md">'
        f'<div class="flex items-center justify-between gap-3">'
        f'<div>📱 WhatsApp to <strong>{target_phone}</strong>: {dispatch_badge}</div>'
        f'<a href="{wa_link}" target="_blank" class="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded font-medium text-xs shadow inline-flex items-center gap-1 transition-colors whitespace-nowrap">'
        f'<span>💬 Open WhatsApp Chat</span> →'
        f'</a>'
        f'</div>'
        f'{hint}'
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


@router.post("/api/promise/{workflow_id}")
async def submit_promise(
    workflow_id: str,
    promise_date: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """Handle customer submitting a promise to pay."""
    stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
    result = await db.execute(stmt)
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if workflow.is_terminal:
        return HTMLResponse(content="<div class='text-red-400 font-bold'>This workflow is already completed.</div>")

    workflow.status = "awaiting_promise"
    workflow.promise_date = datetime.strptime(promise_date, "%Y-%m-%d").date()
    
    audit = AuditLog(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        action="customer_promised_payment",
        actor="customer",
        category="action",
        details=f"Customer promised to pay on {promise_date}. Recovery paused.",
    )
    db.add(audit)
    
    await db.commit()
    
    html = f"""
    <div class="flex flex-col items-center py-6 text-center">
        <div class="w-16 h-16 bg-blue-500/20 text-blue-400 rounded-full flex items-center justify-center mb-4">
            <svg class="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
            </svg>
        </div>
        <h3 class="text-xl font-bold text-white mb-2">Promise Recorded</h3>
        <p class="text-slate-400 text-sm">Thank you. We have recorded your promise to pay on <strong class="text-white">{promise_date}</strong>. Our recovery reminders are now paused.</p>
    </div>
    """
    return HTMLResponse(content=html)


@router.post("/api/test_real_link")
async def test_real_link(db: AsyncSession = Depends(get_db)):
    """Generate a single real Razorpay payment link to test the flow."""
    from ..services.recovery_orchestrator import RecoveryOrchestrator
    orchestrator = RecoveryOrchestrator()
    
    event = PaymentEvent(
        id=str(uuid.uuid4()),
        payment_id=f"pay_{uuid.uuid4().hex[:14]}",
        order_id="",
        customer_id=f"cust_{uuid.uuid4().hex[:8]}",
        customer_name="Test User",
        customer_email="ojaswigupta317@gmail.com",
        customer_phone="+917991924011",
        amount=19900, # Rs. 199
        currency="INR",
        status="failed",
        method="card",
        error_code="BAD_REQUEST_ERROR",
        error_description="Payment failed for testing real links",
    )
    
    db.add(event)
    workflow = await orchestrator.ingest_event(db, event)
    await orchestrator.diagnose_workflow(db, workflow, event)
    # create_on_razorpay=True is the default now!
    workflow = await orchestrator.execute_intervention(db, workflow)
    
    await db.commit()
    return HTMLResponse(
        content=f"<div class='p-4 bg-emerald-900/50 text-emerald-400 rounded-lg'>✅ Triggered real Razorpay link generation. Check your email/WhatsApp!</div>"
    )

@router.post("/api/test_all_channels")
async def test_all_channels(db: AsyncSession = Depends(get_db)):
    """Generate a single real Razorpay payment link and immediately trigger Email, WhatsApp, and Voice."""
    from ..services.recovery_orchestrator import RecoveryOrchestrator
    from ..services.notification import NotificationService
    import asyncio
    
    orchestrator = RecoveryOrchestrator()
    notification_service = NotificationService()
    
    event = PaymentEvent(
        id=str(uuid.uuid4()),
        payment_id=f"pay_{uuid.uuid4().hex[:14]}",
        order_id="",
        customer_id=f"cust_{uuid.uuid4().hex[:8]}",
        customer_name="Test User",
        customer_email="ojaswigupta317@gmail.com",
        customer_phone="+917991924011",
        amount=19900,
        currency="INR",
        status="failed",
        method="card",
        error_code="BAD_REQUEST_ERROR",
        error_description="Testing all 3 channels instantly",
    )
    
    db.add(event)
    workflow = await orchestrator.ingest_event(db, event)
    await orchestrator.diagnose_workflow(db, workflow, event)
    # This generates the Razorpay link via _ensure_payment_link logic
    workflow = await orchestrator.execute_intervention(db, workflow, create_on_razorpay=True)
    await db.commit()
    
    # Send all 3 instantly to the vendor's test credentials
    payment_link = workflow.payment_link_url or f"https://your-domain.com/pay/{workflow.id}"
    
    try:
        # 1. Email
        subject, body, html_body = notification_service.build_invoice_html_email(
            customer_name="Test User",
            company_name="Alfeus Tech",
            invoice_number=f"INV-{uuid.uuid4().hex[:6].upper()}",
            amount_due_inr=199.00,
            days_overdue=0,
            payment_link_url=payment_link,
        )
        await notification_service.send_email(
            email="ojaswigupta317@gmail.com",
            subject=subject,
            body=body,
            workflow_id=workflow.id,
            html_body=html_body,
        )
        
        # 2. WhatsApp
        wa_msg = notification_service.build_recovery_sms("Test User", 199.0, payment_link)
        await notification_service.send_whatsapp(
            phone="+917991924011",
            message=wa_msg,
            workflow_id=workflow.id
        )
        
        # 3. Voice
        call_msg = "Main Alfeus Tech se bol rahi hoon. Aapka payment of 199 rupees process nahi ho paya."
        await notification_service.make_voice_call(
            phone="+917991924011",
            message=call_msg,
            workflow_id=workflow.id,
        )
        
        return HTMLResponse(
            content=f"<div class='p-4 bg-emerald-900/50 text-emerald-400 rounded-lg border border-emerald-700 shadow-sm'>✅ Sent Email, WhatsApp, and Voice Call instantly! Check your phone and inbox.</div>"
        )
    except Exception as e:
        logger.error(f"Failed to test all channels: {e}")
        return HTMLResponse(
            content=f"<div class='p-4 bg-red-900/50 text-red-400 rounded-lg border border-red-700 shadow-sm'>❌ Error: {str(e)}</div>"
        )

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
