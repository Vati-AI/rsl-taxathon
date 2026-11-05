import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { expect, type Locator, type Page, type TestInfo } from '@playwright/test';

/**
 * Scripted proxy users for TAX-082 onboarding walkthroughs.
 *
 * A proxy stands in for a first-time user. It starts at a portal's entrance
 * and reaches each goal only through what the screen offers: links, buttons
 * and fields found by their accessible names. It never types a URL after the
 * entrance. Every action is counted, and keyboard proxies count each Tab stop.
 *
 * Effort is estimated with the Keystroke-Level Model (Card, Moran and Newell,
 * 1983). Those are typical expert operator times, not observed human times:
 * use them to compare walkthroughs, not as a promise of how long a task takes.
 */
export const KLM = {
  /** Keystroke (average skilled typist). */
  K: 0.28,
  /** Point with the mouse. */
  P: 1.1,
  /** Mouse button press or release. */
  B: 0.1,
  /** Mental preparation: deciding what to do next. */
  M: 1.35,
  /** Move a hand between keyboard and mouse. */
  H: 0.4,
} as const;

/** Tab stops above which reaching one control is recorded as friction. */
export const TAB_FRICTION = 15;

export type ProxyMode = 'pointer' | 'keyboard';

export interface TaskRecord {
  id: string;
  goal: string;
  completed: boolean;
  /** Clicks, fills, selections, uploads, and keyboard activations. */
  actions: number;
  /** Tab presses (keyboard proxies only). */
  tabStops: number;
  /** Characters typed. */
  typed: number;
  /** Keystroke-Level Model estimate in seconds (see KLM). */
  klmSeconds: number;
  /** Wall-clock automation time; depends on the machine, informational only. */
  wallMs: number;
  /** What the proxy activated, in order. */
  route: string[];
  friction: string[];
  error?: string;
}

export interface PersonaReport {
  persona: string;
  description: string;
  mode: ProxyMode;
  viewport: string;
  browser: string;
  tasks: TaskRecord[];
}

