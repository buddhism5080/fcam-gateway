/**
 * Clipboard helpers that work on plain HTTP (LAN IP), not only HTTPS.
 *
 * Problems we hit:
 * - navigator.clipboard.writeText may exist but no-op / reject on http://192.168.x.x
 * - document.execCommand('copy') can return true without writing in some browsers
 *   when the source is a detached / zero-size / opacity:0 node
 *
 * Strategy:
 * 1) Prefer Clipboard API only when window.isSecureContext
 * 2) Prefer selecting a *visible* input/textarea already on the page (best UX)
 * 3) Fallback: temporary *visible* input in-viewport, select, execCommand, remove
 * 4) Optionally verify via clipboard.readText when allowed
 */

function isSecureClipboardAvailable(): boolean {
  try {
    return (
      typeof window !== "undefined" &&
      !!window.isSecureContext &&
      typeof navigator !== "undefined" &&
      !!navigator.clipboard &&
      typeof navigator.clipboard.writeText === "function"
    );
  } catch {
    return false;
  }
}

/** Select full content of an input/textarea element. */
export function selectInputElement(el: HTMLInputElement | HTMLTextAreaElement | null | undefined): boolean {
  if (!el) return false;
  try {
    el.focus();
    el.select();
    // setSelectionRange works for text inputs/textareas; ignore for unsupported types
    try {
      el.setSelectionRange(0, el.value.length);
    } catch {
      /* ignore */
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * Copy from an already-mounted visible input/textarea (recommended).
 * Returns true only when the browser reports success.
 */
export async function copyFromInputElement(
  el: HTMLInputElement | HTMLTextAreaElement | null | undefined,
): Promise<boolean> {
  if (!el) return false;
  const value = el.value ?? "";
  if (!value) return false;

  if (isSecureClipboardAvailable()) {
    try {
      await navigator.clipboard.writeText(value);
      selectInputElement(el);
      return true;
    } catch {
      /* fall through to execCommand */
    }
  }

  if (!selectInputElement(el)) return false;
  try {
    const ok = document.execCommand("copy");
    // Keep selection so user can Ctrl+C if the OS clipboard still empty
    return !!ok;
  } catch {
    return false;
  }
}

/**
 * Copy arbitrary text. Prefer copyFromInputElement when a field is on screen.
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  const value = text ?? "";
  if (!value) return false;

  if (isSecureClipboardAvailable()) {
    try {
      await navigator.clipboard.writeText(value);
      // Verify when possible (some environments resolve without writing)
      try {
        if (typeof navigator.clipboard.readText === "function") {
          const got = await navigator.clipboard.readText();
          if (got === value) return true;
          // read may be empty due to permission — don't treat mismatch as hard fail yet
        } else {
          return true;
        }
      } catch {
        // Cannot verify (permission); assume writeText succeeded if it didn't throw
        return true;
      }
      // If read worked but mismatched, continue to fallback
    } catch {
      /* fall through */
    }
  }

  // Visible in-viewport fallback (hidden/opacity:0 nodes often fake-succeed)
  try {
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.setAttribute("readonly", "readonly");
    // Must be selectable: in layout, non-zero size, not display:none / opacity:0
    ta.style.position = "fixed";
    ta.style.top = "10px";
    ta.style.left = "10px";
    ta.style.width = "2px";
    ta.style.height = "2px";
    ta.style.padding = "0";
    ta.style.margin = "0";
    ta.style.border = "none";
    ta.style.outline = "none";
    ta.style.boxShadow = "none";
    ta.style.background = "#fff";
    ta.style.color = "#000";
    ta.style.opacity = "0.01"; // nearly invisible but not 0 (some engines skip opacity:0)
    ta.style.zIndex = "2147483647";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, value.length);
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } finally {
      document.body.removeChild(ta);
    }
    if (!ok) return false;

    // Best-effort verify
    if (isSecureClipboardAvailable() && typeof navigator.clipboard.readText === "function") {
      try {
        const got = await navigator.clipboard.readText();
        if (got && got !== value) return false;
      } catch {
        /* ignore */
      }
    }
    return true;
  } catch {
    return false;
  }
}
