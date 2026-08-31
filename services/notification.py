"""
Notification service — multi-channel dispatcher for recovery communications.
Handles SMS, Email, WhatsApp, and Payment Link generation.
Logs every dispatch to the audit trail.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from ..config import get_settings, RecoveryChannel
from ..models.recovery import RecoveryAction, RecoveryWorkflow

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Multi-channel notification dispatcher.
    Routes recovery messages to the appropriate channel (SMS, Email, WhatsApp).
    Falls back to console logging in demo mode.
    """

    def __init__(self):
        self.settings = get_settings()
        self._twilio_client = None

        if self.settings.has_twilio:
            try:
                from twilio.rest import Client
                self._twilio_client = Client(
                    self.settings.twilio_account_sid,
                    self.settings.twilio_auth_token,
                )
                logger.info("Twilio client initialized")
            except ImportError:
                logger.warning("Twilio SDK not installed — using demo mode for SMS")
            except Exception as e:
                logger.warning(f"Failed to initialize Twilio: {e}")

    async def send_sms(
        self,
        phone: str,
        message: str,
        workflow_id: str,
    ) -> RecoveryAction:
        """
        Send an SMS to the customer.
        
        Args:
            phone: Customer phone number (with country code)
            message: SMS body text
            workflow_id: Associated workflow ID for audit trail
            
        Returns:
            RecoveryAction record
        """
        action = RecoveryAction(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            action_type="send_sms",
            channel=RecoveryChannel.SMS.value,
            status="executing",
            request_payload=json.dumps({"phone": phone, "message": message}),
        )

        try:
            if self._twilio_client:
                result = self._twilio_client.messages.create(
                    body=message,
                    from_=self.settings.twilio_phone_number,
                    to=phone,
                )
                action.status = "success"
                action.response_payload = json.dumps({
                    "sid": result.sid,
                    "status": result.status,
                })
                logger.info(f"SMS sent to {phone}: SID={result.sid}")
            else:
                # Demo mode — log to console
                logger.info(f"[DEMO SMS] To: {phone}\n  Message: {message}")
                action.status = "success"
                action.response_payload = json.dumps({
                    "demo": True,
                    "message": "SMS logged to console (demo mode)",
                })

            action.completed_at = datetime.utcnow()

        except Exception as e:
            action.status = "failed"
            action.error_message = str(e)
            action.completed_at = datetime.utcnow()
            logger.error(f"SMS failed to {phone}: {e}")

        return action

    async def send_email(
        self,
        email: str,
        subject: str,
        body: str,
        workflow_id: str,
    ) -> RecoveryAction:
        """
        Send a recovery email to the customer.
        In demo mode, logs to console.
        """
        action = RecoveryAction(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            action_type="send_email",
            channel=RecoveryChannel.EMAIL.value,
            status="executing",
            request_payload=json.dumps({
                "email": email,
                "subject": subject,
                "body": body[:200],
            }),
        )

        try:
            # Demo mode — always log to console
            logger.info(
                f"[DEMO EMAIL] To: {email}\n"
                f"  Subject: {subject}\n"
                f"  Body: {body[:100]}..."
            )
            action.status = "success"
            action.response_payload = json.dumps({
                "demo": True,
                "message": "Email logged to console (demo mode)",
            })
            action.completed_at = datetime.utcnow()

        except Exception as e:
            action.status = "failed"
            action.error_message = str(e)
            action.completed_at = datetime.utcnow()
            logger.error(f"Email failed to {email}: {e}")

        return action

    async def send_whatsapp(
        self,
        phone: str,
        message: str,
        workflow_id: str,
    ) -> RecoveryAction:
        """
        Send a WhatsApp message via Twilio sandbox.
        Falls back to console logging in demo mode.
        """
        action = RecoveryAction(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            action_type="send_whatsapp",
            channel=RecoveryChannel.WHATSAPP.value,
            status="executing",
            request_payload=json.dumps({"phone": phone, "message": message}),
        )

        try:
            if self._twilio_client:
                result = self._twilio_client.messages.create(
                    body=message,
                    from_="whatsapp:" + self.settings.twilio_phone_number,
                    to="whatsapp:" + phone,
                )
                action.status = "success"
                action.response_payload = json.dumps({
                    "sid": result.sid,
                    "status": result.status,
                })
                logger.info(f"WhatsApp sent to {phone}: SID={result.sid}")
            else:
                logger.info(f"[DEMO WHATSAPP] To: {phone}\n  Message: {message}")
                action.status = "success"
                action.response_payload = json.dumps({
                    "demo": True,
                    "message": "WhatsApp logged to console (demo mode)",
                })

            action.completed_at = datetime.utcnow()

        except Exception as e:
            action.status = "failed"
            action.error_message = str(e)
            action.completed_at = datetime.utcnow()
            logger.error(f"WhatsApp failed to {phone}: {e}")

        return action

    def build_recovery_sms(
        self,
        customer_name: str,
        amount_inr: float,
        payment_link_url: str,
        failure_reason: str = "",
    ) -> str:
        """Build a recovery SMS message."""
        name = customer_name.split()[0]  # First name only
        msg = (
            f"Hi {name}, your payment of Rs.{amount_inr:.0f} "
            f"could not be processed"
        )
        if failure_reason:
            msg += f" ({failure_reason})"
        msg += f". Complete it here: {payment_link_url}"
        msg += " - RecovrAI"
        return msg

    def build_cart_reminder_sms(
        self,
        customer_name: str,
        cart_value_inr: float,
        items_count: int,
        checkout_url: str,
    ) -> str:
        """Build a cart abandonment reminder SMS."""
        name = customer_name.split()[0]
        return (
            f"Hi {name}, you left {items_count} item(s) worth Rs.{cart_value_inr:.0f} "
            f"in your cart. Complete your purchase: {checkout_url} - RecovrAI"
        )

    def build_invoice_reminder_email(
        self,
        customer_name: str,
        company_name: str,
        invoice_number: str,
        amount_due_inr: float,
        days_overdue: int,
        payment_link_url: str,
    ) -> tuple[str, str]:
        """Build an invoice reminder email. Returns (subject, body)."""
        subject = f"Payment Reminder: Invoice {invoice_number} — {days_overdue} days overdue"
        body = f"""Dear {customer_name},

This is a friendly reminder that Invoice {invoice_number} for Rs.{amount_due_inr:,.2f} 
from {company_name} is currently {days_overdue} days past due.

To settle this invoice, please use the following secure payment link:
{payment_link_url}

If you have already made this payment, please disregard this message.

For any questions or to discuss payment arrangements, please reply to this email.

Best regards,
RecovrAI Automated Recovery System
"""
        return subject, body

    def build_subscription_recovery_sms(
        self,
        customer_name: str,
        plan_name: str,
        amount_inr: float,
        payment_link_url: str,
    ) -> str:
        """Build a subscription failure recovery SMS."""
        name = customer_name.split()[0]
        return (
            f"Hi {name}, your {plan_name} subscription payment of Rs.{amount_inr:.0f} "
            f"failed. Update your payment method: {payment_link_url} "
            f"to avoid service interruption. - RecovrAI"
        )