/** The error's message without terminal colour codes, shortened for the report. */
const summary = (error: unknown) =>
  (error instanceof Error ? error.message : String(error))
    // eslint-disable-next-line no-control-regex
    .replace(/\u001b\[[0-9;]*m/g, '')
    .split('\n')
    .filter((line) => line.trim())
    .slice(0, 4)
    .join(' | ')
    .slice(0, 400);

export class ProxyUser {
  readonly tasks: TaskRecord[] = [];
  private current: TaskRecord | null = null;
  private lastInput: 'mouse' | 'keyboard' = 'mouse';
  /** The goal that failed first, if any; its screen is attached by `record`. */
  private failure: string | null = null;

  constructor(
    readonly page: Page,
    readonly persona: string,
    readonly description: string,
    readonly mode: ProxyMode,
  ) {}

  private get task(): TaskRecord {
    if (!this.current) throw new Error('A proxy action ran outside a task');
    return this.current;
  }

  private home(to: 'mouse' | 'keyboard') {
    if (this.lastInput !== to) this.task.klmSeconds += KLM.H;
    this.lastInput = to;
  }

  private point(label: string) {
    this.home('mouse');
    this.task.actions++;
    this.task.route.push(label);
    this.task.klmSeconds += KLM.M + KLM.P + 2 * KLM.B;
  }

  private typing(text: string) {
    this.home('keyboard');
    this.task.typed += text.length;
    this.task.klmSeconds += KLM.K * text.length;
  }

  /**
   * Run one goal. A failure is recorded, not thrown; see `expectAllCompleted`.
   * Once a goal fails, later goals are recorded as not attempted, because the
   * proxy is no longer where a real user would be.
   */
  async run(id: string, goal: string, steps: () => Promise<void>): Promise<boolean> {
    const blocked = this.tasks.find((t) => !t.completed);
    this.current = {
      id,
      goal,
      completed: false,
      actions: 0,
      tabStops: 0,
      typed: 0,
      klmSeconds: 0,
      wallMs: 0,
      route: [],
      friction: [],
    };
    const started = Date.now();
    try {
      if (blocked) throw new Error(`Not attempted: "${blocked.id}" did not complete`);
      await steps();
      this.current.completed = true;
    } catch (error) {
      this.current.error = summary(error);
      if (!blocked) this.failure = this.current.id;
    } finally {
      this.current.wallMs = Date.now() - started;
      this.current.klmSeconds = Math.round(this.current.klmSeconds * 10) / 10;
      this.tasks.push(this.current);
      this.current = null;
    }
    return this.tasks[this.tasks.length - 1].completed;
  }

  /** Record something that slowed the proxy down or would confuse a new user. */
  friction(note: string) {
    this.task.friction.push(note);
  }

  // Pointer actions.

  async click(target: Locator, label: string) {
    this.point(label);
    await target.click();
  }

  async fill(target: Locator, value: string, label: string) {
    this.point(label);
    await target.fill(value);
    this.typing(value);
  }

  async check(target: Locator, label: string) {
    this.point(label);
    await target.check();
  }

  async select(target: Locator, option: string, label: string) {
    // A native select: open it, then choose.
    this.point(label);
    this.task.klmSeconds += KLM.P + 2 * KLM.B;
    await target.selectOption({ label: option });
  }

  /** frappe-ui combobox: open, then choose the option. */
  async choose(combobox: Locator, option: string, label: string) {
    this.point(label);
    await combobox.click();
    this.task.klmSeconds += KLM.P + 2 * KLM.B;
    await this.page.getByRole('option', { name: option }).click();
    await expect(combobox).toContainText(option);
  }

  /** Attach a file. The operating-system file dialog is counted as two more pointer steps. */
  async upload(input: Locator, file: Parameters<Locator['setInputFiles']>[0], label: string) {
    this.point(label);
    this.task.klmSeconds += 2 * (KLM.M + KLM.P + 2 * KLM.B);
    await input.setInputFiles(file);
  }

  /** The browser's Back button. */
  async back(label = 'browser Back') {
    this.point(label);
    await this.page.goBack();
  }

  // Keyboard actions.

  /**
   * Press Tab (or Shift+Tab with `backwards`) until `target` has focus, counting
   * each stop. More than TAB_FRICTION stops is recorded as friction; more than
   * `limit` fails the task as unreachable.
   */
  async tabTo(target: Locator, label: string, { limit = 60, backwards = false } = {}) {
    this.home('keyboard');
    this.task.klmSeconds += KLM.M;
    // Fail fast and clearly when the control is missing or ambiguous.
    await expect(target, `"${label}" should be one visible control`).toHaveCount(1);
    await expect(target).toBeVisible();
    const focused = () =>
      target.evaluate((el) => el === document.activeElement, undefined, { timeout: 2_000 });
    let stops = 0;
    while (!(await focused())) {
      if (stops >= limit) {
        throw new Error(`"${label}" was not reached within ${limit} Tab stops`);
      }
      await this.page.keyboard.press(backwards ? 'Shift+Tab' : 'Tab');
      stops++;
    }
    this.task.tabStops += stops;
    this.task.klmSeconds += KLM.K * stops;
    this.task.route.push(`${label} (${stops} Tab)`);
    if (stops > TAB_FRICTION) {
      this.friction(`${stops} Tab stops to reach "${label}"`);
    }
    return stops;
  }

  /** Press a key to activate the focused control (Enter, Space, Escape, arrows). */
  async press(key: string) {
    this.home('keyboard');
    this.task.actions++;
    this.task.klmSeconds += KLM.K;
    await this.page.keyboard.press(key);
  }

  async type(text: string) {
    this.typing(text);
    await this.page.keyboard.type(text);
  }

  report(testInfo: TestInfo): PersonaReport {
    return {
      persona: this.persona,
      description: this.description,
      mode: this.mode,
      viewport: testInfo.project.name,
      browser: process.env.PLAYWRIGHT_CHANNEL || 'chromium',
      tasks: this.tasks,
    };
  }

  /**
   * Attach the measurements to the test, and write them to `RECORD_DIR` (if
   * set) as `<persona>-<width>.json`. Call before `expectAllCompleted`, so a
   * failed walkthrough still leaves its measurements.
   */
  async record(testInfo: TestInfo) {
    const report = this.report(testInfo);
    const body = JSON.stringify(report, null, 2) + '\n';
    await testInfo.attach(`${this.persona}.json`, { body, contentType: 'application/json' });
    const dir = process.env.RECORD_DIR;
    const width = testInfo.project.name.split('-')[1];
    if (dir) {
      mkdirSync(dir, { recursive: true });
      writeFileSync(path.join(dir, `${this.persona}-${width}.json`), body);
    }
    if (this.failure && !this.page.isClosed()) {
      const screen = await this.page.screenshot({ fullPage: true });
      await testInfo.attach(`failed-${this.failure}.png`, {
        body: screen,
        contentType: 'image/png',
      });
      if (dir) writeFileSync(path.join(dir, `${this.persona}-${width}-failed.png`), screen);
    }
  }

  expectAllCompleted() {
    expect(
      this.tasks.filter((t) => !t.completed).map((t) => `${t.id}: ${t.error}`),
      `${this.persona} did not complete every task`,
    ).toEqual([]);
  }
}
