# RecovrAI — AI Revenue Recovery Agent

> **Enterprise-grade AI agent that detects revenue at risk, diagnoses root causes, and executes bounded recovery workflows with full audit trails.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 🎯 What It Does

Indian merchants lose 5–15% of potential revenue to payment failures, checkout abandonment, subscription lapses, and overdue invoices. **RecovrAI** intercepts every class of revenue leak:

| Revenue Leak | Detection | Diagnosis | Recovery Action |
|---|---|---|---|
| **Payment Failure** | Razorpay webhook | Rule-based + LLM root cause | Auto-retry / Payment link / SMS |
| **Checkout Abandonment** | Session tracking | Stage analysis | Recovery SMS / Cart reminder |
| **Subscription Failure** | Subscription webhook | Failure pattern analysis | Payment link / Plan downgrade / Escalation |
| **Invoice Overdue** | Invoice aging | Overdue severity scoring | Email reminder / Voice call / Escalation |

## 🏗 Architecture

```
Browser/Webhook → FastAPI → Diagnosis Engine → Recovery Orchestrator → Channels
                                                      │
                              ┌────────────────────────┼────────────────────────┐
                              ▼                        ▼                        ▼
                         SMS/Email               Voice Bot               Payment Links
                        (Twilio)             (Groq + Edge TTS)          (Razorpay API)
                              │                        │                        │
                              └────────────────────────┼────────────────────────┘
                                                       ▼
                                              SQLite Audit Trail
                                                       ▼
                                              Dashboard (HTMX)
```

## 🚀 Quick Start

### 1. Clone & Install

```bash
cd recovrai
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys (see below)
```

**Required API Keys:**
| Service | Purpose | How to Get | Cost |
|---|---|---|---|
| Razorpay | Payment APIs | [dashboard.razorpay.com](https://dashboard.razorpay.com) → Test Mode → API Keys | Free (Test Mode) |
| Groq | LLM + STT | [console.groq.com](https://console.groq.com) | Free (30 RPM) |
| Gemini | LLM fallback | [aistudio.google.com](https://aistudio.google.com) | Free (15 RPM) |
| Twilio | SMS/Voice (optional) | [twilio.com](https://www.twilio.com) | Free trial |

> **Note:** The system works fully in **Demo Mode** without any API keys configured. All external calls are mocked with realistic responses.

### 3. Run the Server

```bash
python -m recovrai.main
```

Open **http://localhost:8000** in your browser.

### 4. Seed Data & Run Recovery

1. Click **"Seed Data"** on the dashboard — creates 105 synthetic failure records
2. Click **"Run Batch"** — processes all failures through the recovery pipeline
3. Watch the dashboard update with recovery metrics in real-time

## 🎤 Hinglish Voice Agent

The crown jewel — a conversational AI that calls customers in Hindi-English mix to recover failed payments.

### How It Works
```
Browser Mic → WebSocket → Groq Whisper (STT) → Groq Llama 3 (LLM) → Edge TTS (Hindi) → Browser Speaker
```

### Try It
1. Navigate to **Voice Console** in the sidebar
2. Select a workflow from the dropdown
3. Click the microphone button and speak
4. The agent responds in Hinglish: *"Namaste Aarav ji, aapka payment of ₹2,500 process nahi ho paya..."*

## 🛡 Compliance & Safety

Every action is **bounded, gated, and explainable**:

| Rule | Limit |
|---|---|
| Max contact attempts per customer | 5 |
| Quiet hours (no contact) | 9 PM – 9 AM IST |
| Cooldown between contacts | 4 hours minimum |
| Voice call frequency | Max 1 per 48 hours |
| Workflow lifetime | 7 days max |
| High-value auto-escalation | Above ₹50,000 |
| Low-confidence auto-escalation | Below 0.7 confidence |
| Customer opt-out | Immediate halt, all workflows |

## 📊 Metrics & Audit Trail

- **Recovery Rate**: `amount_recovered / amount_at_risk × 100`
- **Mean Time to Recovery**: Average time from detection to successful recovery
- **Channel Effectiveness**: Per-channel (SMS/Voice/Email) recovery rates
- **Promise Fulfillment Rate**: % of promise-to-pay commitments honored
- **Full Audit Trail**: Every action logged with actor, timestamp, and outcome

### Export
- CSV export of all workflows: `GET /api/metrics/export/csv`
- CSV export of audit trail: `GET /api/metrics/audit/export/csv`
- JSON metrics summary: `GET /api/metrics/summary`

## 🧪 Testing

```bash
# Run all tests
pytest recovrai/tests/ -v

# Run specific test suites
pytest recovrai/tests/test_diagnosis.py -v
pytest recovrai/tests/test_compliance.py -v
pytest recovrai/tests/test_recovery.py -v
pytest recovrai/tests/test_batch_metrics.py -v
```

## 📁 Project Structure

```
recovrai/
├── main.py                    # FastAPI app entry point
├── config.py                  # Configuration & constants
├── models/
│   ├── database.py            # SQLAlchemy async engine
│   ├── events.py              # Payment, Checkout, Subscription, Invoice events
│   ├── recovery.py            # Workflow, Action, AuditLog models
│   └── metrics.py             # Batch recovery metrics
├── services/
│   ├── diagnosis_engine.py    # Rule-based + LLM root cause analysis
│   ├── recovery_orchestrator.py  # Central state machine
│   ├── compliance.py          # Stopping rules & escalation
│   ├── notification.py        # SMS/Email/WhatsApp dispatcher
│   ├── voice_agent.py         # Hinglish voice bot
│   ├── promise_tracker.py     # Promise-to-pay tracking
│   └── razorpay_client.py     # Razorpay SDK wrapper
├── api/
│   ├── webhooks.py            # Razorpay webhook receiver + simulator
│   ├── dashboard.py           # Dashboard routes + HTMX
│   ├── voice.py               # WebSocket voice endpoint
│   └── metrics.py             # Metrics & CSV export API
├── templates/                 # Jinja2 HTML templates
├── static/                    # CSS & JavaScript
├── seed/
│   └── synthetic_data.py      # 105-record synthetic data generator
└── tests/                     # Pytest test suite
```

## 🔑 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Dashboard home |
| `GET` | `/workflow/{id}` | Workflow detail view |
| `GET` | `/audit` | Audit trail (filterable) |
| `GET` | `/report` | Batch report |
| `POST` | `/api/seed` | Seed synthetic data |
| `POST` | `/api/batch` | Run batch processing |
| `POST` | `/api/simulate-recovery/{id}` | Simulate payment received |
| `POST` | `/api/opt-out/{phone}` | Handle customer opt-out |
| `POST` | `/webhooks/razorpay` | Razorpay webhook receiver |
| `POST` | `/webhooks/simulate` | Event simulator |
| `GET` | `/api/metrics/summary` | Metrics JSON |
| `GET` | `/api/metrics/export/csv` | Export CSV |
| `WS` | `/voice/ws/{workflow_id}` | Voice agent WebSocket |
| `POST` | `/voice/demo` | Text-based voice demo |
| `GET` | `/health` | Health check |

## 📄 License

MIT
