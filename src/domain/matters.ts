/** Matter intake. Mirrors `server/src/atlas_argus/domain/matters.py`. */

export interface NewMatterInput {
  name: string;
  aircraft: string;
  accidentDate: string;
  location: string;
  matterType: string;
  docketRef: string;
}

export const MATTER_TYPES: { value: string; label: string }[] = [
  { value: "wrongful_death", label: "Wrongful death" },
  { value: "personal_injury", label: "Personal injury" },
  { value: "property_damage", label: "Property damage" },
  { value: "subrogation", label: "Subrogation" },
  { value: "regulatory_enforcement", label: "Regulatory enforcement" },
  { value: "insurance_coverage", label: "Insurance coverage" },
];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Advisory: the server re-validates and is authoritative. This exists so the
 *  submit button can explain what is missing instead of the user discovering
 *  it through a rejected request. */
export function validateNewMatter(input: NewMatterInput): string | null {
  if (!input.name.trim()) return "Matter name is required.";
  if (!input.aircraft.trim()) return "Aircraft is required.";
  if (!ISO_DATE.test(input.accidentDate.trim())) {
    return "Accident date must be an ISO date (YYYY-MM-DD).";
  }
  const [year, month, day] = input.accidentDate.trim().split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    return "Accident date is not a real date.";
  }
  if (!input.location.trim()) return "Location is required.";
  if (!MATTER_TYPES.some((t) => t.value === input.matterType)) {
    return "Select a matter type.";
  }
  if (!input.docketRef.trim()) return "Docket reference is required.";
  return null;
}
