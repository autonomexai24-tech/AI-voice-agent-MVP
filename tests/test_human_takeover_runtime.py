from __future__ import annotations

from dataclasses import replace

from voice_agent.human_takeover import (
    EscalationPriority,
    EscalationQueue,
    HumanOperator,
    HumanTakeoverRuntime,
    TakeoverOwnershipState,
)
from voice_agent.language import default_language_snapshot
from voice_agent.session_memory import CallSessionMemory


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_escalation_policy_is_deterministic_and_skips_faq_answers() -> None:
    runtime = HumanTakeoverRuntime()
    memory = CallSessionMemory(session_id="takeover-policy")

    faq = runtime.evaluate_escalation(
        "What are your hours?",
        memory=memory,
        language=default_language_snapshot(),
        faq_answer_exists=True,
    )
    emergency = runtime.evaluate_escalation(
        "This is urgent, the patient is bleeding",
        memory=memory,
        language=default_language_snapshot(),
        faq_answer_exists=True,
    )
    correction = runtime.evaluate_escalation(
        "Actually make it Friday",
        memory=memory,
        language=default_language_snapshot(),
    )

    assert faq.should_escalate is False
    assert emergency.should_escalate is True
    assert emergency.reason == "emergency"
    assert emergency.priority == EscalationPriority.EMERGENCY
    assert correction.should_escalate is False


def test_ai_to_human_transition_assigns_operator_and_builds_compact_handoff() -> None:
    runtime = HumanTakeoverRuntime()
    memory = CallSessionMemory(session_id="takeover-assign")
    memory.capture_booking_fields(
        customer_name="Ravi",
        phone_number="9876543210",
        service_type="root canal",
    )
    memory.runtime_memory.mark_unresolved_question(field_name="appointment_time")
    language = replace(
        default_language_snapshot(),
        active_language="kannada",
        dominant_language="kannada",
        previous_language="english",
        confidence=0.91,
        generation=2,
        openai_response_language="Kannada",
    )

    transition = runtime.request_takeover(
        memory=memory,
        language=language,
        reason="caller_escalation_request",
        workflow_state=memory.booking_stage.value,
    )

    assert transition.ownership_state == TakeoverOwnershipState.HUMAN_ACTIVE
    assert transition.assigned_operator_id == "supervisor-1"
    assert transition.transition_message == "Let me connect you with our clinic coordinator."
    assert transition.handoff_payload is not None
    assert transition.handoff_payload.caller_identity["name"] == "Ravi"
    assert transition.handoff_payload.booking_progress["captured"]["service_type"] == "root canal"
    assert transition.handoff_payload.unresolved_issues[0]["field_name"] == "appointment_time"
    assert "Preferred Language: Kannada" in transition.handoff_payload.language_note
    assert "Caller occasionally mixes English" in transition.handoff_payload.language_note
    assert len(transition.handoff_payload.memory_summary) <= 900


def test_queue_supports_five_ai_calls_and_two_human_operators() -> None:
    clock = FakeClock()
    runtime = HumanTakeoverRuntime(
        queue=EscalationQueue(clock=clock, max_active_escalations=5),
        clock=clock,
    )
    transitions = []
    for index in range(5):
        memory = CallSessionMemory(session_id=f"call-{index}")
        transitions.append(
            runtime.request_takeover(
                memory=memory,
                language=default_language_snapshot(),
                reason="caller_escalation_request",
            )
        )

    overflow_memory = CallSessionMemory(session_id="call-overflow")
    overflow = runtime.request_takeover(
        memory=overflow_memory,
        language=default_language_snapshot(),
        reason="caller_escalation_request",
    )

    assert [item.ownership_state for item in transitions[:2]] == [
        TakeoverOwnershipState.HUMAN_ACTIVE,
        TakeoverOwnershipState.HUMAN_ACTIVE,
    ]
    assert [item.ownership_state for item in transitions[2:]] == [
        TakeoverOwnershipState.WAITING_FOR_HUMAN,
        TakeoverOwnershipState.WAITING_FOR_HUMAN,
        TakeoverOwnershipState.WAITING_FOR_HUMAN,
    ]
    assert overflow.ownership_state == TakeoverOwnershipState.AI_RESUMED
    assert overflow.recovery_reason == "queue_overflow"
    assert runtime.snapshot("call-2").queue_position == 1


