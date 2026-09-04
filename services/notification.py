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
                import asyncio
                def _send_sms_sync():
                    return self._twilio_client.messages.create(
                        body=message,
                        from_=self.settings.twilio_phone_number,
                        to=phone,
                    )
                result = await asyncio.to_thread(_send_sms_sync)
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
        html_body: Optional[str] = None,
    ) -> RecoveryAction:
        """
        Send a recovery email to the customer.
        Uses real SMTP if configured; otherwise logs to console (demo mode).
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
                "body_preview": body[:150],
                "has_html": bool(html_body),
            }),
        )

        try:
            if self.settings.has_smtp:
                import asyncio
                import smtplib
                from email.mime.multipart import MIMEMultipart
                from email.mime.text import MIMEText

                def _send_sync():
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    from_display = f"{self.settings.smtp_from_name} <{self.settings.smtp_from_email}>"
                    msg["From"] = from_display
                    msg["To"] = email

                    # Plain text fallback
                    msg.attach(MIMEText(body, "plain", "utf-8"))
                    # HTML body if provided
                    if html_body:
                        msg.attach(MIMEText(html_body, "html", "utf-8"))

                    with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as server:
                        if self.settings.smtp_use_tls:
                            server.starttls()
                        server.login(self.settings.smtp_user, self.settings.smtp_password)
                        server.sendmail(self.settings.smtp_from_email, [email], msg.as_string())

                await asyncio.to_thread(_send_sync)
                action.status = "success"
                action.response_payload = json.dumps({
                    "smtp": True,
                    "to": email,
                    "subject": subject,
                    "host": self.settings.smtp_host,
                    "sent_at": datetime.utcnow().isoformat(),
                })
                logger.info(f"Email sent via SMTP to {email}: '{subject}'")

            else:
                # Demo mode — log to console
                logger.info(
                    f"[DEMO EMAIL] To: {email}\n"
                    f"  Subject: {subject}\n"
                    f"  Body: {body[:150]}..."
                )
                action.status = "success"
                action.response_payload = json.dumps({
                    "demo": True,
                    "to": email,
                    "subject": subject,
                    "message": "Email logged to console (demo mode — configure SMTP in .env for live dispatch)",
                    "simulated_at": datetime.utcnow().isoformat(),
                })

            action.completed_at = datetime.utcnow()

        except Exception as e:
            action.status = "failed"
            action.error_message = str(e)
            action.completed_at = datetime.utcnow()
            logger.error(f"Email delivery failed to {email}: {e}")

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
                import asyncio
                wa_from = self.settings.twilio_whatsapp_number or ("whatsapp:" + self.settings.twilio_phone_number)
                wa_to = phone if phone.startswith("whatsapp:") else ("whatsapp:" + phone)
                def _send_wa_sync():
                    return self._twilio_client.messages.create(
                        body=message,
                        from_=wa_from,
                        to=wa_to,
                    )
                result = await asyncio.to_thread(_send_wa_sync)
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

    def build_whatsapp_direct_link(self, phone: str, message: str) -> str:
        """Generate a WhatsApp click-to-chat web/mobile link."""
        import urllib.parse
        clean_phone = "".join(filter(str.isdigit, phone))
        encoded_msg = urllib.parse.quote(message)
        return f"https://wa.me/{clean_phone}?text={encoded_msg}"

    async def make_voice_call(
        self,
        phone: str,
        message: str,
        workflow_id: str,
    ) -> RecoveryAction:
        """
        Initiate an outbound PSTN phone call via Twilio with Hindi/Indian TTS.
        Falls back to console demo mode if Twilio is not configured.
        """
        action = RecoveryAction(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            action_type="voice_call",
            channel=RecoveryChannel.VOICE_CALL.value,
            status="executing",
            request_payload=json.dumps({"phone": phone, "message": message}),
        )

        try:
            if self._twilio_client:
                import asyncio
                twiml = f"""<Response>
                    <Say voice="Polly.Aditi" language="hi-IN">{message}</Say>
                    <Pause length="1"/>
                    <Say voice="Polly.Aditi" language="hi-IN">Kripya apna payment link check karein aur payment complete karein. Dhanyawad.</Say>
                </Response>"""

                def _call_sync():
                    return self._twilio_client.calls.create(
                        to=phone,
                        from_=self.settings.twilio_phone_number,
                        twiml=twiml,
                    )

                call = await asyncio.to_thread(_call_sync)
                action.status = "success"
                action.response_payload = json.dumps({
                    "sid": call.sid,
                    "status": call.status,
                    "to": phone,
                })
                logger.info(f"Twilio Voice call dispatched to {phone}: SID={call.sid}")
            else:
                logger.info(f"[DEMO CALL] To: {phone}\n  Spoken Text: {message}")
                action.status = "success"
                action.response_payload = json.dumps({
                    "demo": True,
                    "message": "Voice call logged to console (demo mode)",
                })

            action.completed_at = datetime.utcnow()

        except Exception as e:
            action.status = "failed"
            action.error_message = str(e)
            action.completed_at = datetime.utcnow()
            logger.error(f"Voice call failed to {phone}: {e}")

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

    def build_invoice_html_email(
        self,
        customer_name: str,
        company_name: str,
        invoice_number: str,
        amount_due_inr: float,
        days_overdue: int,
        payment_link_url: str,
    ) -> tuple[str, str, str]:
        """
        Build an enterprise-grade HTML invoice reminder email.
        Returns (subject, plain_text, html_body).
        """
        subject, plain_text = self.build_invoice_reminder_email(
            customer_name=customer_name,
            company_name=company_name,
            invoice_number=invoice_number,
            amount_due_inr=amount_due_inr,
            days_overdue=days_overdue,
            payment_link_url=payment_link_url,
        )

        urgency_color = "#dc2626" if days_overdue > 30 else "#f59e0b"
        urgency_label = f"OVERDUE BY {days_overdue} DAYS" if days_overdue > 0 else "PAYMENT DUE"

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 32px 16px;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
        <!-- Header Banner -->
        <div style="background-color: #0f172a; padding: 24px; border-bottom: 1px solid #334155; display: flex; align-items: center; justify-content: space-between;">
            <div>
                <h1 style="margin: 0; font-size: 20px; font-weight: 700; color: #10b981;">⚡ RecovrAI</h1>
                <p style="margin: 4px 0 0; font-size: 13px; color: #94a3b8;">Automated Accounts Receivable</p>
            </div>
            <span style="display: inline-block; background-color: {urgency_color}20; color: {urgency_color}; border: 1px solid {urgency_color}50; padding: 4px 12px; border-radius: 9999px; font-size: 12px; font-weight: 600; text-transform: uppercase;">
                {urgency_label}
            </span>
        </div>

        <!-- Body Content -->
        <div style="padding: 32px 24px;">
            <p style="font-size: 16px; margin: 0 0 16px; color: #e2e8f0;">Dear <strong>{customer_name}</strong>,</p>
            <p style="font-size: 14px; line-height: 1.6; color: #94a3b8; margin: 0 0 24px;">
                This is an official notice regarding outstanding invoice <strong>{invoice_number}</strong> issued by <strong>{company_name}</strong>. Our records indicate this balance is currently unpaid.
            </p>

            <!-- Invoice Summary Box -->
            <div style="background-color: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 20px; margin-bottom: 28px;">
                <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                    <tr>
                        <td style="padding: 6px 0; color: #94a3b8;">Invoice Number:</td>
                        <td style="padding: 6px 0; font-weight: 600; color: #f8fafc; text-align: right; font-family: monospace;">{invoice_number}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; color: #94a3b8;">Vendor / Billed By:</td>
                        <td style="padding: 6px 0; font-weight: 600; color: #f8fafc; text-align: right;">{company_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; color: #94a3b8;">Days Overdue:</td>
                        <td style="padding: 6px 0; font-weight: 700; color: {urgency_color}; text-align: right;">{days_overdue} days</td>
                    </tr>
                    <tr style="border-top: 1px solid #334155;">
                        <td style="padding: 12px 0 0; font-size: 16px; font-weight: 600; color: #f8fafc;">Total Amount Due:</td>
                        <td style="padding: 12px 0 0; font-size: 22px; font-weight: 800; color: #10b981; text-align: right;">₹{amount_due_inr:,.2f}</td>
                    </tr>
                </table>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin-bottom: 28px;">
                <a href="{payment_link_url}" style="display: inline-block; background: linear-gradient(135deg, #10b981, #059669); color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 700; padding: 14px 36px; border-radius: 8px; box-shadow: 0 4px 12px rgba(16, 185, 129, 0.4);">
                    Pay ₹{amount_due_inr:,.2f} Securely via Razorpay →
                </a>
            </div>

            <p style="font-size: 12px; color: #64748b; text-align: center; margin: 0 0 16px;">
                Or copy and paste this link into your browser:<br>
                <a href="{payment_link_url}" style="color: #38bdf8; word-break: break-all;">{payment_link_url}</a>
            </p>

            <div style="border-top: 1px solid #334155; padding-top: 20px; font-size: 12px; color: #64748b; line-height: 1.5;">
                <p style="margin: 0 0 8px;">• If you have already settled this invoice, please disregard this reminder.</p>
                <p style="margin: 0;">• For billing disputes or payment arrangement requests, reply directly to this email.</p>
            </div>
        </div>

        <!-- Footer -->
        <div style="background-color: #0f172a; padding: 16px 24px; border-top: 1px solid #334155; text-align: center; font-size: 11px; color: #64748b;">
            Sent by RecovrAI Autonomous Recovery Agent on behalf of {company_name}. Fully audited & compliance-verified.
        </div>
    </div>
</body>
</html>"""
        return subject, plain_text, html_body

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
