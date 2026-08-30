import { expect, type APIRequestContext, type Locator, type Page, test } from "@playwright/test";

const API_BASE = "http://127.0.0.1:18080";
const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost"]);

test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ page, request }) => {
  await blockExternalBrowserRequests(page);
  await request.post(`${API_BASE}/api/fake-chat/reset`);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Inbox" })).toBeVisible();
});

test("dashboard configures listings, audits simulator decisions, and stays usable", async ({ page, request }, testInfo) => {
  const suffix = `${testInfo.project.name}-${Date.now()}`;
  const availableName = `Acceptance Available ${suffix}`;
  const unavailableName = `Acceptance Unavailable ${suffix}`;
  const availableReply = "Please send your preferred viewing slots.";
  const inboxAvailableText = `Hi, I saw ${availableName} and want to rent it.`;
  const inboxUnavailableText = `Hi, I saw ${unavailableName}. Is it still available?`;

  await openProperties(page);
  await expectUsableViewport(page, [
    page.getByRole("button", { name: /New Listing/ }),
    page.getByPlaceholder("Search listings"),
  ]);

  await createListingThroughEditor(page, {
    propertyName: availableName,
    statusLabel: "Available",
    listingUrl: `https://example.test/listings/${suffix}/available`,
    rent: "4200",
    configureAutoReplies: true,
    replyText: availableReply,
  });
  await expect(page.getByText(availableName)).toBeVisible();

  await createListingThroughEditor(page, {
    propertyName: unavailableName,
    statusLabel: "Unavailable",
    listingUrl: `https://example.test/listings/${suffix}/unavailable`,
    rent: "3900",
    configureAutoReplies: false,
  });
  await expect(page.getByText(unavailableName)).toBeVisible();
  await expect(page.getByText("Not available").first()).toBeVisible();
  await expectUsableViewport(page, [
    page.getByRole("button", { name: /New Listing/ }),
    page.getByText(availableName),
    page.getByText(unavailableName),
  ]);

  await openSimulator(page);
  await expectUsableViewport(page, [
    page.getByRole("button", { name: /New/ }),
    page.getByPlaceholder("Display name"),
    page.getByPlaceholder("Type a fake tenant WhatsApp message..."),
  ]);

  const sentDecision = await submitSimulatorEnquiry(page, {
    displayName: `Tenant ${suffix}`,
    chatId: `acceptance-${suffix}@s.whatsapp.net`,
    text: `Hi, I am interested in ${availableName}. Is this rental still available?`,
  });

  expect(sentDecision.result.triage).toMatchObject({
    is_initial_rental_enquiry: true,
    confidence: "high",
  });
  expect(sentDecision.result.rental_listing_matching).toMatchObject({
    match_status: "matched",
    matched_property_status: "available",
  });
  expect(sentDecision.result.send_result).toMatchObject({
    status: "sent",
    reason: "actions_executed",
  });
  await expect(page.locator(".whatsappThread .bubble.outbound", { hasText: availableReply })).toBeVisible();

  const availableInspection = await request.get(`${API_BASE}/api/conversations/${sentDecision.conversation_id}/inspection`);
  expect(availableInspection.ok()).toBeTruthy();
  const availableAudit = await availableInspection.json();
  expect(availableAudit.stage_runs.map((run: { stage: string }) => run.stage)).toEqual(
    expect.arrayContaining(["rental_listing_matching", "outbound_actions"]),
  );
  expect(availableAudit.planned_actions[0]).toMatchObject({
    action_type: "send_playbook",
    stage: "rental_listing_matching",
  });

  await request.patch(`${API_BASE}/api/config`, { data: { values: { send_lock: "true" } } });
  const inboxDecision = await submitBridgeEnquiry(request, {
    chatId: `inbox-${suffix}@s.whatsapp.net`,
    displayName: `Inbox ${suffix}`,
    messageId: `inbox-${suffix}-1`,
    text: inboxAvailableText,
  });
  expect(inboxDecision.data.pipeline.triage).toMatchObject({
    is_initial_rental_enquiry: true,
    confidence: "high",
  });
  expect(inboxDecision.data.pipeline.rental_listing_matching).toMatchObject({
    match_status: "matched",
    matched_property_status: "available",
  });
  expect(inboxDecision.data.pipeline.send_result).toMatchObject({
    status: "blocked",
    reason: "send_lock_enabled",
  });

  await openSimulator(page);
  await page.getByRole("button", { name: /New/ }).click();
  const manualDecision = await submitSimulatorEnquiry(page, {
    displayName: `Manual ${suffix}`,
    chatId: `manual-${suffix}@s.whatsapp.net`,
    text: `Hi, I am checking whether ${unavailableName} is still available.`,
  });

  expect(manualDecision.result.rental_listing_matching).toMatchObject({
    match_status: "manual_review",
    matched_property_status: "unavailable",
  });
  expect(manualDecision.result.send_result).toMatchObject({
    status: "manual_review",
  });

  const manualMessages = await request.get(`${API_BASE}/api/conversations/${manualDecision.conversation_id}/messages`);
  expect(manualMessages.ok()).toBeTruthy();
  const manualTranscript = await manualMessages.json();
  expect(manualTranscript.filter((message: { direction: string }) => message.direction === "outbound")).toHaveLength(0);

  const manualInboxDecision = await submitBridgeEnquiry(request, {
    chatId: `manual-inbox-${suffix}@s.whatsapp.net`,
    displayName: `Manual Inbox ${suffix}`,
    messageId: `manual-inbox-${suffix}-1`,
    text: inboxUnavailableText,
  });
  expect(manualInboxDecision.data.pipeline.rental_listing_matching).toMatchObject({
    match_status: "manual_review",
    matched_property_status: "unavailable",
  });
  expect(manualInboxDecision.data.pipeline.send_result).toMatchObject({
    status: "manual_review",
  });

  const manualInboxMessages = await request.get(`${API_BASE}/api/conversations/${manualInboxDecision.data.conversation_id}/messages`);
  expect(manualInboxMessages.ok()).toBeTruthy();
  const manualInboxTranscript = await manualInboxMessages.json();
  expect(manualInboxTranscript.filter((message: { direction: string }) => message.direction === "outbound")).toHaveLength(0);

  await page.reload();
  await openInbox(page);
  const availableLead = page.locator(".leadRow", { hasText: availableName });
  await expect(availableLead).toBeVisible();
  await availableLead.click();
  await expect(page.getByText("Prosper Audit")).toBeVisible();
  await page.getByText("Prosper Audit").click();
  await expect(page.locator(".aiDecision", { hasText: "Rental listing matching" })).toBeVisible();
  await expect(page.locator(".aiDecision", { hasText: "Response decision" })).toBeVisible();
  await expect(page.locator(".messageThread .bubble.inbound", { hasText: inboxAvailableText })).toBeVisible();
  await expectUsableViewport(page, [
    page.getByPlaceholder("Search for leads"),
    page.getByText("Prosper Audit"),
    page.locator(".aiDecision", { hasText: "Rental listing matching" }),
    page.locator(".messageThread .bubble.inbound", { hasText: inboxAvailableText }),
  ]);

  const unavailableLead = page.locator(".leadRow", { hasText: unavailableName });
  await expect(unavailableLead).toBeVisible();
  await unavailableLead.click();
  await expect(page.locator(".matchedPropertyCard strong", { hasText: unavailableName })).toBeVisible();
  await expect(page.getByText("Manual Review").first()).toBeVisible();
  await expectNoHorizontalDocumentOverflow(page);
});

