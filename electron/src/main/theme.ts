/* MIT License - Copyright (c) fintonlabs.com */

/**
 * Read Ableton Live's own skins and mirror them.
 *
 * Live ships its themes as `.ask` files -- plain XML, roughly 200 named colours
 * each -- inside the app bundle, plus anything the user has dropped in their
 * User Library. Parsing those means the app is not merely *styled like* Live,
 * it is wearing the same skin the user picked, down to the selection orange.
 *
 * Live does not record the active theme anywhere we can read (Preferences.cfg
 * is a binary blob), so the choice is surfaced in the UI instead, defaulting to
 * Live's own default.
 */

import { readFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, join } from 'node:path';

export interface AbletonTheme {
  name: string;
  path: string;
  isDark: boolean;
  colours: Record<string, string>;
}

export interface ThemeTokens {
  name: string;
  isDark: boolean;
  /** CSS custom properties, ready to write onto :root. */
  vars: Record<string, string>;
}

const BUNDLE_GLOBS = [
  '/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Themes',
  '/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Themes',
  '/Applications/Ableton Live 12 Intro.app/Contents/App-Resources/Themes',
  '/Applications/Ableton Live 11 Suite.app/Contents/App-Resources/Themes',
];

const USER_THEMES = join(homedir(), 'Music/Ableton/User Library/Themes');

export const DEFAULT_THEME = 'Default Dark Neutral Medium';

/** Where Live keeps its skins on this machine. */
export function themeDirectories(): string[] {
  const dirs = BUNDLE_GLOBS.filter((d) => existsSync(d));
  if (existsSync(USER_THEMES)) dirs.push(USER_THEMES);
  return dirs;
}

export async function listThemes(): Promise<{ name: string; path: string }[]> {
  const found: { name: string; path: string }[] = [];
  for (const dir of themeDirectories()) {
    let entries: string[];
    try {
      entries = await readdir(dir);
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.toLowerCase().endsWith('.ask')) continue;
      found.push({ name: basename(entry, '.ask'), path: join(dir, entry) });
    }
  }
  // User themes shadow bundled ones of the same name.
  const byName = new Map<string, { name: string; path: string }>();
  for (const theme of found) byName.set(theme.name, theme);
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** Parse a `.ask` file into its named colours. */
export async function readTheme(path: string): Promise<AbletonTheme> {
  const xml = await readFile(path, 'utf8');
  const colours: Record<string, string> = {};

  // <ControlForeground Value="#b5b5b5" /> -- values may carry an alpha byte.
  const pattern = /<(\w+)\s+Value="(#[0-9A-Fa-f]{6,8})"\s*\/?>/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(xml)) !== null) {
    const [, key, value] = match;
    if (key && value) colours[key] = normaliseColour(value);
  }

  const name = basename(path, '.ask');
  return { name, path, colours, isDark: isDarkTheme(colours, name) };
}

/** Live writes #RRGGBB or #RRGGBBAA; CSS wants #RRGGBB or #RRGGBBAA too. */
function normaliseColour(value: string): string {
  return value.length === 9 || value.length === 7 ? value.toLowerCase() : value;
}

function luminance(hex: string): number {
  const clean = hex.replace('#', '').slice(0, 6);
  const channels = [0, 2, 4].map((i) => parseInt(clean.slice(i, i + 2), 16) / 255);
  const linear = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4),
  );
  return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
}

function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function mix(a: string, b: string, amount: number): string {
  const parse = (hex: string) => {
    const clean = hex.replace('#', '').slice(0, 6);
    return [0, 2, 4].map((i) => parseInt(clean.slice(i, i + 2), 16));
  };
  const [ar, ag, ab] = parse(a);
  const [br, bg, bb] = parse(b);
  const channel = (x: number, y: number) =>
    Math.round(x + (y - x) * amount).toString(16).padStart(2, '0');
  return `#${channel(ar!, br!)}${channel(ag!, bg!)}${channel(ab!, bb!)}`;
}

/** Move a colour toward black or white by `amount` (-1..1). */
function shade(hex: string, amount: number): string {
  return mix(hex, amount >= 0 ? '#ffffff' : '#000000', Math.abs(amount));
}

/**
 * Live reuses colours across surfaces that we combine differently.
 *
 * In every Default Light skin, TextDisabled and SurfaceArea are both #6e6e6e,
 * so dim text drawn on a panel is invisible. Some third-party skins are worse
 * still (near-white text on a white ground). Live gets away with it because it
 * never puts those two together; we do, so the colour has to be corrected.
 *
 * Walk the colour toward the theme's own text colour first, to stay in the
 * skin's character. If that cannot reach the target -- because the text colour
 * is itself low-contrast on this surface -- fall back to plain black or white,
 * whichever the background can carry. Readability wins over fidelity.
 */
