# Sarvam STT + TTS Skill

---

# 1. Purpose

This skill defines how Sarvam AI should be used inside the realtime AI voice receptionist system.

Sarvam is responsible for:
- Speech-To-Text (STT)
- Text-To-Speech (TTS)
- multilingual Indian language support
- automatic language detection

Sarvam is a core strategic provider because the project is optimized for Indian voice conversations.

---

# 2. System Role

Sarvam acts as:
- the AI hearing system
- the AI speaking system

---

# 3. Core Responsibilities

## 3.1 STT Responsibilities

Convert caller speech into text.

Responsibilities:
- realtime transcription
- multilingual understanding
- Indian accent handling
- telecom audio handling
- language detection

---

## 3.2 TTS Responsibilities

Convert AI responses into natural voice.

Responsibilities:
- human-like speech
- local language speech
- natural Indian accents
- conversational pacing

---

# 4. Architecture Position

```txt
Caller Audio
↓
LiveKit
↓
Sarvam STT
↓
Conversation Engine
↓
OpenAI
↓
Sarvam TTS
↓
LiveKit Playback
↓
Caller