async function openInbox(page: Page) {
  await page.getByRole("button", { name: "Inbox", exact: true }).click();
  await expect(page.getByPlaceholder("Search for leads")).toBeVisible();
}

async function openProperties(page: Page) {
  await page.getByRole("button", { name: "Properties", exact: true }).click();
  await expect(page.getByRole("button", { name: /New Listing/ })).toBeVisible();
}

async function openSimulator(page: Page) {
  await page.getByRole("button", { name: "Simulator", exact: true }).click();
  await expect(page.getByPlaceholder("Type a fake tenant WhatsApp message...")).toBeVisible();
}

async function createListingThroughEditor(
  page: Page,
  options: {
    propertyName: string;
    statusLabel: "Available" | "Unavailable";
    listingUrl: string;
    rent: string;
    configureAutoReplies: boolean;
    replyText?: string;
  },
) {
  await page.getByRole("button", { name: /New Listing/ }).click();
  await expect(page.getByRole("heading", { name: "New Listing" })).toBeVisible();
  await expectUsableViewport(page, [
    page.getByLabel("Property name"),
    page.getByLabel("Status"),
    page.getByLabel("Listing URL"),
  ]);

  await page.getByLabel("Property name").fill(options.propertyName);
  await page.getByLabel("Status").selectOption({ label: options.statusLabel });
  await page.getByLabel("Listing URL").fill(options.listingUrl);
  await page.getByLabel("Rent").fill(options.rent);
  await page.getByLabel("Available date").fill("Immediate");
  await page.getByLabel("Bedrooms").fill("2");
  await page.getByLabel("Bathrooms").fill("2");
  await page.getByLabel("Address").fill(`${options.propertyName}, Singapore`);

  if (options.configureAutoReplies) {
    await page.getByRole("button", { name: "Auto Replies" }).click();
    await expect(page.getByRole("heading", { name: "Auto Replies" })).toBeVisible();
    await page.getByLabel("Enabled").check();
    await page.getByLabel("Message 1").fill(`Hi, yes {unit_info} is available.`);
    await page.getByLabel("Message 2").fill(options.replyText || "Please send your preferred viewing slots.");
    await expect(page.getByText("WhatsApp Preview")).toBeVisible();
    await expect(page.locator(".phoneBubble.outbound", { hasText: options.replyText || "Please send your preferred viewing slots." })).toBeVisible();
    await expectUsableViewport(page, [
      page.getByLabel("Enabled"),
      page.getByLabel("Message 1"),
      page.getByText("WhatsApp Preview"),
    ]);
  }

  await page.getByRole("button", { name: /Save & Exit/ }).click();
  await expect(page.getByRole("button", { name: /New Listing/ })).toBeVisible();
}

