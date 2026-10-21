import { expect, test } from "@playwright/test";

test("shows the disclaimer and ECG analysis screen", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Screening tool, not a diagnosis. Research use only.").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review a night of sleep" })).toBeVisible();
  await expect(page.getByText("Research model")).toBeVisible();
});

test("does not show ECG validation metrics on the unvalidated interval tab", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: /RR intervals/ }).click();
  await expect(page.getByText("Unvalidated source").first()).toBeVisible();
  await expect(page.getByText("Wearable interval performance has not been evaluated.")).toBeVisible();
  await expect(page.getByText("Sensitivity", { exact: true })).toHaveCount(0);
  await expect(page.getByText("AUC", { exact: true })).toHaveCount(0);
});

test("keeps the upload form usable at a mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByLabel("Recording source")).toBeVisible();
  await expect(page.getByRole("button", { name: /Analyze recording/ })).toBeDisabled();
});
