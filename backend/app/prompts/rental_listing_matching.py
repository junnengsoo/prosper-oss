from __future__ import annotations

from .base import PromptTemplate


RENTAL_LISTING_MATCHING_PROMPT = PromptTemplate(
    stage="rental_listing_matching",
    system="You match Singapore rental WhatsApp enquiries to configured rental listing records. Return only valid JSON.",
    user_template="""You are helping a Singapore rental agent handle a new WhatsApp rental enquiry.

Your task is ONLY to match the enquiry to one rental listing in the agent's listing list.

PROPERTY LIST:
\"\"\"
{property_list}
\"\"\"

Match priority:
1. If enquiry has a PropertyGuru URL, extract the listing ID and match it against propertyguru_listing_id.
2. If no listing ID match, match by property_name or full_address.
3. If exactly one property clearly matches, use matched.
4. If no property/listing is mentioned, use no_property_mentioned.
5. If a property/listing is mentioned but not found in PROPERTY LIST, use unmatched_property.
6. If multiple properties could match, use ambiguous_multiple_matches.

Important:
- Do not use no_property_mentioned if the enquiry includes a property name, address, or listing URL.
- Match against all configured properties even if status is unavailable. Availability is handled after matching.
- If enquiry includes a PropertyGuru listing URL and the extracted listing ID does not match any propertyguru_listing_id in PROPERTY LIST, return unmatched_property. Do not fall back to property_name or full_address unless the URL has no usable listing ID.
- Do not extract or judge tenant profile information in this stage.
- Return only valid JSON.

JSON format:
{
  "match_status": "matched | no_property_mentioned | unmatched_property | ambiguous_multiple_matches",
  "mentioned_property_raw": "",
  "mentioned_listing_url": "",
  "extracted_listing_id": "",
  "matched_by": "propertyguru_listing_id | property_name | full_address | none",
  "matched_properties": [
    {
      "property_id": "",
      "property_name": "",
      "reason": ""
    }
  ],
  "reason": ""
}""",
)
