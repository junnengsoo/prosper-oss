from .models import Property


def render_property_facts(property_: Property) -> str:
    fields = [
        ("Property ID", property_.property_id),
        ("Property", property_.property_name),
        ("Address", property_.full_address),
        ("Status", property_.status),
        ("Type", property_.property_type),
        ("Bedrooms", property_.bedrooms),
        ("Bathrooms", property_.bathrooms),
        ("Asking rent", property_.asking_rent),
        ("Available from", property_.available_from),
        ("PropertyGuru URL", property_.property_url),
        ("PropertyGuru listing ID", property_.propertyguru_listing_id),
    ]
    return "\n".join(f"- {label}: {value}" for label, value in fields if value not in (None, ""))


def render_property_context(property_: Property) -> str:
    requirements = property_.landlord_profile_requirements.strip() or "None stated."
    caveats = property_.tenant_facing_caveats.strip() or "None stated."
    return "\n".join(
        [
            "PROPERTY FACTS:",
            render_property_facts(property_),
            "",
            "LANDLORD / PROFILE REQUIREMENTS:",
            requirements,
            "",
            "TENANT-FACING CAVEATS:",
            caveats,
        ]
    )


def render_qualification_property_context(property_: Property) -> str:
    requirements = property_.landlord_profile_requirements.strip() or "None stated."
    return "\n".join(
        [
            "PROPERTY FACTS:",
            render_property_facts(property_),
            "",
            "LANDLORD / PROFILE REQUIREMENTS:",
            requirements,
        ]
    )
