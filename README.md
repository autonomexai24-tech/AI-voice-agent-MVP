# AI Voice Receptionist Platform

Realtime multilingual AI voice receptionist platform optimized for low-latency phone conversations, interruption handling, and reliable appointment booking.

---

# Overview

This project builds a realtime conversational AI system capable of:

* answering inbound business phone calls
* speaking naturally in Indian languages
* handling interruptions naturally
* booking appointments automatically
* integrating with CRM and calendar systems
* supporting configurable conversational behavior
* operating with low-latency realtime audio streaming

The system is designed as:

```txt
Realtime Conversational Infrastructure
```

NOT:

* a simple chatbot
* a traditional CRM app
* a basic IVR system

The core product focus is:

* realtime voice orchestration
* multilingual conversation quality
* conversational realism
* rapid conversational iteration
* reliable workflow execution

---

# Current MVP Goal

The current MVP focuses on:

```txt
Reliable AI appointment booking over realtime phone calls.
```

The MVP should:

* answer calls naturally
* auto-detect customer language
* respond conversationally
* follow business rules
* collect booking information
* create appointments through Cal.com
* log calls and transcripts
* provide CRM visibility

---

# Current System Status

## Infrastructure Completed

### Telephony

* ✅ VoBiz SIP integration
* ✅ LiveKit SIP ingress
* ✅ Dynamic room dispatch
* ✅ LiveKit AgentSession runtime

### Realtime Voice Runtime

* ✅ Realtime audio streaming
* ✅ Duplex voice conversation
* ✅ Low-latency orchestration
* ✅ Interruption-capable architecture

### Speech Systems

* ✅ Sarvam STT
* ✅ Sarvam TTS
* ✅ Realtime transcription
* ✅ Realtime playback
* ✅ Multilingual audio support

### AI Runtime

* ✅ OpenAI GPT-4o-mini integration
* ✅ Business prompt injection
* ✅ Realtime AI responses

### Observability

* ✅ Structured logging
* ✅ Runtime diagnostics
* ✅ Transcript logging
* ✅ Playback event logging

---

# MVP Scope

The MVP includes:

* realtime AI receptionist
* appointment booking workflow
* frontend dashboard
* CRM
* call logs
* recordings
* prompt management
* multilingual support
* Cal.com integration

The MVP intentionally avoids:

* multi-tenant billing
* enterprise auth
* Kubernetes
* advanced analytics
* outbound calling
* large-scale SaaS infrastructure

---

# Core Architecture

```txt
Caller
↓
VoBiz SIP
↓
LiveKit Cloud
↓
Python Voice Runtime
↓
Sarvam STT
↓
Conversation Engine
↓
OpenAI
↓
Sarvam TTS
↓
Realtime Audio Playback
↓
Caller
```

Additional platform services:

```txt
FastAPI Backend
+
PostgreSQL
+
Next.js Frontend
+
Cal.com Integration
```

---

# Tech Stack

## Voice Infrastructure

* VoBiz SIP
* LiveKit Cloud
* livekit-agents

## AI Stack

* Sarvam AI
* OpenAI GPT-4o-mini

## Backend

* Python 3.10+
* FastAPI
* PostgreSQL

## Frontend

* Next.js
* TypeScript

## Future Deployment

* Docker
* EasyPanel

---

# Frontend MVP Pages

## Dashboard

Business metrics:

* total calls
* answered calls
* missed calls
* booking conversion
* average call duration
* successful bookings
* human transfer rate

---

## CRM + Calendar

Initial entities:

* contacts
* call history
* appointment history
* transcripts
* recordings
* missed calls

Booking provider:

```txt
Cal.com
```

---

## Agent Settings

Configurable settings:

* greetings
* tone
* business prompts
* refusal policies
* booking instructions
* escalation rules
* default language
* ambience settings

Prompt changes apply:

```txt
New calls only
```

---

## Call Logs

Each call log includes:

* transcript
* recording playback
* booking outcome
* call duration
* language used
* AI response history

---

# Multilingual Support

Primary target languages:

* Hindi
* Kannada
* Tamil
* Telugu
* Malayalam
* Marathi
* Punjabi
* Bengali
* Hinglish

Features:

* automatic language detection
* realtime language switching
* code-mixed conversation support
* India-first telecom audio optimization

Example:

```txt
Business default = Kannada
Caller speaks Hindi
AI switches naturally to Hindi
```

---

# Appointment Workflow

The MVP prioritizes:

```txt
Reliable appointment booking
```

Initial booking fields:

* customer name
* phone number
* appointment date
* appointment time
* service type
* doctor/staff
* notes

Initial constraints:

* single booking per call
* single calendar initially
* automatic booking confirmation

---

# Prompt Architecture

The platform uses:

