import { expect, type Locator, type Page } from "@playwright/test";

/** Tab until `locator` is focused, then wait for its focus-visible outline (it paints a frame after focus lands). */
export async function keyboardFocus(page: Page, locator: Locator) {
  for (let i = 0; i < 200; i++) {
    await page.keyboard.press("Tab");
    if (await locator.evaluate(node => node === document.activeElement)) {
      await expect.poll(() => locator.evaluate(node => getComputedStyle(node).outlineStyle)).not.toBe("none");
      return;
    }
  }
  throw new Error("Control is unreachable by keyboard");
}