def test_human_to_ai_recovery_preserves_and_updates_memory() -> None:
    runtime = HumanTakeoverRuntime()
    memory = CallSessionMemory(session_id="takeover-resume")
    memory.capture_booking_fields(
        customer_name="Ravi",
        phone_number="9876543210",
        service_type="dental cleaning",
        appointment_date="tomorrow",
    )
    runtime.request_takeover(
        memory=memory,
        language=default_language_snapshot(),
        reason="booking_failure_loop",
        workflow_state=memory.booking_stage.value,
    )

    resumed = runtime.resume_ai(
        memory=memory,
        language=default_language_snapshot(),
        human_resolution={
            "booking_updates": {
                "appointment_date": "friday",
                "appointment_time": "6 pm",
                "doctor_preference": "any doctor",
            },
            "resolved_fields": ("appointment_time",),
        },
    )

    assert resumed.ownership_state == TakeoverOwnershipState.AI_RESUMED
    assert memory.booking.caller_name == "Ravi"
    assert memory.booking.preferred_date == "friday"
    assert memory.booking.preferred_time == "6 pm"
    assert memory.runtime_memory.snapshot().booking.values["appointment_time"] == "6 pm"
    assert memory.runtime_memory.snapshot().correction.active_fields == ("appointment_date",)


def test_escalation_timeout_recovers_to_ai_without_losing_handoff() -> None:
    clock = FakeClock()
    runtime = HumanTakeoverRuntime(
        queue=EscalationQueue(
            operators=(),
            timeout_seconds=5.0,
            clock=clock,
        ),
        clock=clock,
    )
    memory = CallSessionMemory(session_id="takeover-timeout")

    transition = runtime.request_takeover(
        memory=memory,
        language=default_language_snapshot(),
        reason="unsupported_request",
    )
    clock.advance(6.0)
    recovered = runtime.recover_timed_out_escalations()

    assert transition.ownership_state == TakeoverOwnershipState.WAITING_FOR_HUMAN
    assert recovered[0].session_id == memory.session_id
    assert recovered[0].ownership_state == TakeoverOwnershipState.AI_RESUMED
    assert recovered[0].handoff_payload is not None
    assert runtime.snapshot(memory.session_id).last_recovery_reason == "escalation_timeout"


def test_supervisor_disconnect_requeues_or_recovers_safely() -> None:
    clock = FakeClock()
    runtime = HumanTakeoverRuntime(
        queue=EscalationQueue(
            operators=(HumanOperator("supervisor-1", "Clinic Coordinator 1"),),
            clock=clock,
        ),
        clock=clock,
    )
    first = CallSessionMemory(session_id="disconnect-first")
    second = CallSessionMemory(session_id="disconnect-second")
    runtime.request_takeover(
        memory=first,
        language=default_language_snapshot(),
        reason="caller_escalation_request",
    )
    runtime.request_takeover(
        memory=second,
        language=default_language_snapshot(),
        reason="caller_escalation_request",
    )

    transition = runtime.handle_supervisor_disconnect(
        memory=first,
        language=default_language_snapshot(),
    )

    assert transition.ownership_state == TakeoverOwnershipState.HUMAN_ACTIVE
    assert transition.assigned_operator_id == "supervisor-1"
    assert runtime.snapshot(first.session_id).ownership_state == TakeoverOwnershipState.HUMAN_ACTIVE


def test_takeover_state_snapshot_persists_ownership_and_queue_status() -> None:
    clock = FakeClock()
    runtime = HumanTakeoverRuntime(
        queue=EscalationQueue(
            operators=(HumanOperator("supervisor-1", "Clinic Coordinator 1"),),
            clock=clock,
        ),
        clock=clock,
    )
    first = CallSessionMemory(session_id="snapshot-first")
    second = CallSessionMemory(session_id="snapshot-second")
    runtime.request_takeover(
        memory=first,
        language=default_language_snapshot(),
        reason="caller_escalation_request",
    )
    runtime.request_takeover(
        memory=second,
        language=default_language_snapshot(),
        reason="caller_escalation_request",
    )

    first_snapshot = runtime.snapshot(first.session_id)
    second_snapshot = runtime.snapshot(second.session_id)

    assert first_snapshot.ownership_state == TakeoverOwnershipState.HUMAN_ACTIVE
    assert first_snapshot.active_human_count == 1
    assert second_snapshot.ownership_state == TakeoverOwnershipState.WAITING_FOR_HUMAN
    assert second_snapshot.queued is True
    assert second_snapshot.queue_position == 1
