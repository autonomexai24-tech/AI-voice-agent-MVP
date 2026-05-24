# Phase 3A Real Booking Runtime Validation Checklist

## Required real-call runs
- [ ] Successful inbound SIP call books a real Cal.com appointment and captures the Cal.com UID.
- [ ] Caller repeats "yes" after confirmation; no second external Cal.com booking is created.
- [ ] Caller disconnects/reconnects during booking; collected fields and confirmed state survive.
- [ ] Caller interrupts during summary and then resumes; booking state is preserved.
- [ ] Caller switches English/Hinglish/Hindi during booking; collected fields are preserved.
- [ ] Caller asks for human takeover during booking; handoff includes booking progress.
- [ ] Cal.com availability failure produces a safe failure response, never a confirmation.
- [ ] Cal.com booking creation failure produces a safe failure response, never a confirmation.
- [ ] Persistence failure after external booking prevents AI confirmation and logs the failure.
- [ ] SMS sends only after Cal.com success, runtime validation success, and persistence success.

## Evidence required
- [ ] LiveKit room logs for each real call.
- [ ] Runtime logs for booking_started, booking_validated, booking_failed, booking_duplicate_detected, booking_retry, booking_confirmed, booking_persisted, booking_escalated, booking_interrupted, and booking_sms_sent.
- [ ] Cal.com dashboard or API evidence showing the external booking UID.
- [ ] Database row containing calcom_uid, external_status, confirmed_at, booking_validation_state, and booking_fingerprint.
- [ ] SMS provider evidence for successful confirmations only.
- [ ] Validation report from `scripts/run_realtime_validation.py analyze-logs` against the real-call log file.

## Pass/fail gates
- [ ] No response says "Your appointment is confirmed" unless Cal.com booking, validation, duplicate protection, and persistence all succeed.
- [ ] Duplicate attempts do not create duplicate external Cal.com bookings.
- [ ] Failed Cal.com or persistence paths produce only safe fallback responses.
- [ ] Booking continuity survives interruption, escalation, reconnect, and language switching.
- [ ] Real callers experience the booking as smooth and deterministic, not guessed by the AI.