function readable(fg: string, bg: string, text: string, min: number): string {
  if (contrast(fg, bg) >= min) return fg;

  for (let step = 1; step <= 10; step += 1) {
    const candidate = mix(fg, text, step / 10);
    if (contrast(candidate, bg) >= min) return candidate;
  }

  const black = '#000000';
  const white = '#ffffff';
  const best = contrast(black, bg) >= contrast(white, bg) ? black : white;
  if (contrast(best, bg) >= min) {
    // Blend a little of the original back in so it still reads as the skin's.
    for (let step = 4; step >= 0; step -= 1) {
      const candidate = mix(best, fg, step / 10);
      if (contrast(candidate, bg) >= min) return candidate;
    }
    return best;
  }
  return best;
}

/** Readable against every surface the token is actually drawn on. */
function readableOnAll(
  fg: string,
  backgrounds: string[],
  text: string,
  min: number,
): string {
  let candidate = fg;
  for (const bg of backgrounds) candidate = readable(candidate, bg, text, min);
  // One more pass: fixing for a later surface may have broken an earlier one.
  for (const bg of backgrounds) candidate = readable(candidate, bg, text, min);
  return candidate;
}

/**
 * Whether a skin reads as dark.
 *
 * Live's "Default Light" skins sit on a mid-grey ground (#818181, luminance
 * 0.24), so a naive < 0.4 test calls them dark and lands white text on mid-grey.
 * Judge by which polarity the ground can actually carry instead.
 */
function isDarkTheme(colours: Record<string, string>, name: string): boolean {
  const ground = colours.Desktop ?? colours.SurfaceArea;
  if (!ground) return /dark/i.test(name);
  // A ground that carries black text better than white is a light theme,
  // however grey it looks.
  return contrast('#ffffff', ground) > contrast('#000000', ground);
}

/** The five surfaces we draw on, derived from one ground. */
interface Ramp {
  bg: string;
  panel: string;
  sunk: string;
  raised: string;
  hover: string;
}

function buildRamp(ground: string, dark: boolean): Ramp {
  // Light skins get a tighter spread: on a pale ground a big step downward
  // pushes a surface back into the mid-grey band where nothing is readable.
  const steps = dark
    ? { panel: -0.16, sunk: -0.34, raised: 0.08, hover: 0.16 }
    : { panel: -0.04, sunk: -0.08, raised: 0.05, hover: 0.1 };
  return {
    bg: ground,
    panel: shade(ground, steps.panel),
    sunk: shade(ground, steps.sunk),
    raised: shade(ground, steps.raised),
    hover: shade(ground, steps.hover),
  };
}

/**
 * Move the ground until *every* surface in the ramp can carry text at AA.
 *
 * Lifting the ground alone is not enough: the ramp then darkens `sunk` straight
 * back into the unreadable band. Live's mid-greys are fine for Live -- it draws
 * chunky controls, not body copy -- but we put small text on them, so the whole
 * ramp has to clear the bar together. Hue is preserved; only lightness moves.
 */
function solveRamp(
  rawGround: string,
  dark: boolean,
  min = 4.6,
): { ground: string; ramp: Ramp; ink: string } {
  const ink = dark ? '#ffffff' : '#000000';
  const target = dark ? '#000000' : '#ffffff';

  let ground = rawGround;
  for (let step = 0; step <= 24; step += 1) {
    ground = step === 0 ? rawGround : mix(rawGround, target, step / 24);
    const ramp = buildRamp(ground, dark);
    const worst = Math.min(...Object.values(ramp).map((s) => contrast(ink, s)));
    if (worst >= min) return { ground, ramp, ink };
  }
  return { ground, ramp: buildRamp(ground, dark), ink };
}

/**
 * Map Live's colour names onto the app's tokens.
 *
 * Only a handful of the ~200 are load-bearing for a chat surface; the rest
 * describe controls we do not have. Anything missing falls back to a sibling
 * key so a third-party skin with an unusual palette still renders.
 */
