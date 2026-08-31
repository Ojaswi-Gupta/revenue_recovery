"""
Razorpay client wrapper for test-mode API interactions.
Handles orders, payments, payment links, and webhook signature verification.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

import razorpay

from ..config import get_settings

logger = logging.getLogger(__name__)


class RazorpayClient:
    """
    Wrapper around the Razorpay Python SDK for test-mode operations.
    Falls back to mock responses when API keys are not configured (demo mode).
    """

    def __init__(self):
        settings = get_settings()
        self._demo_mode = settings.is_demo_mode

        if not self._demo_mode:
            self._client = razorpay.Client(
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
            )
            self._client.set_app_details({
                "title": "RecovrAI",
                "version": settings.app_version,
            })
            logger.info("Razorpay client initialized in TEST MODE")
        else:
            self._client = None
            logger.warning("Razorpay client in DEMO MODE — using mock responses")

    # ─── Orders ───────────────────────────────────────────────────────────────

    def create_order(
        self,
        amount: int,
        currency: str = "INR",
        receipt: Optional[str] = None,
        notes: Optional[dict] = None,
    ) -> dict[str, Any]:
        """
        Create a Razorpay order.
        
        Args:
            amount: Amount in paise (e.g., 50000 = ₹500)
            currency: Currency code (default: INR)
            receipt: Optional receipt ID
            notes: Optional key-value notes
            
        Returns:
            Order response dict with id, amount, status, etc.
        """
        if self._demo_mode:
            return self._mock_order(amount, currency, receipt)

        order_data: dict[str, Any] = {
            "amount": amount,
            "currency": currency,
            "receipt": receipt or f"rcpt_{uuid.uuid4().hex[:12]}",
        }
        if notes:
            order_data["notes"] = notes

        try:
            order = self._client.order.create(data=order_data)
            logger.info(f"Order created: {order['id']} for ₹{amount/100}")
            return order
        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            raise

    # ─── Payment Links ────────────────────────────────────────────────────────

    def create_payment_link(
        self,
        amount: int,
        customer_name: str,
        customer_email: str,
        customer_phone: str,
        description: str = "Recovery Payment",
        currency: str = "INR",
        expire_by: Optional[int] = None,
        notify_sms: bool = True,
        notify_email: bool = True,
        notes: Optional[dict] = None,
    ) -> dict[str, Any]:
        """
        Create a short-lived payment link for recovery.
        
        Args:
            amount: Amount in paise
            customer_name: Customer's full name
            customer_email: Customer's email
            customer_phone: Customer's phone (with country code)
            description: Payment description
            currency: Currency code
            expire_by: Unix timestamp for expiry (optional)
            notify_sms: Send SMS notification
            notify_email: Send email notification
            notes: Optional key-value notes
            
        Returns:
            Payment link response with short_url, id, etc.
        """
        if self._demo_mode:
            return self._mock_payment_link(amount, customer_name, customer_phone)

        link_data: dict[str, Any] = {
            "amount": amount,
            "currency": currency,
            "description": description,
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "contact": customer_phone,
            },
            "notify": {
                "sms": notify_sms,
                "email": notify_email,
            },
            "reminder_enable": True,
        }
        if expire_by:
            link_data["expire_by"] = expire_by
        if notes:
            link_data["notes"] = notes

        try:
            link = self._client.payment_link.create(data=link_data)
            logger.info(
                f"Payment link created: {link['id']} "
                f"₹{amount/100} → {link.get('short_url', 'N/A')}"
            )
            return link
        except Exception as e:
            logger.error(f"Failed to create payment link: {e}")
            raise

    def fetch_payment_link(self, link_id: str) -> dict[str, Any]:
        """Fetch a payment link by ID to check its status."""
        if self._demo_mode:
            return {"id": link_id, "status": "created", "amount": 0}

        try:
            return self._client.payment_link.fetch(link_id)
        except Exception as e:
            logger.error(f"Failed to fetch payment link {link_id}: {e}")
            raise

    # ─── Payments ─────────────────────────────────────────────────────────────

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        """Fetch payment details by payment ID."""
        if self._demo_mode:
            return self._mock_payment(payment_id)

        try:
            return self._client.payment.fetch(payment_id)
        except Exception as e:
            logger.error(f"Failed to fetch payment {payment_id}: {e}")
            raise

    def fetch_payments_for_order(self, order_id: str) -> list[dict[str, Any]]:
        """Fetch all payments associated with an order."""
        if self._demo_mode:
            return []

        try:
            result = self._client.order.payments(order_id)
            return result.get("items", [])
        except Exception as e:
            logger.error(f"Failed to fetch payments for order {order_id}: {e}")
            raise

    # ─── Webhook Verification ─────────────────────────────────────────────────

    def verify_webhook_signature(
        self, body: str, signature: str, secret: str
    ) -> bool:
        """
        Verify Razorpay webhook signature.
        
        Args:
            body: Raw request body string
            signature: Value of X-Razorpay-Signature header
            secret: Webhook secret from Razorpay dashboard
            
        Returns:
            True if signature is valid
        """
        if self._demo_mode:
            return True

        try:
            self._client.utility.verify_webhook_signature(body, signature, secret)
            return True
        except razorpay.errors.SignatureVerificationError:
            logger.warning("Webhook signature verification failed")
            return False

    # ─── Mock Responses (Demo Mode) ──────────────────────────────────────────

    def _mock_order(
        self, amount: int, currency: str, receipt: Optional[str]
    ) -> dict[str, Any]:
        """Generate a mock order response."""
        order_id = f"order_demo_{uuid.uuid4().hex[:12]}"
        logger.info(f"[DEMO] Mock order created: {order_id} for ₹{amount/100}")
        return {
            "id": order_id,
            "entity": "order",
            "amount": amount,
            "amount_paid": 0,
            "amount_due": amount,
            "currency": currency,
            "receipt": receipt or f"rcpt_{uuid.uuid4().hex[:8]}",
            "status": "created",
            "created_at": int(datetime.now().timestamp()),
        }

    def _mock_payment_link(
        self, amount: int, customer_name: str, customer_phone: str
    ) -> dict[str, Any]:
        """Generate a mock payment link response."""
        link_id = f"plink_demo_{uuid.uuid4().hex[:12]}"
        short_url = f"https://rzp.io/demo/{uuid.uuid4().hex[:8]}"
        logger.info(f"[DEMO] Mock payment link: {link_id} → {short_url}")
        return {
            "id": link_id,
            "entity": "payment_link",
            "amount": amount,
            "currency": "INR",
            "status": "created",
            "short_url": short_url,
            "customer": {
                "name": customer_name,
                "contact": customer_phone,
            },
            "created_at": int(datetime.now().timestamp()),
        }

    def _mock_payment(self, payment_id: str) -> dict[str, Any]:
        """Generate a mock payment response."""
        return {
            "id": payment_id,
            "entity": "payment",
            "amount": 50000,
            "currency": "INR",
            "status": "failed",
            "method": "upi",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment failed",
            "created_at": int(datetime.now().timestamp()),
        }
