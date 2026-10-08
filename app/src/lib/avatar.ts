/**
 * Deterministic per-person avatar — a colored disc with white initials, used wherever an
 * employee is shown without a real uploaded photo. The hue is hashed from the opaque token,
 * so it is stable per person and carries no PII. Real photos (when present) take precedence
 * at the call site; this is only the fallback.
 */

export function avatarHue(token: string): number {
  let h = 0;
  for (let i = 0; i < token.length; i++) h = (h * 31 + token.charCodeAt(i)) >>> 0;
  return h % 360;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "";
  const first = parts[0][0] ?? "";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

/** Avatar disc fill — vivid-but-muted, theme-agnostic (white initials read on both themes). */
export function avatarFill(token: string): string {
  return `oklch(0.62 0.13 ${avatarHue(token)})`;
}
