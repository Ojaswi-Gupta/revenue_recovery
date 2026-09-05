# RecovrAI — AI Revenue Recovery Agent

Built for the **Razorpay AI Buildathon** (Track 03: AI Revenue Recovery).

RecovrAI is an autonomous, multi-channel AI agent that detects revenue at risk (like failed payments), determines the right intervention, and executes a bounded, compliant recovery workflow across Email, WhatsApp, and Twilio Voice.

## 🚀 Key Features (The Bar Met)

1. **Real-time Webhook Ingestion**: Plugs directly into Razorpay webhooks. When a `payment.failed` event hits, the orchestrator immediately ingests the data and creates a bounding workflow.
2. **AI Diagnosis Engine**: Uses Groq/Gemini to diagnose *why* the payment failed based on error codes and customer history, assigning a confidence score to determine the next action (or escalating to a human if confidence is too low).
3. **Smart Channel Cascade**: Executes an escalating recovery ladder with bounded stopping rules:
   - **Hour 0**: Instant Payment Email
   - **Hour +4**: WhatsApp Direct Link (via Twilio)
   - **Day 3**: Automated Twilio Voice Call
4. **Quiet Hours Compliance**: Built-in compliance engine automatically suppresses outbound WhatsApp and Voice calls between 9 PM and 9 AM IST, rescheduling them for the next morning.
5. **Conversational Hinglish Voice Agent**: (Currently deploying) A real-time voice agent using Twilio Speech Recognition and Groq LLM that talks to the customer in Hinglish to secure a Promise-to-Pay.
6. **Self-Serve Promise Portal**: If a customer can't pay right now, the fallback checkout portal allows them to click "I need more time" and log a Promise-to-Pay date, pausing the automated cascade until salary day.
7. **Comprehensive Audit Trail**: Every single money action, API call, LLM decision, and customer interaction is logged immutably in the database for complete transparency.

## 🛠️ Tech Stack

- **Backend**: FastAPI (Python 3.11), SQLAlchemy, SQLite
- **AI Models**: Groq (Llama 3 / Mixtral), Google Gemini API (Fallback)
- **Voice / Messaging**: Twilio (WhatsApp, Programmable Voice), Edge TTS
- **Frontend**: Tailwind CSS, HTMX, Chart.js

## 🏃‍♂️ How to Run Locally

1. **Install dependencies**:
   ```bash
   pip install fastapi uvicorn sqlalchemy aiosqlite jinja2 httpx pydantic-settings twilio groq google-genai edge-tts
   ```
2. **Set up Environment Variables**:
   Copy `.env.example` to `.env` and fill in your keys (Razorpay, Twilio, Groq, Gemini).
3. **Run the server**:
   ```bash
   python -m recovrai.main
   ```
4. **Expose to Webhooks**:
   Run `ngrok http 8000` and copy the URL into your Razorpay Dashboard Webhook settings (listening for `payment.failed`).

## 🛑 Failure Recovery: What broke & How we got out

**The Problem**: While testing the agent's full recovery flow, we hit Razorpay's hard account limit for Test-Mode Payment Links (30 maximum). The Razorpay API started returning `400 Bad Request` limits, which normally crashes automated recovery loops and abandons the customer.

**The Solution**: We built a graceful fail-safe directly into the Orchestrator. When `razorpay_client.create_payment_link()` throws a Quota Exception, the system catches it dynamically and automatically spins up our own self-hosted fallback checkout portal (`/pay/{workflow_id}`). The agent continues the communication cascade without dropping a single customer, ensuring revenue is never lost to upstream API limitations.
