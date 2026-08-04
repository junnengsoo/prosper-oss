from __future__ import annotations

from .base import PromptTemplate


TRIAGE_PROMPT = PromptTemplate(
    stage="triage",
    system="You classify Singapore property WhatsApp threads. Return only valid JSON.",
    user_template="""You are helping a Singapore property agent triage WhatsApp messages.

Your task is ONLY to decide whether this WhatsApp thread is an initial property enquiry.

Classify as an initial property enquiry if the message is from a tenant/buyer/agent/prospect asking about:
- property availability
- viewing
- rent
- sale price
- purchase interest
- listing details
- whether a unit is still available
- similar rental or sale options
- general rental or purchase interest

Do NOT classify as an initial property enquiry if it is:
- landlord/owner update
- existing deal follow-up
- viewing reschedule for an existing deal
- contract/offer/payment matter
- personal/irrelevant message
- spam
- generic acknowledgement like "ok", "thanks", "sent"
- unclear without enough context

KNOWN CONTEXT:
\"\"\"
{known_context}
\"\"\"

Return only JSON in this format:

{
  "is_initial_property_enquiry": true,
  "confidence": "high",
  "reason": ""
}""",
)
