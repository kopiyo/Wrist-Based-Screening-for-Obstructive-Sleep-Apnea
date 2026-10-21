import { expect, test } from "@playwright/test";

async function asAnonymousGuest(page: import("@playwright/test").Page) {
  await page.route("**/v1/model-card", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ auc: 0.8, sensitivity: 0.7, specificity: 0.75, threshold: 0.3 }),
  }));
  await page.route("**/v1/auth/me", route => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Authentication required." }),
  }));
  await page.goto("/");
}

async function asSignedInUser(page: import("@playwright/test").Page) {
  await page.route("**/v1/model-card", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({}),
  }));
  await page.route("**/v1/auth/me", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ id: "user-1", email: "user@example.invalid", email_verified_at: null, timezone: "Africa/Nairobi" }),
  }));
  await page.route("**/v1/alerts/rules", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ rules: [
      { kind: "screening_result", enabled: false, channels: ["web_push"] },
      { kind: "worsening_trend", enabled: false, channels: ["web_push"] },
      { kind: "morning_summary", enabled: false, channels: ["web_push"] },
      { kind: "device_silent_overnight", enabled: false, channels: ["web_push"], availability: "needs_live_device" },
    ], quiet_hours: { start: "22:00", end: "07:00" } }),
  }));
  await page.route("**/v1/alerts", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }),
  }));
  await page.goto("/");
}

test("anonymous screening remains available while account pages are gated", async ({ page }) => {
  await asAnonymousGuest(page);

  await expect(page.getByRole("heading", { name: "Review a night of sleep" })).toBeVisible();
  await expect(page.getByLabel("Recording source")).toBeVisible();
  await expect(page.getByText("Anonymous analyses stay temporary.")).toBeVisible();
  await expect(page.getByText("Save this analysis to my nights")).toHaveCount(0);

  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Nights/ }).click();
  await expect(page.getByRole("heading", { name: "Nights" })).toBeVisible();
  await expect(page.getByText("Sign-in is required only for saved history and account features.")).toBeVisible();
  await expect(page.getByRole("main").getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("alerts explain that only consented saved nights can trigger them", async ({ page }) => {
  await asSignedInUser(page);

  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Alerts/ }).click();

  await expect(page.getByRole("heading", { name: "Alerts", exact: true })).toBeVisible();
  await expect(page.getByText("Alerts only apply to nights saved with explicit consent.")).toBeVisible();
  await expect(page.getByText("Email (verify email first)").first()).toBeVisible();
  await expect(page.getByText("Needs live device · disabled until Phase 3")).toBeVisible();
});

test("primary navigation, upload input, and icon controls expose accessible names", async ({ page }) => {
  await asAnonymousGuest(page);

  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
  await expect(page.getByLabel("Recording source")).toBeVisible();
  await expect(page.getByLabel("Toggle color theme")).toBeVisible();
  await expect(page.locator(".sidebar").getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(page.locator("img:not([alt])")).toHaveCount(0);
});

test("recovery UI keeps the account restricted until a passkey is added", async ({ page }) => {
  let meRequests = 0;
  await page.addInitScript(() => {
    const buffer = new Uint8Array([1, 2, 3]).buffer;
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: {
        create: async () => ({
          id: "recovery-passkey", rawId: buffer, type: "public-key",
          authenticatorAttachment: "platform",
          response: { clientDataJSON: buffer, attestationObject: buffer, getTransports: () => ["internal"] },
        }),
        get: async () => null,
      },
    });
  });
  await page.route("**/v1/model-card", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({}),
  }));
  await page.route("**/v1/auth/csrf", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ csrf_token: "test-csrf" }),
  }));
  await page.route("**/v1/auth/refresh", route => route.fulfill({
    status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Authentication required." }),
  }));
  await page.route("**/v1/auth/me", route => {
    meRequests += 1;
    return route.fulfill(meRequests === 1 ? {
      status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Authentication required." }),
    } : {
      status: 200, contentType: "application/json",
      body: JSON.stringify({ id: "user-1", email: "recover@example.com", email_verified_at: null, timezone: "UTC" }),
    });
  });
  await page.route("**/v1/auth/recovery", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ status: "restricted_recovery_session" }),
  }));
  await page.route("**/v1/auth/passkeys/options", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ challenge_id: "00000000-0000-4000-8000-000000000001", public_key_options: { challenge: "AQID", user: { id: "AQID" } } }),
  }));
  await page.route("**/v1/auth/passkeys", route => route.fulfill({
    status: 201, contentType: "application/json", body: JSON.stringify({ status: "added" }),
  }));
  await page.goto("/");
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Privacy & settings/ }).click();
  await page.getByRole("button", { name: "Recovery", exact: true }).click();
  await page.getByLabel("Email").fill("recover@example.com");
  await page.getByLabel("One-time recovery code").fill("ONE-TIME-CODE");
  await page.getByRole("button", { name: "Use recovery code" }).click();

  await expect(page.getByRole("heading", { name: "Restore account access" })).toBeVisible();
  await expect(page.getByText("Your recovery code created a restricted session.")).toBeVisible();
  await page.getByRole("button", { name: "Add passkey and restore access" }).click();
  await expect(page.getByRole("heading", { name: "Account settings" })).toBeVisible();
  await expect(page.getByText("Full account access has been restored.")).toBeVisible();
});
