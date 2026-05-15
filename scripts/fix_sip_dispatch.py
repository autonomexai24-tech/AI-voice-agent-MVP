"""Fix SIP dispatch rule to use the correct agent_name.

Run:
    python scripts/fix_sip_dispatch.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voice_agent.config import load_project_dotenv

load_project_dotenv()

from livekit import api


EXPECTED_AGENT_NAME = "voice-calling-agent"
DISPATCH_RULE_ID = "SDR_scu8CqR6r8ew"


async def main() -> None:
    lk = api.LiveKitAPI()

    print(f"Updating dispatch rule {DISPATCH_RULE_ID}")
    print(f"Setting agent_name = '{EXPECTED_AGENT_NAME}'")
    print()

    updated_rule = api.SIPDispatchRuleInfo(
        sip_dispatch_rule_id=DISPATCH_RULE_ID,
        rule=api.SIPDispatchRule(
            dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                room_prefix="",
                pin="",
            ),
        ),
        trunk_ids=["ST_mwFofcTT3nCY"],
        name="Mind Dispatch Rule",
        room_config=api.RoomConfiguration(
            agents=[
                api.RoomAgentDispatch(agent_name=EXPECTED_AGENT_NAME),
            ],
        ),
    )

    resp = await lk.sip.update_dispatch_rule(DISPATCH_RULE_ID, updated_rule)
    print("✅ Dispatch rule updated successfully!")
    print()

    # Verify
    agents = list(resp.room_config.agents) if resp.HasField("room_config") else []
    for a in agents:
        print(f"  agent_name: {a.agent_name}")

    await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