async function submitSimulatorEnquiry(
  page: Page,
  options: { displayName: string; chatId: string; text: string },
): Promise<{ conversation_id: number; result: Record<string, any> }> {
  await page.getByPlaceholder("Display name").fill(options.displayName);
  await page.getByPlaceholder("Fake chat ID").fill(options.chatId);
  await page.getByPlaceholder("Type a fake tenant WhatsApp message...").fill(options.text);

  const responsePromise = page.waitForResponse(
    (response) => response.url() === `${API_BASE}/api/fake-chat/inbound-and-run` && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Send fake message" }).click();
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  expect(body.conversation_id).toEqual(expect.any(Number));
  await expect(page.getByText(options.text)).toBeVisible();
  return body;
}

async function submitBridgeEnquiry(
  request: APIRequestContext,
  options: { chatId: string; displayName: string; messageId: string; text: string },
) {
  const response = await request.post(`${API_BASE}/api/bridge/inbound`, {
    data: {
      chat_jid: options.chatId,
      sender_jid: options.chatId,
      message_id: options.messageId,
      timestamp_ms: Date.now(),
      from_me: false,
      text: options.text,
      raw_type: "text",
      display_name: options.displayName,
    },
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function blockExternalBrowserRequests(page: Page) {
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if ((url.protocol === "http:" || url.protocol === "https:") && !LOCAL_HOSTS.has(url.hostname)) {
      return route.abort();
    }
    return route.continue();
  });
}

async function expectUsableViewport(page: Page, locators: Locator[]) {
  await expectNoHorizontalDocumentOverflow(page);
  for (const locator of locators) {
    const first = locator.first();
    await first.scrollIntoViewIfNeeded();
    await expect(first).toBeVisible();
    await expectLocatorWithinViewport(page, first);
    await expectLocatorCenterUncovered(first);
  }
}

async function expectNoHorizontalDocumentOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(2);
}

async function expectLocatorWithinViewport(page: Page, locator: Locator) {
  const box = await locator.boundingBox();
  const viewport = page.viewportSize();
  expect(box, "expected element to have a box").not.toBeNull();
  expect(viewport, "expected page to have a viewport").not.toBeNull();
  if (!box || !viewport) return;
  expect(box.x).toBeGreaterThanOrEqual(-1);
  expect(box.y).toBeGreaterThanOrEqual(-1);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
}

async function expectLocatorCenterUncovered(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box, "expected element to have a box").not.toBeNull();
  if (!box) return;
  const point = {
    x: Math.max(0, box.x + Math.min(box.width / 2, Math.max(1, box.width - 1))),
    y: Math.max(0, box.y + Math.min(box.height / 2, Math.max(1, box.height - 1))),
  };
  const receivesPointer = await locator.evaluate((element, center) => {
    const topElement = document.elementFromPoint(center.x, center.y);
    return Boolean(topElement && (element === topElement || element.contains(topElement) || topElement.contains(element)));
  }, point);
  expect(receivesPointer).toBeTruthy();
}
