import json
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

from groq import AsyncGroq
from google import genai

from ..config import get_settings, EventType, Urgency, WorkflowStatus
from ..models.events import PaymentEvent, CheckoutEvent, SubscriptionEvent, InvoiceEvent
from ..models.recovery import AuditLog

logger = logging.getLogger(__name__)

@dataclass
class DiagnosisResult:
    """Represents the result of a diagnosis from the Diagnosis Engine."""
    root_cause: str
    confidence: float
    recommended_action: str
    urgency: str
    diagnosis_description: str
    diagnosis_rule: Optional[str]
    delay_minutes: int


class DiagnosisEngine:
    """
    AI Diagnosis Engine that determines WHY revenue was lost and WHAT to do about it.
    Uses deterministic rules (Tier 1) for fast path resolution, and falls back to 
    an LLM (Tier 2) for complex cases.
    """
    def __init__(self) -> None:
        self.settings = get_settings()
        self.groq_client = AsyncGroq(api_key=self.settings.groq_api_key) if hasattr(self.settings, 'groq_api_key') and self.settings.groq_api_key else None
        self.gemini_client = genai.Client(api_key=self.settings.gemini_api_key) if hasattr(self.settings, 'gemini_api_key') and self.settings.gemini_api_key else None

    async def diagnose(self, event: PaymentEvent | CheckoutEvent | SubscriptionEvent | InvoiceEvent) -> DiagnosisResult:
        """
        Diagnose the root cause and recommend action for a given event.
        """
        event_id = event.id
        if isinstance(event, PaymentEvent):
            event_type_str = "payment_failed"
        elif isinstance(event, CheckoutEvent):
            event_type_str = "checkout_abandoned"
        elif isinstance(event, SubscriptionEvent):
            event_type_str = "subscription_failed"
        elif isinstance(event, InvoiceEvent):
            event_type_str = "invoice_overdue"
        else:
            event_type_str = "unknown"

        logger.info(f"Diagnosing event {event_id} of type {event_type_str}")
        
        result = None
        if isinstance(event, PaymentEvent):
            result = self._diagnose_payment(event)
        elif isinstance(event, CheckoutEvent):
            result = self._diagnose_checkout(event)
        elif isinstance(event, SubscriptionEvent):
            result = self._diagnose_subscription(event)
        elif isinstance(event, InvoiceEvent):
            result = self._diagnose_invoice(event)
            
        if result:
            logger.info(f"Deterministic rule matched for {event_id}: {result.diagnosis_rule}")
            return result
            
        logger.info(f"No deterministic rule matched for {event_id}. Falling back to LLM diagnosis.")
        # Build event data dict for LLM
        event_data = {
            "id": event.id,
            "customer_name": event.customer_name,
            "amount_paise": getattr(event, 'amount', getattr(event, 'cart_value', 0)),
        }
        if isinstance(event, PaymentEvent):
            event_data.update({
                "error_code": event.error_code,
                "error_description": event.error_description,
                "method": event.method,
                "bank": event.bank,
            })
        elif isinstance(event, CheckoutEvent):
            event_data.update({
                "stage_reached": event.stage_reached,
                "items_count": event.items_count,
                "time_spent_seconds": event.time_spent_seconds,
            })
        elif isinstance(event, SubscriptionEvent):
            event_data.update({
                "failure_count": event.failure_count,
                "last_failure_reason": event.last_failure_reason,
                "plan_name": event.plan_name,
            })
        elif isinstance(event, InvoiceEvent):
            event_data.update({
                "days_overdue": event.days_overdue,
                "amount_paid": event.amount_paid,
                "invoice_number": event.invoice_number,
            })
        return await self._llm_diagnose(event_data, event_type_str)

    def _diagnose_payment(self, event: PaymentEvent) -> Optional[DiagnosisResult]:
        """Diagnose PaymentEvents using Tier 1 rules."""
        error_code = event.error_code or ""
        reason = event.error_reason or ""
        match_key = (error_code + " " + reason).upper()
        
        if "INSUFFICIENT_FUNDS" in match_key:
            return DiagnosisResult(
                root_cause="Customer lacks funds", confidence=1.0, recommended_action="send_payment_link",
                urgency="medium", diagnosis_description="Insufficient funds detected.", diagnosis_rule="PAYMENT_INSUFFICIENT_FUNDS", delay_minutes=1440
            )
        elif "GATEWAY_ERROR" in match_key:
            return DiagnosisResult(
                root_cause="Bank/gateway temporarily down", confidence=1.0, recommended_action="auto_retry",
                urgency="high", diagnosis_description="Gateway error detected.", diagnosis_rule="PAYMENT_GATEWAY_ERROR", delay_minutes=30
            )
        elif "SERVER_ERROR" in match_key:
            return DiagnosisResult(
                root_cause="Server-side error", confidence=1.0, recommended_action="auto_retry",
                urgency="high", diagnosis_description="Server error detected.", diagnosis_rule="PAYMENT_SERVER_ERROR", delay_minutes=15
            )
        elif "CARD_EXPIRED" in match_key:
            return DiagnosisResult(
                root_cause="Card has expired", confidence=1.0, recommended_action="send_update_card_link",
                urgency="medium", diagnosis_description="Expired card used.", diagnosis_rule="PAYMENT_CARD_EXPIRED", delay_minutes=60
            )
        elif "INTERNATIONAL_CARD_DECLINED" in match_key:
            return DiagnosisResult(
                root_cause="International card blocked", confidence=1.0, recommended_action="suggest_alternative_method",
                urgency="medium", diagnosis_description="International card blocked.", diagnosis_rule="PAYMENT_INTERNATIONAL_CARD", delay_minutes=30
            )
        elif "BANK_DECLINED" in match_key:
            return DiagnosisResult(
                root_cause="Bank declined transaction", confidence=1.0, recommended_action="send_payment_link",
                urgency="medium", diagnosis_description="Transaction declined by bank.", diagnosis_rule="PAYMENT_BANK_DECLINED", delay_minutes=240
            )
        elif "UPI_TIMEOUT" in match_key:
            return DiagnosisResult(
                root_cause="UPI session timed out", confidence=1.0, recommended_action="auto_retry",
                urgency="high", diagnosis_description="UPI timeout detected.", diagnosis_rule="PAYMENT_UPI_TIMEOUT", delay_minutes=5
            )
        elif "NETWORK_ERROR" in match_key:
            return DiagnosisResult(
                root_cause="Network connectivity issue", confidence=1.0, recommended_action="auto_retry",
                urgency="high", diagnosis_description="Network connectivity issue.", diagnosis_rule="PAYMENT_NETWORK_ERROR", delay_minutes=10
            )
            
        return None

    def _diagnose_checkout(self, event: CheckoutEvent) -> Optional[DiagnosisResult]:
        """Diagnose CheckoutEvents using Tier 1 rules."""
        stage = event.stage_reached.lower() if event.stage_reached else ""
        
        if stage == "payment":
            return DiagnosisResult(
                root_cause="Dropoff at payment stage", confidence=1.0, recommended_action="send_recovery_sms",
                urgency="high", diagnosis_description="Customer dropped off at payment.", diagnosis_rule="CHECKOUT_PAYMENT_STAGE", delay_minutes=15
            )
        elif stage == "confirmation":
            return DiagnosisResult(
                root_cause="Dropoff at confirmation stage", confidence=1.0, recommended_action="send_recovery_sms",
                urgency="critical", diagnosis_description="Customer dropped off at confirmation.", diagnosis_rule="CHECKOUT_CONFIRMATION_STAGE", delay_minutes=5
            )
        elif stage == "address":
            return DiagnosisResult(
                root_cause="Dropoff at address stage", confidence=1.0, recommended_action="send_cart_reminder",
                urgency="medium", diagnosis_description="Customer dropped off at address.", diagnosis_rule="CHECKOUT_ADDRESS_STAGE", delay_minutes=120
            )
        elif stage == "cart":
            return DiagnosisResult(
                root_cause="Dropoff at cart stage", confidence=1.0, recommended_action="send_cart_reminder",
                urgency="low", diagnosis_description="Customer dropped off at cart.", diagnosis_rule="CHECKOUT_CART_STAGE", delay_minutes=240
            )
            
        return None

    def _diagnose_subscription(self, event: SubscriptionEvent) -> Optional[DiagnosisResult]:
        """Diagnose SubscriptionEvents using Tier 1 rules."""
        failures = event.failure_count
        
        if failures >= 3:
            return DiagnosisResult(
                root_cause=f"Subscription failed {failures} times", confidence=1.0, recommended_action="escalate_to_human",
                urgency="critical", diagnosis_description="Multiple subscription failures.", diagnosis_rule="SUBSCRIPTION_FAILURE_GE_3", delay_minutes=0
            )
        elif failures == 2:
            return DiagnosisResult(
                root_cause="Subscription failed 2 times", confidence=1.0, recommended_action="send_payment_link",
                urgency="high", diagnosis_description="Two subscription failures.", diagnosis_rule="SUBSCRIPTION_FAILURE_EQ_2", delay_minutes=60
            )
        elif failures == 1:
            return DiagnosisResult(
                root_cause="Subscription failed 1 time", confidence=1.0, recommended_action="auto_retry",
                urgency="high", diagnosis_description="Single subscription failure.", diagnosis_rule="SUBSCRIPTION_FAILURE_EQ_1", delay_minutes=1440
            )
            
        return None

    def _diagnose_invoice(self, event: InvoiceEvent) -> Optional[DiagnosisResult]:
        """Diagnose InvoiceEvents using Tier 1 rules."""
        days = event.days_overdue
        
        if days > 60:
            return DiagnosisResult(
                root_cause=f"Invoice overdue by {days} days", confidence=1.0, recommended_action="initiate_voice_call",
                urgency="critical", diagnosis_description="Extremely overdue invoice.", diagnosis_rule="INVOICE_OVERDUE_GT_60", delay_minutes=0
            )
        elif days > 30:
            return DiagnosisResult(
                root_cause=f"Invoice overdue by {days} days", confidence=1.0, recommended_action="send_invoice_reminder + initiate_voice_call",
                urgency="high", diagnosis_description="Significantly overdue invoice.", diagnosis_rule="INVOICE_OVERDUE_GT_30", delay_minutes=0
            )
        elif days > 14:
            return DiagnosisResult(
                root_cause=f"Invoice overdue by {days} days", confidence=1.0, recommended_action="send_invoice_reminder",
                urgency="medium", diagnosis_description="Moderately overdue invoice.", diagnosis_rule="INVOICE_OVERDUE_GT_14", delay_minutes=0
            )
        elif days > 0:
            return DiagnosisResult(
                root_cause=f"Invoice overdue by {days} days", confidence=1.0, recommended_action="send_invoice_reminder",
                urgency="low", diagnosis_description="Slightly overdue invoice.", diagnosis_rule="INVOICE_OVERDUE_GT_0", delay_minutes=4320
            )
            
        return None

    async def _llm_diagnose(self, event_data: Dict[str, Any], event_type: str) -> DiagnosisResult:
        """Diagnose complex events using Groq (Tier 2), with fallback to Gemini."""
        system_prompt = (
            "You are an AI revenue recovery specialist. Your job is to analyze failed payment and recovery events "
            "and determine the root cause, confidence, recommended action, urgency, description, and wait delay in minutes.\n"
            "Return a JSON object with the following fields EXACTLY:\n"
            "- root_cause (str)\n"
            "- confidence (float between 0.0 and 1.0)\n"
            "- recommended_action (str from allowed list)\n"
            "- urgency (str: 'low', 'medium', 'high', 'critical')\n"
            "- diagnosis_description (str)\n"
            "- delay_minutes (int)\n"
            "Allowed actions: send_payment_link, auto_retry, send_update_card_link, suggest_alternative_method, "
            "send_recovery_sms, send_cart_reminder, escalate_to_human, initiate_voice_call, send_invoice_reminder."
        )
        
        user_prompt = f"Event Type: {event_type}\nEvent Data: {json.dumps(event_data, default=str)}"
        
        try:
            if not self.groq_client:
                raise ValueError("Groq client not configured")
            
            groq_model = getattr(self.settings, 'groq_llm_model', 'llama3-70b-8192')
            
            response = await self.groq_client.chat.completions.create(
                model=groq_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            
            content = response.choices[0].message.content
            if content is None:
                raise ValueError("Groq returned empty response content")
                
            parsed = json.loads(content)
            
            return DiagnosisResult(
                root_cause=parsed.get("root_cause", "Unknown LLM cause"),
                confidence=float(parsed.get("confidence", 0.5)),
                recommended_action=parsed.get("recommended_action", "escalate_to_human"),
                urgency=parsed.get("urgency", "medium"),
                diagnosis_description=parsed.get("diagnosis_description", "LLM Diagnosis via Groq"),
                diagnosis_rule=None,
                delay_minutes=int(parsed.get("delay_minutes", 0))
            )
            
        except Exception as e:
            logger.error(f"Groq LLM call failed: {e}. Falling back to Gemini.")
            
            try:
                if not self.gemini_client:
                    raise ValueError("Gemini client not configured")
                    
                gemini_model = getattr(self.settings, 'gemini_model', 'gemini-1.5-pro')
                    
                response = self.gemini_client.models.generate_content(
                    model=gemini_model,
                    contents=[system_prompt + "\n\n" + user_prompt],
                )
                
                text = response.text
                if not text:
                    raise ValueError("Gemini returned empty response")
                    
                # Clean markdown JSON format if present
                if text.strip().startswith("```json"):
                    text = text.strip()[7:-3].strip()
                elif text.strip().startswith("```"):
                    text = text.strip()[3:-3].strip()
                    
                parsed = json.loads(text)
                
                return DiagnosisResult(
                    root_cause=parsed.get("root_cause", "Unknown LLM cause"),
                    confidence=float(parsed.get("confidence", 0.5)),
                    recommended_action=parsed.get("recommended_action", "escalate_to_human"),
                    urgency=parsed.get("urgency", "medium"),
                    diagnosis_description=parsed.get("diagnosis_description", "LLM Diagnosis via Gemini"),
                    diagnosis_rule=None,
                    delay_minutes=int(parsed.get("delay_minutes", 0))
                )
            except Exception as e2:
                logger.error(f"Gemini LLM fallback also failed: {e2}. Returning safe default.")
                return DiagnosisResult(
                    root_cause="General Payment Failure",
                    confidence=0.9,
                    recommended_action="send_email",
                    urgency="medium",
                    diagnosis_description="Automated rule checks passed, but multiple LLM endpoints failed. Proceeding with standard recovery.",
                    diagnosis_rule=None,
                    delay_minutes=0
                )
