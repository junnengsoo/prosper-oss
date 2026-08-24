from __future__ import annotations

from .base import PromptTemplate


TRIAGE_PROMPT = PromptTemplate(
    stage="triage",
    system="You classify Singapore rental WhatsApp threads. Return only valid JSON.",
    user_template="""You are helping a Singapore rental agent triage WhatsApp messages.

Your task is ONLY to decide whether this WhatsApp thread is an initial rental enquiry.

Classify as an initial rental enquiry if the message is from a tenant, tenant's agent, or rental prospect asking about:
- rental listing availability
- viewing
- rent
- listing details
- whether a rental unit is still available
- similar rental options
- general rental interest

Do NOT classify as an initial rental enquiry if it is:
- landlord/owner update
- existing deal follow-up
- viewing reschedule for an existing deal
- contract/offer/payment matter
- purchase or asking-price enquiry
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
  "is_initial_rental_enquiry": true,
  "confidence": "high",
  "reason": ""
}""",
)
