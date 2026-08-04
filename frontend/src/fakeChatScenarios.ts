export type FakeChatScenario = {
  id: string;
  label: string;
  displayName: string;
  expectedRunStatus: FakeScenarioRunStatus;
  expectedStage: string;
  expectedAction: string;
  verify: string;
  text: string;
};

export type FakeScenarioRunStatus = "created_conversation" | "skipped";

export type FakeScenarioEvaluation = {
  tone: "success" | "warning";
  label: string;
  detail: string;
};

export const FAKE_CHAT_SCENARIOS: FakeChatScenario[] = [
  {
    id: "available_profile",
    label: "Available unit + profile",
    displayName: "Demo Tenant",
    expectedRunStatus: "created_conversation",
    expectedStage: "Unit matching or qualification",
    expectedAction: "Availability and qualification follow-up based on the matched property.",
    verify: "Confirm that Maple Grove Residence is matched and the structured profile is available for qualification.",
    text: `Hi, is Maple Grove Residence available?

We are 4 family members. Budget is 3400. We can move in immediately and need a one-year lease.`,
  },
  {
    id: "ambiguous_property",
    label: "Ambiguous enquiry",
    displayName: "Unspecified Tenant",
    expectedRunStatus: "created_conversation",
    expectedStage: "End or manual review",
    expectedAction: "No confident outbound reply when the enquiry does not identify a property clearly.",
    verify: "Confirm that Prosper preserves the enquiry for review instead of inventing a confident property match.",
    text: "Hi, do you have a suitable two-bedroom rental near the city? Budget is around 3000.",
  },
  {
    id: "unavailable_property",
    label: "Unavailable listing",
    displayName: "Pending Tenant",
    expectedRunStatus: "created_conversation",
    expectedStage: "End or handoff",
    expectedAction: "Unavailable or handoff action for a listing with a pending offer.",
    verify: "Confirm that Riverside Lofts is recognized but is not treated as available.",
    text: "Hi, is Riverside Lofts still available? We need two bedrooms and can move in immediately.",
  },
  {
    id: "incomplete_profile",
    label: "Incomplete profile",
    displayName: "Follow-up Tenant",
    expectedRunStatus: "created_conversation",
    expectedStage: "Qualification after operator run",
    expectedAction: "A focused follow-up asking only for missing qualification details.",
    verify: "Run qualification and confirm that Prosper asks for missing fields instead of rejecting the enquiry.",
    text: "Hi, is Maple Grove Residence available? We are a family of four and can move in soon.",
  },
  {
    id: "not_enquiry",
    label: "Non-enquiry message",
    displayName: "Existing Contact",
    expectedRunStatus: "skipped",
    expectedStage: "End or skipped",
    expectedAction: "No outbound action.",
    verify: "Confirm that a generic acknowledgement does not create an actionable enquiry.",
    text: "Thanks, noted.",
  },
  {
    id: "qualification_match",
    label: "Qualification match",
    displayName: "Qualified Tenant",
    expectedRunStatus: "created_conversation",
    expectedStage: "End after qualification",
    expectedAction: "A match or viewing response based on the configured Playbook.",
    verify: "Run qualification and inspect the structured stage result and outbound action.",
    text: `Hi, Maple Grove Residence is the one I am asking about.

Budget: 3400
Occupants: 4 family members
Move in: immediate
Lease: 1 year
No pets`,
  },
];

export function findFakeChatScenario(scenarioId: string): FakeChatScenario | null {
  return FAKE_CHAT_SCENARIOS.find((scenario) => scenario.id === scenarioId) ?? null;
}

export function evaluateFakeScenarioRun(scenario: FakeChatScenario, actualStatus: FakeScenarioRunStatus): FakeScenarioEvaluation {
  if (scenario.expectedRunStatus === actualStatus) {
    return {
      tone: "success",
      label: "Matches expected routing",
      detail: actualStatus === "created_conversation" ? "A rental-enquiry conversation was created for review." : "The message was skipped without creating an actionable conversation.",
    };
  }

  return {
    tone: "warning",
    label: "Check routing mismatch",
    detail: `Expected ${scenario.expectedRunStatus.replace(/_/g, " ")}, got ${actualStatus.replace(/_/g, " ")}. Review the stage log and queue state.`,
  };
}
