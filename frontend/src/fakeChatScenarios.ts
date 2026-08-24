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
    id: "available_listing",
    label: "Available listing",
    displayName: "Demo Tenant",
    expectedRunStatus: "created_conversation",
    expectedStage: "End after rental listing matching",
    expectedAction: "Availability reply based on the matched rental listing.",
    verify: "Confirm that Maple Grove Residence is matched and no tenant screening details are collected.",
    text: "Hi, is Maple Grove Residence available? Can we view this weekend?",
  },
  {
    id: "ambiguous_property",
    label: "Ambiguous enquiry",
    displayName: "Unspecified Tenant",
    expectedRunStatus: "created_conversation",
    expectedStage: "End or manual review",
    expectedAction: "No confident outbound reply when the enquiry does not identify a listing clearly.",
    verify: "Confirm that Prosper preserves the enquiry for review instead of inventing a confident listing match.",
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
    id: "purchase_not_rental",
    label: "Purchase enquiry",
    displayName: "Buyer Contact",
    expectedRunStatus: "skipped",
    expectedStage: "Skipped",
    expectedAction: "No outbound action.",
    verify: "Confirm that purchase interest is not treated as a rental enquiry.",
    text: "Hi, I am looking to buy a two-bedroom apartment near the city.",
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
