"""
Voice agent API routes — WebSocket for real-time audio streaming and text-based demo endpoint.
"""

import base64
import json
import logging
import uuid
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models.database import get_db, get_db_session
from ..models.recovery import RecoveryWorkflow, RecoveryAction, AuditLog
from ..services.voice_agent import VoiceAgent, ConversationState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])
templates = Jinja2Templates(directory="recovrai/templates")


@router.get("/console", response_class=HTMLResponse)
async def voice_console(request: Request, db: AsyncSession = Depends(get_db)):
    """Serve the voice console HTML template."""
    # Fetch workflows that can be called
    stmt = select(RecoveryWorkflow).where(
        ~RecoveryWorkflow.status.in_(["recovered", "failed", "stopped_compliance"])
    ).order_by(RecoveryWorkflow.created_at.desc()).limit(50)
    result = await db.execute(stmt)
    workflows = list(result.scalars().all())

    return templates.TemplateResponse("voice_console.html", {
        "request": request,
        "workflows": workflows,
    })


@router.websocket("/ws/{workflow_id}")
async def voice_websocket_endpoint(websocket: WebSocket, workflow_id: str):
    """
    Real-time audio streaming WebSocket for the Hinglish voice agent.
    
    Protocol:
    - Client sends: binary audio chunks (WAV/WebM)
    - Server sends: JSON with { type, text, audio_base64, intent, call_ended }
    """
    await websocket.accept()
    agent = VoiceAgent()
    state = None

    try:
        # Fetch the workflow from the database
        async with get_db_session() as session:
            stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
            result = await session.execute(stmt)
            workflow = result.scalar_one_or_none()

        if not workflow:
            await websocket.send_json({
                "type": "error",
                "text": f"Workflow {workflow_id} not found",
            })
            await websocket.close()
            return

        # Start the conversation
        state = await agent.start_conversation(workflow)

        # Send the initial greeting
        greeting_text = state.conversation_history[-1]["content"]
        greeting_audio = await agent.synthesize_speech(greeting_text)

        await websocket.send_json({
            "type": "greeting",
            "text": greeting_text,
            "audio_base64": base64.b64encode(greeting_audio).decode("utf-8"),
            "intent": None,
            "call_ended": False,
        })

        # Conversation loop
        while not state.call_ended:
            try:
                # Receive audio from the client
                data = await websocket.receive()

                if "bytes" in data:
                    audio_bytes = data["bytes"]
                elif "text" in data:
                    # Text fallback mode
                    msg = json.loads(data["text"])
                    if msg.get("type") == "text":
                        user_text = msg.get("text", "")
                        state.conversation_history.append({"role": "user", "content": user_text})
                        response_text = await agent.generate_response(state, user_text)
                        response_audio = await agent.synthesize_speech(response_text)

                        state.turn_count += 1
                        if state.turn_count >= state.max_turns:
                            state.call_ended = True

                        await websocket.send_json({
                            "type": "response",
                            "text": response_text,
                            "audio_base64": base64.b64encode(response_audio).decode("utf-8"),
                            "intent": state.customer_intent,
                            "call_ended": state.call_ended,
                        })
                        continue
                    else:
                        continue
                else:
                    continue

                # Process audio: STT → LLM → TTS
                text_response, audio_response = await agent.process_user_speech(state, audio_bytes)

                if text_response:
                    await websocket.send_json({
                        "type": "response",
                        "text": text_response,
                        "audio_base64": base64.b64encode(audio_response).decode("utf-8") if audio_response else None,
                        "intent": state.customer_intent,
                        "call_ended": state.call_ended,
                    })

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error processing voice turn: {e}")
                await websocket.send_json({
                    "type": "error",
                    "text": f"Processing error: {str(e)}",
                })
                break

    except WebSocketDisconnect:
        logger.info(f"Voice WebSocket disconnected: {workflow_id}")
    except Exception as e:
        logger.error(f"Voice WebSocket error for {workflow_id}: {e}")
    finally:
        # Save call summary to audit trail
        if state and agent:
            try:
                summary = await agent.generate_call_summary(state)
                intent = await agent.extract_intent(state)

                async with get_db_session() as session:
                    # Save the action
                    action = RecoveryAction(
                        id=str(uuid.uuid4()),
                        workflow_id=workflow_id,
                        action_type="voice_call_completed",
                        channel="voice_call",
                        status="success",
                        call_duration_seconds=state.turn_count * 15,
                        call_summary=summary,
                        customer_intent=intent,
                        response_payload=json.dumps({
                            "turns": state.turn_count,
                            "intent": intent,
                            "promise_date": state.promise_date.isoformat() if state.promise_date else None,
                        }),
                        completed_at=datetime.utcnow(),
                    )
                    session.add(action)

                    # Save to audit log
                    audit = AuditLog(
                        id=str(uuid.uuid4()),
                        workflow_id=workflow_id,
                        action="voice_call_completed",
                        actor="voice_agent",
                        category="action",
                        details=(
                            f"Voice call completed. Turns: {state.turn_count}. "
                            f"Intent: {intent}. Summary: {summary[:200]}"
                        ),
                    )
                    session.add(audit)

                logger.info(f"Voice call summary saved for {workflow_id}: intent={intent}")
            except Exception as e:
                logger.error(f"Failed to save voice call audit: {e}")


# ─── Text-based Demo Endpoint ────────────────────────────────────────────────

class DemoRequest(BaseModel):
    workflow_id: str
    user_text: str


class DemoResponse(BaseModel):
    response_text: str
    audio_base64: str
    intent: str | None
    call_ended: bool
    turn: int


# In-memory state store for demo conversations
_demo_states: Dict[str, ConversationState] = {}


from fastapi import Form

@router.post("/demo")
async def text_demo(workflow_id: str = Form(...), message: str = Form(...)):
    """
    Text-based demo endpoint for testing the voice agent without a microphone.
    Accepts HTMX form data, returns text + audio response.
    """
    agent = VoiceAgent()

    if workflow_id not in _demo_states:
        # Fetch workflow and initialize conversation
        async with get_db_session() as session:
            stmt = select(RecoveryWorkflow).where(
                RecoveryWorkflow.id == workflow_id
            )
            result = await session.execute(stmt)
            workflow = result.scalar_one_or_none()

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        _demo_states[workflow_id] = await agent.start_conversation(workflow)

    state = _demo_states[workflow_id]

    if state.call_ended:
        # Clean up and return
        _demo_states.pop(workflow_id, None)
        raise HTTPException(status_code=400, detail="Conversation already ended")

    # Process the user's text
    state.conversation_history.append({"role": "user", "content": message})
    response_text = await agent.generate_response(state, message)
    audio_bytes = await agent.synthesize_speech(response_text)

    state.turn_count += 1
    if state.turn_count >= state.max_turns:
        state.call_ended = True

    if state.call_ended:
        _demo_states.pop(workflow_id, None)

    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8") if audio_bytes else ""
    
    html = f"""
    <div id="chat-container" hx-swap-oob="beforeend">
        <div class="flex justify-start my-4">
            <div class="max-w-[80%] rounded-2xl rounded-tl-sm px-4 py-2 bg-gray-700 text-gray-200 text-sm">
                {response_text}
            </div>
        </div>
        <audio autoplay src="data:audio/mp3;base64,{audio_b64}"></audio>
    </div>
    <div class="text-emerald-400">> Turn {state.turn_count} complete (Intent: {state.customer_intent})</div>
    """
    
    if state.call_ended:
        html += '<div class="text-red-400">> Call ended.</div>'
        
    return HTMLResponse(html)
