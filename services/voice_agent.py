import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

import edge_tts
from groq import AsyncGroq

from ..config import get_settings
from ..models.recovery import RecoveryWorkflow, RecoveryAction, AuditLog

logger = logging.getLogger(__name__)
settings = get_settings()

@dataclass
class ConversationState:
    workflow_id: str
    customer_name: str
    amount_inr: float
    failure_reason: str
    turn_count: int = 0
    max_turns: int = 10
    conversation_history: list[dict] = field(default_factory=list)
    customer_intent: Optional[str] = None  # will_pay, need_time, dispute, confused, stop
    promise_date: Optional[datetime] = None
    promise_amount: Optional[float] = None
    call_ended: bool = False
    call_summary: str = ""


class VoiceAgent:
    def __init__(self):
        self.groq_client = AsyncGroq(api_key=settings.groq_api_key) if getattr(settings, "has_groq", True) else None

    async def start_conversation(self, workflow: RecoveryWorkflow) -> ConversationState:
        """Initialize a new voice conversation for a recovery workflow."""
        customer_name = workflow.customer_name or "Sir/Madam"
        amount = workflow.amount_at_risk_inr
        reason = workflow.root_cause or workflow.diagnosis_description or "technical issue"
        
        state = ConversationState(
            workflow_id=str(workflow.id),
            customer_name=customer_name,
            amount_inr=float(amount),
            failure_reason=reason
        )
        
        greeting = f"Namaste {customer_name} ji, main merchant se bol raha hoon. Aapka recent payment of ₹{amount:.0f} process nahi ho paya."
        state.conversation_history.append({"role": "assistant", "content": greeting})
        return state

    async def process_user_speech(self, state: ConversationState, audio_bytes: bytes) -> Tuple[str, bytes]:
        """Process user audio: STT → LLM → TTS. Returns (text_response, audio_response_bytes)."""
        if state.call_ended:
            return "", b""
            
        user_text = await self.transcribe_audio(audio_bytes)
        state.conversation_history.append({"role": "user", "content": user_text})
        
        text_response = await self.generate_response(state, user_text)
        audio_response_bytes = await self.synthesize_speech(text_response)
        
        state.turn_count += 1
        if state.turn_count >= state.max_turns:
            state.call_ended = True
            
        return text_response, audio_response_bytes

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """Convert speech to text using Groq Whisper."""
        if not self.groq_client:
            logger.warning("Groq client not initialized, returning empty transcription.")
            return ""
            
        try:
            transcription = await self.groq_client.audio.transcriptions.create(
                model=getattr(settings, "groq_whisper_model", "whisper-large-v3"),
                file=("audio.wav", audio_bytes),
                language="hi",
            )
            return transcription.text
        except Exception as e:
            logger.error(f"Error transcribing audio: {e}")
            return ""

    def _build_system_prompt(self, state: ConversationState) -> str:
        """Build the system prompt for the LLM."""
        prompt = f"""
You are an AI recovery agent calling a customer on behalf of a merchant.
Customer Name: {state.customer_name}
Amount Owed: ₹{state.amount_inr}
Failure Reason: {state.failure_reason}

INSTRUCTIONS:
1. Always respond in Hinglish (a natural mix of Hindi and English written in Latin script).
2. Be polite, professional, and empathetic. Never be aggressive or threatening.
3. Detect customer intent accurately (will_pay, need_time, dispute, confused, stop).
4. If customer says 'stop', 'mat karo', or wants to end the call, set should_end_call to true immediately.
5. If customer gives a promise date to pay later, extract it in YYYY-MM-DD format if possible.
6. Keep responses concise (2-3 sentences max).
7. Include the payment amount and failure reason naturally when explaining.
8. Offer a payment link via SMS if they agree to pay.

You MUST respond strictly in valid JSON format with the following schema:
{{
  "response_text": "Your Hinglish response here",
  "detected_intent": "one of: will_pay, need_time, dispute, confused, stop",
  "promise_date": "extracted date or null",
  "should_end_call": boolean
}}
"""
        return prompt

    async def generate_response(self, state: ConversationState, user_text: str) -> str:
        """Generate Hinglish response using LLM based on conversation state."""
        if not self.groq_client:
            return "Maaf kijiye, abhi system down hai."
            
        messages = [{"role": "system", "content": self._build_system_prompt(state)}]
        for msg in state.conversation_history[-5:]: # Keep last 5 messages for context
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        try:
            completion = await self.groq_client.chat.completions.create(
                model=settings.groq_llm_model,
                messages=messages,
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            
            response_data = json.loads(completion.choices[0].message.content)
            
            state.customer_intent = response_data.get("detected_intent")
            if response_data.get("promise_date"):
                try:
                    state.promise_date = datetime.strptime(response_data.get("promise_date"), "%Y-%m-%d")
                except ValueError:
                    pass
                    
            if response_data.get("should_end_call"):
                state.call_ended = True
                
            response_text = response_data.get("response_text", "Samajh nahi aaya, kripya dobara bataiye.")
            state.conversation_history.append({"role": "assistant", "content": response_text})
            return response_text
            
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return "Maaf kijiye, mujhe samajh nahi aaya."

    async def synthesize_speech(self, text: str) -> bytes:
        """Convert text to speech using Edge TTS (Hindi voice)."""
        voice = getattr(settings, "voice_tts_voice_hindi", "hi-IN-MadhurNeural")
        communicate = edge_tts.Communicate(text, voice)
        audio_data = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                audio_data.write(chunk['data'])
        return audio_data.getvalue()

    async def extract_intent(self, state: ConversationState) -> str:
        """Extract customer intent from conversation history."""
        return state.customer_intent or "unknown"

    async def generate_call_summary(self, state: ConversationState) -> str:
        """Generate a structured summary of the call."""
        if not self.groq_client:
            return f"Call ended with intent: {state.customer_intent}"
            
        try:
            history_text = "\\n".join([f"{msg['role']}: {msg['content']}" for msg in state.conversation_history])
            prompt = f"Summarize this recovery call briefly:\\n{history_text}"
            
            completion = await self.groq_client.chat.completions.create(
                model=settings.groq_llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            
            summary = completion.choices[0].message.content
            state.call_summary = summary
            return summary
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return "Could not generate summary."
