"""
Razorpay webhook receiver and event simulator.
Handles real webhooks from Razorpay and provides an event simulator for demos.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel

from ..config import get_settings
from ..models.database import get_db_session
from ..models.events import PaymentEvent
from ..services.razorpay_client import RazorpayClient
from ..services.recovery_orchestrator import RecoveryOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
razorpay_client = RazorpayClient()
orchestrator = RecoveryOrchestrator()


# ─── Razorpay Webhook Endpoint ────────────────────────────────────────────────

@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None, alias="X-Razorpay-Signature"),
):
    """
    Receive and process Razorpay webhook events.
    
    Supported events:
    - payment.failed
    - order.paid
    - payment_link.paid
    - invoice.expired
    - subscription.halted
    """
    body = await request.body()
    body_str = body.decode("utf-8")
    settings = get_settings()

    # Verify signature (skip in demo mode)
    if not settings.is_demo_mode and x_razorpay_signature:
        is_valid = razorpay_client.verify_webhook_signature(
            body_str,
            x_razorpay_signature,
            settings.razorpay_key_secret,
        )
        if not is_valid:
            logger.warning("Invalid webhook signature")
            raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("event", "")
    entity = payload.get("payload", {})

    logger.info(f"Webhook received: {event_type}")

    if event_type == "payment.failed":
        await _handle_payment_failed(entity)
    elif event_type == "order.paid":
        await _handle_order_paid(entity)
    elif event_type == "payment_link.paid":
        await _handle_payment_link_paid(entity)
    else:
        logger.info(f"Unhandled webhook event: {event_type}")

    return {"status": "ok"}


async def _handle_payment_failed(entity: dict) -> None:
    """Process a payment.failed webhook event."""
    payment = entity.get("payment", {}).get("entity", {})

    event = PaymentEvent(
        id=str(uuid.uuid4()),
        payment_id=payment.get("id", f"pay_{uuid.uuid4().hex[:14]}"),
        order_id=payment.get("order_id", ""),
        customer_id=payment.get("customer_id", f"cust_{uuid.uuid4().hex[:8]}"),
        customer_name=payment.get("notes", {}).get("customer_name", "Unknown"),
        customer_email=payment.get("email", "unknown@example.com"),
        customer_phone=payment.get("contact", "+919999999999"),
        amount=payment.get("amount", 0),
        currency=payment.get("currency", "INR"),
        status="failed",
        method=payment.get("method", ""),
        bank=payment.get("bank", ""),
        error_code=payment.get("error_code", ""),
        error_description=payment.get("error_description", ""),
        error_source=payment.get("error_source", ""),
        error_reason=payment.get("error_reason", ""),
    )

    async with get_db_session() as session:
        session.add(event)
        workflow = await orchestrator.ingest_event(session, event)
        await orchestrator.diagnose_workflow(session, workflow, event)
        logger.info(f"Payment failure processed → workflow {workflow.id[:8]}")


async def _handle_order_paid(entity: dict) -> None:
    """Process an order.paid webhook — marks recovery as successful."""
    order = entity.get("order", {}).get("entity", {})
    order_id = order.get("id", "")

    async with get_db_session() as session:
        from sqlalchemy import select
        from ..models.recovery import RecoveryWorkflow
        from ..models.events import PaymentEvent as PE

        # Find workflow by matching order_id in events
        stmt = select(PE).where(PE.order_id == order_id)
        result = await session.execute(stmt)
        payment_event = result.scalar_one_or_none()

        if payment_event:
            stmt2 = select(RecoveryWorkflow).where(
                RecoveryWorkflow.event_id == payment_event.id
            )
            result2 = await session.execute(stmt2)
            workflow = result2.scalar_one_or_none()

            if workflow:
                await orchestrator.simulate_payment_received(session, workflow.id)
                logger.info(f"Order paid → workflow {workflow.id[:8]} recovered")


async def _handle_payment_link_paid(entity: dict) -> None:
    """Process a payment_link.paid webhook — marks recovery as successful."""
    pl = entity.get("payment_link", {}).get("entity", {})
    link_id = pl.get("id", "")

    async with get_db_session() as session:
        from sqlalchemy import select
        from ..models.recovery import RecoveryWorkflow

        stmt = select(RecoveryWorkflow).where(
            RecoveryWorkflow.payment_link_id == link_id
        )
        result = await session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if workflow:
            await orchestrator.simulate_payment_received(session, workflow.id)
            logger.info(f"Payment link paid → workflow {workflow.id[:8]} recovered")


# ─── Event Simulator (for demos) ─────────────────────────────────────────────

class SimulateEventRequest(BaseModel):
    event_type: str = "payment.failed"
    amount: int = 250000  # ₹2,500 in paise
    customer_name: str = "Demo Customer"
    customer_email: str = "demo@example.com"
    customer_phone: str = "+919876543210"
    error_code: str = "INSUFFICIENT_FUNDS"
    method: str = "upi"


@router.post("/simulate")
async def simulate_event(req: SimulateEventRequest):
    """
    Simulate a webhook event for demo purposes.
    Creates a realistic failure event and triggers the full recovery pipeline.
    """
    payload = {
        "event": req.event_type,
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_sim_{uuid.uuid4().hex[:10]}",
                    "order_id": f"order_sim_{uuid.uuid4().hex[:10]}",
                    "amount": req.amount,
                    "currency": "INR",
                    "method": req.method,
                    "email": req.customer_email,
                    "contact": req.customer_phone,
                    "error_code": req.error_code,
                    "error_description": f"Payment failed: {req.error_code}",
                    "error_source": "gateway",
                    "error_reason": req.error_code.lower(),
                    "notes": {"customer_name": req.customer_name},
                }
            }
        },
    }

    await _handle_payment_failed(payload["payload"])

    return {
        "status": "simulated",
        "event_type": req.event_type,
        "amount_inr": req.amount / 100,
        "message": f"Simulated {req.event_type} for ₹{req.amount/100:.2f}",
    }