```txt
Modular Prompt Architecture
```

Prompt layers:

* identity
* tone
* business rules
* booking rules
* multilingual rules
* refusal rules
* escalation rules
* interruption behavior

Future improvements should NOT require major rewrites.

---

# Development Philosophy

This project prioritizes:

* realtime conversational quality
* low latency
* interruption realism
* modularity
* fast iteration
* provider flexibility
* scalable orchestration

The architecture is optimized for:

```txt
Rapid conversational improvement velocity
```

---

# Local Development

## Requirements

* Python 3.10+
* Node.js 20+
* PostgreSQL
* npm

---

# Backend Setup

```bash
python -m venv .venv
```

Activate virtual environment:

### Windows

```bash
.\.venv\Scripts\activate
```

Install backend dependencies:

```bash
pip install -e .[dev]
```

Copy environment file:

```bash
Copy-Item .env.example .env
```

Fill `.env` with:

* LiveKit credentials
* Sarvam API keys
* OpenAI API key
* PostgreSQL connection
* Cal.com configuration

The frontend has a separate local environment file:

```bash
Copy-Item frontend/.env.example frontend/.env.local
```

Default local API URL:

```txt
http://127.0.0.1:8000
```

---

# Run Voice Runtime

```bash
python -m voice_agent.worker dev
```

---

# Frontend Setup

Install frontend dependencies:

```bash
npm install
```

Run frontend:

```bash
npm run dev
```

---

# Backend API Setup

Run FastAPI server:

```bash
uvicorn api.main:app --reload
```

---

# Full Local Stack Workflow

From a PowerShell session:

```powershell
.\scripts\start-local-stack.ps1
```

To include the realtime runtime worker:

```powershell
.\scripts\start-local-stack.ps1 -WithWorker
```

Validate the local API/frontend path:

```powershell
.\scripts\check-local-stack.ps1
```

Expected local sequence:

```txt
PostgreSQL running
→ FastAPI on http://127.0.0.1:8000
→ Next.js on http://127.0.0.1:3000
→ optional realtime worker
```

---

# Docker + EasyPanel Deployment

Phase 3A deployment foundation is documented in [DEPLOYMENT.md](DEPLOYMENT.md).

Local container smoke path:

```powershell
Copy-Item .env.docker.example .env.docker
docker compose --env-file .env.docker up --build
```

The container stack runs:

```txt
PostgreSQL
→ FastAPI backend on 0.0.0.0:8000
→ Next.js standalone frontend on 0.0.0.0:3000
```

Backend health endpoints:

```txt
/healthz
/readyz
```

---

# Expected Runtime Flow

```txt
Inbound call
→ LiveKit SIP room
→ Worker joins room
→ Caller audio streams
→ Sarvam STT transcribes
→ OpenAI generates response
→ Sarvam TTS generates speech
→ Audio streams back to caller
```

---

# Expected Runtime Logs

```json
{"event":"caller_transcript","transcript":"I need an appointment","is_final":true}
{"event":"ai_response","response":"Of course. What day would you like to visit?"}
{"event":"ai_speech_ready","duration_ms":1700}
{"event":"ai_playback_completed","frame_count":82}
```

---

# Project Structure

```txt
src/
├── voice_agent/
│   ├── worker.py
│   ├── agent.py
│   ├── prompts/
│   ├── providers/
│   ├── booking/
│   ├── conversation/
│   ├── multilingual/
│   └── logging/
│
frontend/
├── app/
├── components/
├── crm/
├── dashboard/
├── settings/
└── call-logs/
│
api/
├── routes/
├── services/
├── database/
└── models/
```

---

# Current Roadmap

## Phase 2

Prompt Architecture System

## Phase 3

Conversation State Machine

## Phase 4

Booking Workflow Engine

## Phase 5

FastAPI + PostgreSQL Persistence

## Phase 6

Frontend MVP

## Phase 7

CRM + Call Logs

## Phase 8

Cal.com Integration

## Phase 9

Multilingual Intelligence Layer

## Phase 10

Agent Role Expansion

---

# Future Expansion

Future roadmap includes:

* multi-tenant SaaS
* authentication
* advanced analytics
* outbound calling
* sales agents
* support agents
* campaign orchestration
* advanced CRM
* self-hosted infrastructure

These are intentionally deferred.

---

# Core Product Principle

This project is NOT:

* a generic chatbot
* a dashboard wrapper
* a basic CRM SaaS

This project IS:

* realtime conversational infrastructure
* multilingual voice orchestration
* interruption-aware conversational runtime
* configurable conversational agent platform

The core moat is:

* realtime orchestration
* conversational realism
* interruption quality
* multilingual behavior
* low latency
* rapid conversational iteration
* reliable workflow execution
