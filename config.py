"""
RecovrAI Configuration
Centralized configuration management using pydantic-settings.
All environment variables, API keys, and policy constants.
"""

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class RecoveryChannel(str, Enum):
    SMS = "sms"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    VOICE_CALL = "voice_call"
    PAYMENT_LINK = "payment_link"
    HUMAN_ESCALATION = "human_escalation"


class WorkflowStatus(str, Enum):
    DETECTED = "detected"
    DIAGNOSING = "diagnosing"
    INTERVENTION_PLANNED = "intervention_planned"
    EXECUTING = "executing"
    RECOVERED = "recovered"
    AWAITING_PROMISE = "awaiting_promise"
    ESCALATED = "escalated"
    FAILED = "failed"
    STOPPED_COMPLIANCE = "stopped_compliance"


class EventType(str, Enum):
    PAYMENT_FAILED = "payment_failed"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    SUBSCRIPTION_FAILED = "subscription_failed"
    INVOICE_OVERDUE = "invoice_overdue"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === App ===
    app_env: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    app_name: str = "RecovrAI"
    app_version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8000
    app_base_url: str = "" # e.g. https://your-ngrok.app

    # === Database ===
    database_url: str = "sqlite+aiosqlite:///./recovrai.db"

    # === Razorpay Test Mode ===
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    # === Groq API (Free Tier) ===
    groq_api_key: Optional[str] = None
    groq_llm_model: str = "mixtral-8x7b-32768"
    groq_whisper_model: str = "whisper-large-v3-turbo"

    # === Google Gemini API (Fallback) ===
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"

    # === Twilio (Optional) ===
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    twilio_whatsapp_number: str = "whatsapp:+14155238886"
    test_phone_number: str = "+917991924011"

    # === Recovery Policy Constants ===
    max_contact_attempts: int = 5
    max_workflow_lifetime_days: int = 7
    cooldown_between_contacts_hours: int = 4
    quiet_hours_start: int = 21  # 9 PM IST
    quiet_hours_end: int = 9    # 9 AM IST
    max_voice_calls_per_48h: int = 1
    high_value_threshold_inr: int = 50000  # Auto-escalate above this

    # === LLM Confidence Thresholds ===
    min_confidence_for_auto_action: float = 0.7
    min_confidence_for_voice_call: float = 0.8

    # === Rate Limiting (Stay within free tiers) ===
    groq_max_rpm: int = 25       # Leave 5 RPM headroom from 30 limit
    razorpay_max_rpm: int = 20
    gemini_max_rpm: int = 12     # Leave 3 RPM headroom from 15 limit

    @property
    def escalation_ladder(self) -> list[RecoveryChannel]:
        return [
            RecoveryChannel.EMAIL,
            RecoveryChannel.WHATSAPP,
            RecoveryChannel.VOICE_CALL,
            RecoveryChannel.HUMAN_ESCALATION,
        ]

    # === Retry Delays (in minutes) ===
    gateway_error_retry_delay_min: int = 30
    insufficient_funds_retry_delay_hours: int = 24
    checkout_abandoned_nudge_delay_min: int = 15
    cart_abandoned_nudge_delay_hours: int = 2
    subscription_retry_delay_hours: int = 24
    invoice_reminder_interval_days: int = 3

    # === SMTP Email Configuration ===
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "recovery@recovrai.com"
    smtp_from_name: str = "RecovrAI Accounts Receivable"
    smtp_use_tls: bool = True

    # === Voice Agent ===
    voice_tts_voice_hindi: str = "hi-IN-SwaraNeural"
    voice_tts_voice_english: str = "en-IN-NeerjaNeural"
    voice_max_call_duration_seconds: int = 300  # 5 min max per call
    voice_silence_timeout_ms: int = 2000  # 2 seconds of silence = user done talking

    @property
    def is_demo_mode(self) -> bool:
        """Check if running in demo mode (no real API keys)."""
        return not self.razorpay_key_id or self.razorpay_key_id.startswith("rzp_test_XXXX")

    @property
    def has_twilio(self) -> bool:
        """Check if Twilio credentials are configured."""
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    @property
    def has_groq(self) -> bool:
        """Check if Groq API key is configured."""
        return bool(self.groq_api_key) and not self.groq_api_key.startswith("gsk_XXXX")

    @property
    def has_gemini(self) -> bool:
        """Check if Gemini API key is configured."""
        return bool(self.gemini_api_key) and not self.gemini_api_key.startswith("XXXX")

    @property
    def has_smtp(self) -> bool:
        """Check if SMTP email credentials are configured."""
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)


@lru_cache()
def get_settings() -> Settings:
    """Get cached application settings singleton."""
    return Settings()