export function toTokens(theme: AbletonTheme): ThemeTokens {
  const c = theme.colours;
  const pick = (...keys: string[]): string | undefined => {
    for (const key of keys) if (c[key]) return c[key];
    return undefined;
  };

  const dark = theme.isDark;

  // Live's surface names do not map onto ours. In its light skins SurfaceArea
  // is mid-grey (#6e6e6e) while ControlBackground is light (#cfcfcf): Live puts
  // light text on the first and dark text on the second. Borrowing both means
  // no single text colour can be readable on both -- the constraint is simply
  // unsatisfiable.
  //
  // So take the skin's *ground* and derive our own lightness ramp from it. The
  // hue, the accent and the light/dark character are all still Live's; only the
  // step between surfaces is ours, and it is consistent by construction.
  const rawGround = pick('Desktop', 'SurfaceArea') ?? (dark ? '#2a2a2a' : '#c8c8c8');
  const { ground, ramp } = solveRamp(rawGround, dark);
  const { bg, panel, sunk, raised, hover } = ramp;

  const rawText = pick('ControlForeground', 'ControlOffForeground')
    ?? (dark ? '#d6d6d6' : '#121212');
  const rawDim = pick('TextDisabled', 'ControlDisabled') ?? rawText;
  const accent = pick('ChosenDefault', 'Progress') ?? '#ffad56';
  const line = pick('SelectionFrame', 'ClipBorder') ?? shade(ground, dark ? 0.2 : -0.25);
  const alert = pick('Alert') ?? '#e76942';
  const record = pick('ChosenRecord', 'Alert') ?? '#ff5559';
  const selection = pick('SelectionBackground') ?? accent;

  // With a coherent ramp, one text colour can clear AA on every surface.
  const surfaces = [bg, panel, sunk, raised, hover];
  // Polarity follows what the surfaces can carry, not the theme's label.
  const polarity = dark ? '#ffffff' : '#000000';
  const safeText = readableOnAll(rawText, surfaces, polarity, 4.5);
  const safeDim = readableOnAll(rawDim, surfaces, safeText, 4.5);
  const safeAccent = readableOnAll(accent, surfaces, safeText, 4.5);
  // Borders are non-text; 2.2:1 is enough to read as a division.
  const safeLine = readableOnAll(line, [bg, panel], safeText, 2.2);

  return {
    name: theme.name,
    isDark: dark,
    vars: {
      '--bg': bg,
      '--panel': panel,
      '--sunk': sunk,
      '--raised': raised,
      '--hover': hover,
      '--text': safeText,
      '--text-dim': safeDim,
      '--accent': safeAccent,
      /** The unmodified accent, for fills where contrast is not a concern. */
      '--accent-raw': accent,
      '--line': safeLine,
      '--alert': alert,
      '--record': record,
      '--selection': selection,
      // Live's own detail-view grey reads as the "well" behind content.
      '--detail': pick('DetailViewBackground', 'SurfaceBackground') ?? raised,
    },
  };
}

/**
 * Ableton's clip-colour palette, used for the musical roles.
 *
 * Live's clip colours are not stored in the theme file -- they come from a
 * fixed 60-swatch grid -- so these are taken from that grid and split into two
 * sets, because a swatch that reads well on Live's dark skin is washed out on
 * the light one.
 */
export const ROLE_COLOURS: Record<'dark' | 'light', Record<string, string>> = {
  dark: {
    kick: '#ff9b4e',
    drums: '#ffb84d',
    perc: '#e7d44b',
    bass: '#c5e04c',
    sub: '#8fd94f',
    chords: '#4fd7b8',
    arp: '#54c4ec',
    lead: '#7ba3ff',
    hook: '#b389ff',
    pad: '#e07be0',
    riser: '#ff77b0',
    impact: '#ff6b7d',
    vocal: '#69dc7a',
    fx: '#a3a3a3',
  },
  light: {
    kick: '#c85a1e',
    drums: '#b8791a',
    perc: '#8f8317',
    bass: '#6f8a1c',
    sub: '#3f8a2c',
    chords: '#12867a',
    arp: '#1a6f9e',
    lead: '#3a55b8',
    hook: '#6f3fb0',
    pad: '#9c2b96',
    riser: '#b32a6b',
    impact: '#b8262f',
    vocal: '#2b7a3a',
    fx: '#5a5a5a',
  },
};

/** Load a theme by name, falling back to Live's default and then to nothing. */
export async function loadThemeByName(name?: string): Promise<ThemeTokens | null> {
  const themes = await listThemes();
  if (themes.length === 0) return null;
  const wanted =
    themes.find((t) => t.name === name) ??
    themes.find((t) => t.name === DEFAULT_THEME) ??
    themes[0]!;
  return toTokens(await readTheme(wanted.path));
}
