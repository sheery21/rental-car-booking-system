// Shared helpers used by every page (ES module).

export const money = (n) => "Rs " + Math.round(Number(n) || 0).toLocaleString("en-PK");

export const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function isoDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function addDays(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDate(new Date(y, m - 1, d + days));
}

export function prettyDate(iso) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export const CATEGORY_RULES = {
  Economy: "10% off on 7+ days",
  SUV: "Off-road insurance included",
  Luxury: "Premium insurance, chauffeur option",
  Electric: "5% green discount",
};

const SEDAN = `<svg viewBox="0 0 200 80" aria-hidden="true">
  <path d="M12 56 L24 41 Q30 34 42 33 L66 31 L88 18 Q94 14 104 14 L134 14 Q145 14 152 21 L166 32 L182 35 Q190 37 190 46 L190 56 Z"/>
  <circle cx="55" cy="58" r="13" fill="#1f2937"/><circle cx="55" cy="58" r="5" fill="#e5e7eb"/>
  <circle cx="152" cy="58" r="13" fill="#1f2937"/><circle cx="152" cy="58" r="5" fill="#e5e7eb"/></svg>`;
const SUV = `<svg viewBox="0 0 200 80" aria-hidden="true">
  <path d="M12 57 L12 38 Q12 31 20 30 L44 28 L60 11 Q64 7 72 7 L150 7 Q158 7 162 13 L176 29 L186 32 Q192 34 192 42 L192 57 Z"/>
  <circle cx="52" cy="59" r="14" fill="#1f2937"/><circle cx="52" cy="59" r="6" fill="#e5e7eb"/>
  <circle cx="154" cy="59" r="14" fill="#1f2937"/><circle cx="154" cy="59" r="6" fill="#e5e7eb"/></svg>`;
export const carArt = (category) => (category === "SUV" ? SUV : SEDAN);

export class ApiClient {
  async request(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }
  get(path, params = {}) {
    const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v != null));
    const qs = new URLSearchParams(clean).toString();
    return this.request(qs ? `${path}?${qs}` : path);
  }
  post(path, body = {}) {
    return this.request(path, { method: "POST", body: JSON.stringify(body) });
  }
}

export class Toast {
  constructor(el = document.getElementById("toast")) { this.el = el; this.timer = null; }
  show(message, isError = false) {
    this.el.textContent = message;
    this.el.classList.toggle("is-error", isError);
    this.el.classList.add("is-visible");
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.el.classList.remove("is-visible"), 3400);
  }
}
