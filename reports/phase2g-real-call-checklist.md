# Phase 2G Real SIP and LiveKit Validation Checklist

## Required runs
- [ ] 5 inbound SIP calls from real mobile devices across two networks.
- [ ] Kannada, Telugu, Marathi, Hindi, English, Hinglish, and mixed-language calls.
- [ ] 15 minute call, then 30 minute call, with booking corrections after minute 10.
- [ ] 5 simultaneous AI calls with 2 human operators connected.
- [ ] Rapid interruption run: 10 caller barge-ins across active playback.
- [ ] AI to human takeover, supervisor disconnect, and AI recovery on the same call.
- [ ] OpenAI latency spike, Sarvam TTS delay, retrieval failure, and websocket disconnect drills.

## Evidence required
- [ ] LiveKit room logs with connect, participant, stream, disconnect, and cleanup events.
- [ ] EasyPanel VPS CPU, RAM, process restart, and app log excerpts for each run.
- [ ] Runtime JSON logs containing response_latency, orchestration_latency, cache hits/misses, compression_ratio, memory_growth, escalation events, queue_wait_time, and interruption_recovery.
- [ ] Call notes from mobile testers rating perceived responsiveness and receptionist realism.
- [ ] Validation JSON report produced by scripts/run_realtime_validation.py analyze-logs.

## Pass/fail gates
- [ ] Total response p95 below 2500 ms and no sustained latency growth across long calls.
- [ ] Interruption recovery p95 below 400 ms with playback stopped and booking memory preserved.
- [ ] No LiveKit room leaks, stream desync, or unrecovered websocket disconnects.
- [ ] 5 concurrent calls do not starve the event loop or exhaust KVM2 RAM.
- [ ] Human takeover and AI recovery preserve language, booking state, and escalation reason.
- [ ] Real callers report the system sounded like a receptionist, not a broken bot.
