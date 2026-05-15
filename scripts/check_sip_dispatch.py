"""Diagnostic: list SIP trunks, dispatch rules, and verify agent_name binding.

Run:
    python scripts/check_sip_dispatch.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Load .env from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voice_agent.config import load_project_dotenv

load_project_dotenv()

from livekit import api


EXPECTED_AGENT_NAME = "voice-calling-agent"


async def main() -> None:
    lk = api.LiveKitAPI()

    # ── 1. List SIP Inbound Trunks ──────────────────────────────
    print("\n═══ SIP INBOUND TRUNKS ═══")
    trunks_resp = await lk.sip.list_sip_inbound_trunk(api.ListSIPInboundTrunkRequest())
    trunks = list(trunks_resp.items)
    if not trunks:
        print("  ⚠  NO inbound trunks found!")
    for t in trunks:
        print(f"  Trunk ID   : {t.sip_trunk_id}")
        print(f"  Name       : {t.name}")
        print(f"  Numbers    : {list(t.numbers)}")
        print(f"  Allowed IPs: {list(t.allowed_addresses)}")
        print()

    # ── 2. List SIP Dispatch Rules ──────────────────────────────
    print("═══ SIP DISPATCH RULES ═══")
    rules_resp = await lk.sip.list_sip_dispatch_rule(api.ListSIPDispatchRuleRequest())
    rules = list(rules_resp.items)
    if not rules:
        print("  ⚠  NO dispatch rules found!")
        print("  ➜  This is the ROOT CAUSE: no dispatch rule = no job sent to worker.")
        print()
        print("  FIX: Create a dispatch rule with SIPDispatchRuleIndividual")
        print(f"       and room_config.agents = [{{agent_name: '{EXPECTED_AGENT_NAME}'}}]")
        await lk.aclose()
        return

    agent_name_found = False
    for r in rules:
        print(f"  Rule ID    : {r.sip_dispatch_rule_id}")
        print(f"  Name       : {r.name}")
        print(f"  Trunk IDs  : {list(r.trunk_ids)}")
        print(f"  Room Preset: {r.room_preset!r}")

        rule = r.rule
        if rule.HasField("dispatch_rule_individual"):
            ind = rule.dispatch_rule_individual
            print(f"  Type       : SIPDispatchRuleIndividual")
            print(f"  Room Prefix: {ind.room_prefix!r}")
            print(f"  Pin        : {ind.pin!r}")
        elif rule.HasField("dispatch_rule_direct"):
            direct = rule.dispatch_rule_direct
            print(f"  Type       : SIPDispatchRuleDirect")
            print(f"  Room Name  : {direct.room_name!r}")
            print(f"  Pin        : {direct.pin!r}")
        elif rule.HasField("dispatch_rule_callee"):
            callee = rule.dispatch_rule_callee
            print(f"  Type       : SIPDispatchRuleCallee")
        else:
            print(f"  Type       : UNKNOWN")

        # Check room_config agents
        if r.HasField("room_config"):
            rc = r.room_config
            agents = list(rc.agents)
            print(f"  Room Config: agents={[{'agent_name': a.agent_name, 'metadata': a.metadata} for a in agents]}")
            for a in agents:
                if a.agent_name == EXPECTED_AGENT_NAME:
                    agent_name_found = True
                    print(f"  ✅ agent_name '{EXPECTED_AGENT_NAME}' MATCHES")
        else:
            print(f"  Room Config: NOT SET")

        # Check metadata/attributes
        if r.metadata:
            print(f"  Metadata   : {r.metadata}")
        if r.attributes:
            print(f"  Attributes : {dict(r.attributes)}")
        print()

    # ── 3. Verdict ──────────────────────────────────────────────
    print("═══ VERDICT ═══")
    if agent_name_found:
        print(f"  ✅ Dispatch rule correctly binds to agent_name='{EXPECTED_AGENT_NAME}'")
        print("  ➜  If worker still doesn't receive jobs, check:")
        print("     - Is another worker registered with the same agent_name?")
        print("     - Is the trunk_id in the dispatch rule correct?")
        print("     - Does the LiveKit Cloud region match?")
    else:
        print(f"  ❌ NO dispatch rule has room_config.agents with agent_name='{EXPECTED_AGENT_NAME}'")
        print()
        print("  THIS IS THE ROOT CAUSE.")
        print("  LiveKit creates the SIP room but never dispatches a job to your worker.")
        print()
        print("  FIX OPTIONS:")
        print("  1. Run: python scripts/fix_sip_dispatch.py")
        print("  2. Or manually update the dispatch rule in LiveKit Cloud to include:")
        print(f"     room_config.agents = [{{agent_name: '{EXPECTED_AGENT_NAME}'}}]")

    await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
