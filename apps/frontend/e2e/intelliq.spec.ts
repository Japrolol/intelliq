import path from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("demo@example.com");
  await page.getByLabel("Password", { exact: true }).fill("demo");
  await page.getByRole("button", { name: "Enter demo" }).click();

  // One eligible organization is selected without an extra picker step.
  await expect(page).toHaveURL(/\/overview$/);
  await expect(
    page.getByRole("heading", { name: "Today’s decisions", exact: true }),
  ).toBeVisible();
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
}

test("analysis failure stays in the decision card and project actions match", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  let release!: () => void;
  const responseGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/analyses", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await responseGate;
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Injected analysis failure" }),
    });
  });
  await page.getByRole("button", { name: /Prepare decisions|Update decisions/ }).click();
  await expect(
    page.getByRole("heading", { name: "Preparing decisions", exact: true }),
  ).toBeVisible();
  release();
  await expect(
    page.getByRole("alert").getByRole("heading", { name: "Couldn’t prepare decisions" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry analysis" })).toHaveText("");
  await expect(page.getByText("No pending implementation steps.")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  await page.goto("/projects");
  const addData = page.getByRole("button", { name: "Add data", exact: true });
  await expect(addData).toBeVisible();
  expect((await addData.boundingBox())?.height).toBe(44);
});

function analysisIdFromUrl(url: string): string {
  const match = new URL(url).pathname.match(/^\/analyses\/([^/]+)$/);
  if (!match) throw new Error(`Expected an analysis URL, received ${url}`);
  return decodeURIComponent(match[1]);
}

function localPastTimestamp(): string {
  const value = new Date(Date.now() - 60 * 60 * 1000);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
}

test("decision desk stays usable at mobile, tablet, and desktop widths", async ({
  page,
}, testInfo: TestInfo) => {
  await signIn(page);

  for (const viewport of [
    { name: "mobile", width: 390, height: 844 },
    { name: "tablet", width: 768, height: 1024 },
    { name: "desktop", width: 1440, height: 1000 },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/overview");
    await expect(
      page.getByRole("heading", { name: "Today’s decisions", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Organization menu" })).toHaveCount(0);
    await expect(
      page.locator("header").getByRole("button", { name: "Add data" }),
    ).toHaveCount(0);
    await expectNoHorizontalOverflow(page);

    const demoBadge = page.getByText("Demo data", { exact: true });
    if (viewport.width < 640) await expect(demoBadge).toBeHidden();
    else await expect(demoBadge).toBeVisible();

    await page.getByRole("button", { name: "Account menu" }).click();
    await expect(
      page.getByRole("menuitem", { name: "Switch organization" }),
    ).toBeVisible();
    await expect(
      page.getByRole("menuitem", { name: "Settings", exact: true }),
    ).toHaveCount(0);
    await page.keyboard.press("Escape");
    const navigation = page.getByRole("navigation", {
      name: viewport.width < 640 ? "Mobile navigation" : "Main navigation",
      exact: true,
    });
    await navigation.getByRole("link", { name: "Projects", exact: true }).click();
    await expect(page).toHaveURL(/\/projects$/);
    await page.getByRole("button", { name: "Add data" }).click();
    await expect(
      page.getByRole("menuitem", { name: "Import spreadsheet" }),
    ).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menuitem", { name: "Import spreadsheet" })).toBeHidden();
    await expectNoHorizontalOverflow(page);

    await page.screenshot({
      path: testInfo.outputPath(`projects-${viewport.name}.png`),
      fullPage: true,
    });
  }
});

test("fixture workflow runs, compares, inspects, reviews, applies, completes, and learns", async ({
  page,
}) => {
  await signIn(page);

  const runButton = page.getByRole("button", {
    name: /^(Prepare decisions|Update decisions)$/,
  });
  if (await runButton.count()) {
    await runButton.first().click();
    await expect(page.getByRole("button", { name: "Open details" })).toBeVisible();
    await expect(page).toHaveURL(/\/overview$/);
    await page.getByRole("button", { name: "Open details" }).click();
    await expect(page).toHaveURL(/\/analyses\/[^/]+$/);
  } else {
    // A retained fixture database may already contain the current published run.
    await page.getByRole("button", { name: "Open details" }).click();
    await expect(page).toHaveURL(/\/analyses\/[^/]+$/);
  }
  const analysisId = analysisIdFromUrl(page.url());

  await expect(page.getByText("Projects, work, and shared capacity")).toBeVisible();
  await expect(page.getByText("Where simulated finishes land")).toBeVisible();
  await expect(page.getByText("Available versus used effort")).toBeVisible();
  await expect(page.locator(".recharts-bar-rectangle").first()).toBeVisible();
  await expect(page.locator(".recharts-line-curve").first()).toBeVisible();

  await page.getByRole("button", { name: "List", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Accessible relationship list" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "3D", exact: true }).click();
  await expect(page.locator("canvas").first()).toBeVisible();

  await page.goto("/overview");
  await expect(page.getByRole("button", { name: "Compare options" })).toBeVisible();
  await page.getByRole("button", { name: "Compare options" }).click();
  await expect(page).toHaveURL(new RegExp(`/decisions/${analysisId}$`));
  await expect(
    page.getByRole("heading", { name: "Choose the move worth authorizing." }),
  ).toBeVisible();

  const transferOption = page
    .locator("button.option-card")
    .filter({ hasText: "Transfer" });
  await expect(transferOption).toHaveCount(1);
  await transferOption.click();
  await expect(page.getByText("Same evaluated plan", { exact: true })).toBeVisible();
  const reviewDialog = page.getByRole("dialog");
  await expect(reviewDialog.getByText("Manual steps", { exact: true })).toBeVisible();
  await expect(reviewDialog.getByText(/Transfer .* from .* to /).first()).toBeVisible();
  await reviewDialog.getByRole("button", { name: "Apply reviewed bundle" }).click();

  await expect(page.getByText("Execution receipt", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Mark complete" })).toBeVisible();
  await page.getByRole("button", { name: "Mark complete" }).click();
  await expect(
    page.getByText("No open manual steps were returned by the execution."),
  ).toBeVisible();

  await page.goto(`/analyses/${encodeURIComponent(analysisId)}`);
  await expect(
    page.getByText("Record measured setup time", { exact: true }),
  ).toBeVisible();
  const workerControl = page.getByLabel("Worker id", { exact: true });
  await expect(workerControl).toBeVisible();
  if ((await workerControl.getAttribute("role")) === "combobox") {
    await expect(workerControl).toContainText("Electrician B");
  } else {
    const workerId = await workerControl.inputValue();
    expect(workerId).toBeTruthy();
  }
  await page.getByLabel("Measured setup hours", { exact: true }).fill("4");
  await page
    .getByLabel("Measured event time", { exact: true })
    .fill(localPastTimestamp());
  await page.getByLabel(/^Note/).fill("Measured transfer setup at the target site.");
  await page.getByRole("button", { name: "Record setup outcome" }).click();

  await expect(
    page.getByText(/Measured setup outcome recorded|calibration/i).last(),
  ).toBeVisible();
  await expect(
    page.getByText(/observed setup|setup mean|forecast/i).last(),
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.goto("/overview");
  await expect(
    page.getByRole("button", { name: "Update decisions" }).first(),
  ).toBeVisible();
  const freshRun = page.waitForResponse(
    (response) =>
      response.url().endsWith("/analyses") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Update decisions" }).first().click();
  await freshRun;
  await expect(page.getByRole("button", { name: "Open details" })).toBeVisible();
  await page.getByRole("button", { name: "Open details" }).click();
  await expect(page).toHaveURL(/\/analyses\/[^/]+$/);
  await expect(page.getByText("Transfer setup learning", { exact: true })).toBeVisible();
  await expect(page.getByText(/Prior → current setup mean/)).toBeVisible();
  await expect(page.getByText(/observed setup/)).toBeVisible();
});

test("XLSX preview shows structured unmatched-member corrections without committing", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/projects");
  await page.getByRole("button", { name: "Add data" }).click();
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("menuitem", { name: "Import spreadsheet" }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles(
    path.resolve(process.cwd(), "../../fixtures/imports/mock-portfolio.xlsx"),
  );
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await expect(page).toHaveURL(/\/imports\/[^/]+$/);
  await expect(page.getByRole("heading", { name: "Review import" })).toBeVisible();
  await expect(page.getByText(/unmatched member/i).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm import" })).toBeDisabled();
  await expect(page.getByText(/source row/i).first()).toBeVisible();
});

test("knowledge import opens the native file picker without a dialog", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/knowledge");
  await expect(page.getByRole("button", { name: "Import knowledge" })).toBeVisible();
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Import knowledge" }).click();
  await chooserPromise;
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Import knowledge" })).toHaveCount(0);
});

test("valid CSV confirmation reports fixture manual receipts without claiming Timecue writes", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/imports/new");
  await page.locator('input[type="file"]').setInputFiles({
    name: "projects.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "externalKey,name,timezone,address,targetFinishAt\n" +
        "browser-project,Construction demo,Europe/Warsaw,Warsaw Poland,2026-10-30T16:00:00Z\n",
    ),
  });
  await page.getByRole("button", { name: "Preview import", exact: true }).click();
  await page.getByRole("button", { name: "Confirm import", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Confirm import", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("heading", { name: "Import receipts" })).toBeVisible();
  await expect(
    page.getByText("Fixture mode does not write to Timecue; perform this step manually."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Review and resume" })).toBeVisible();
});
