import type { PropertyInput, PropertyRecord } from "./api";

export type EditorSection = "facts" | "gallery" | "auto_replies";
export type AutoReplyField = "availableInitial";
export type PropertyRequiredField = "property_name" | "status" | "property_url";

export type MediaPathForm = {
  file_path: string;
  caption: string;
  sort_order: number;
  enabled: boolean;
  media_type: "photo" | "video";
};

export const PROPERTY_REQUIRED_FIELDS: PropertyRequiredField[] = ["property_name", "status", "property_url"];

export const EDITOR_SECTIONS: Array<{ key: EditorSection; label: string }> = [
  { key: "facts", label: "Listing Facts" },
  { key: "gallery", label: "Gallery" },
  { key: "auto_replies", label: "Auto Replies" },
];

export const EMPTY_PROPERTY_FORM: PropertyInput = {
  property_id: "",
  property_name: "",
  status: "available",
  property_type: "rental",
  bedrooms: null,
  bathrooms: null,
  asking_rent: null,
  available_from: "",
  full_address: "",
  property_url: "",
  propertyguru_listing_id: "",
  tenant_facing_caveats: "",
};

export const EMPTY_MEDIA_PATH_FORM: MediaPathForm = {
  file_path: "",
  caption: "",
  sort_order: 0,
  enabled: true,
  media_type: "photo",
};

export function propertyToInput(property: PropertyRecord | null | undefined): PropertyInput {
  if (!property) return { ...EMPTY_PROPERTY_FORM };
  return {
    property_id: property.property_id,
    property_name: property.property_name,
    status: property.status,
    property_type: "rental",
    bedrooms: property.bedrooms,
    bathrooms: property.bathrooms,
    asking_rent: property.asking_rent,
    available_from: property.available_from ?? "",
    full_address: property.full_address ?? "",
    property_url: property.property_url ?? "",
    propertyguru_listing_id: property.propertyguru_listing_id ?? "",
    tenant_facing_caveats: property.tenant_facing_caveats ?? "",
  };
}

export function generatedPropertyId(name: string, existingIds: Set<string>): string {
  const base = name
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 36) || `PROPERTY-${Date.now().toString(36).toUpperCase()}`;
  let candidate = `PROP-${base}`;
  let suffix = 2;
  while (existingIds.has(candidate)) {
    candidate = `PROP-${base}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}
