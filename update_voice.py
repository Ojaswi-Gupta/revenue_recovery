import os
import re

with open("../recovrai/api/voice.py", "r") as f:
    content = f.read()

# We will replace from twiml-callback to the end of the file
pattern = r'@router\.post\("/twiml-callback"\).*'
new_code = """@router.post("/gather-callback")
async def gather_callback(
    request: Request,
    SpeechResult: str = Form(None),
    CallSid: str = Form(None),
    To: str = Form(None)
):
    \"\"\"
    Twilio callback for the conversational speech recognition.
    User speaks, Twilio transcribes, we generate LLM response and return TwiML.
    \"\"\"
    from ..services.recovery_orchestrator import RecoveryOrchestrator
    
    workflow_id = request.query_params.get("workflow_id")
    
    if not workflow_id:
        return HTMLResponse(content="<Response><Hangup/></Response>", media_type="text/xml")
        
    # We will use the same _demo_states for simplicity, or _call_states
    agent = VoiceAgent()
    
    if workflow_id not in _demo_states:
        async with get_db_session() as session:
            stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow_id)
            result = await session.execute(stmt)
            workflow = result.scalar_one_or_none()
            
            if workflow:
                _demo_states[workflow_id] = await agent.start_conversation(workflow)
            else:
                return HTMLResponse(content="<Response><Hangup/></Response>", media_type="text/xml")
                
    state = _demo_states[workflow_id]
    
    twiml_response = "<Response>"
    
    if not SpeechResult:
        # No speech detected, prompt again or hangup
        state.turn_count += 1
        if state.turn_count >= state.max_turns:
            state.call_ended = True
            twiml_response += '<Say voice="Polly.Aditi" language="hi-IN">Humein kuch sunayi nahi diya. Hum call disconnect kar rahe hain.</Say><Hangup/>'
        else:
            base_url = str(request.base_url).rstrip("/")
            callback_url = f"{base_url}/api/voice/gather-callback?workflow_id={workflow_id}"
            twiml_response += f'''
                <Gather input="speech" action="{callback_url}" method="POST" language="hi-IN" speechTimeout="auto">
                    <Say voice="Polly.Aditi" language="hi-IN">Kripya boliye, hum sun rahe hain.</Say>
                </Gather>
            '''
    else:
        # Process the speech through LLM
        state.conversation_history.append({"role": "user", "content": SpeechResult})
        response_text = await agent.generate_response(state, SpeechResult)
        
        state.turn_count += 1
        if state.turn_count >= state.max_turns:
            state.call_ended = True
            
        if state.call_ended:
            twiml_response += f'<Say voice="Polly.Aditi" language="hi-IN">{response_text}</Say><Hangup/>'
            _demo_states.pop(workflow_id, None)
            
            # Save the call summary asynchronously or block
            import asyncio
            asyncio.create_task(agent.end_conversation(workflow_id, state))
            
        else:
            base_url = str(request.base_url).rstrip("/")
            callback_url = f"{base_url}/api/voice/gather-callback?workflow_id={workflow_id}"
            twiml_response += f'''
                <Gather input="speech" action="{callback_url}" method="POST" language="hi-IN" speechTimeout="auto">
                    <Say voice="Polly.Aditi" language="hi-IN">{response_text}</Say>
                </Gather>
            '''
            
    twiml_response += "</Response>"
    return HTMLResponse(content=twiml_response, media_type="text/xml")
"""

updated_content = re.sub(pattern, new_code, content, flags=re.DOTALL)

with open("../recovrai/api/voice.py", "w") as f:
    f.write(updated_content)
