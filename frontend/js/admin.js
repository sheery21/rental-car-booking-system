import { ApiClient, Toast, escapeHtml, money, prettyDate } from "./common.js";

/* Admin panel:
 *   AuthGate       – login form / logout, shows the right view
 *   OverviewView   – stat tiles, two bar lists, latest bookings
 *   BookingsView   – all bookings with filter, search, return / cancel
 *   FleetView      – change daily rate, retire / activate cars
 *   CustomersView  – customer list with totals
 *   AddCarDialog   – form to add a new car
 *   AdminApp       – tabs + wiring; any 401 sends you back to the login
 */

const dates = (b) => `${prettyDate(b.start_date)} → ${prettyDate(b.end_date)}<small>${b.days} day${b.days > 1 ? "s" : ""}</small>`;
const statusPill = (s) => `<span class="status status--${escapeHtml(s)}">${escapeHtml(s)}</span>`;
const emptyRow = (cols, text) => `<tr class="empty-row"><td colspan="${cols}">${text}</td></tr>`;

class AuthGate {
  constructor(api, onLogin) {
    this.api = api;
    this.onLogin = onLogin;
    this.loginView = document.getElementById("login-view");
    this.appView = document.getElementById("app-view");
    this.form = document.getElementById("login-form");
    this.error = document.getElementById("login-error");
    this.form.addEventListener("submit", (e) => this.login(e));
    document.getElementById("logout-btn").addEventListener("click", () => this.logout());
  }

  async check() {
    const { authenticated } = await this.api.get("/api/admin/session");
    authenticated ? this.showApp() : this.showLogin();
  }

  showLogin(message = "") {
    this.appView.hidden = true;
    this.loginView.hidden = false;
    this.error.textContent = message;
    this.form.elements.password.focus();
  }

  showApp() {
    this.loginView.hidden = true;
    this.appView.hidden = false;
    this.onLogin();
  }

  async login(e) {
    e.preventDefault();
    const btn = this.form.querySelector("button");
    btn.disabled = true;
    try {
      await this.api.post("/api/admin/login", { password: this.form.elements.password.value });
      this.form.reset();
      this.showApp();
    } catch (err) {
      this.error.textContent = err.message;
      this.form.elements.password.select();
    } finally {
      btn.disabled = false;
    }
  }

  async logout() {
    await this.api.post("/api/admin/logout").catch(() => {});
    this.showLogin();
  }
}

class OverviewView {
  constructor(api) {
    this.api = api;
    this.tiles = document.getElementById("stat-tiles");
    this.statusBars = document.getElementById("status-bars");
    this.revenueBars = document.getElementById("revenue-bars");
    this.recent = document.getElementById("recent-rows");
  }

  async load() {
    const [s, bookings] = await Promise.all([this.api.get("/api/admin/stats"), this.api.get("/api/admin/bookings")]);
    const tiles = [
      [s.active_bookings, "Active bookings", `${s.total_bookings} in total`],
      [money(s.revenue), "Revenue", "confirmed + completed"],
      [`${s.available_now} / ${s.total_vehicles}`, "Cars free today", s.retired_vehicles ? `${s.retired_vehicles} retired` : "whole fleet active"],
      [s.customers, "Customers", "unique emails"],
    ];
    this.tiles.innerHTML = tiles.map(([v, l, sub]) =>
      `<div class="stat"><b>${escapeHtml(v)}</b><span>${l}</span><small>${escapeHtml(sub)}</small></div>`).join("");

    const statusRows = Object.entries(s.by_status).map(([k, v]) => ({ key: k, label: k, value: v, text: String(v) }));
    this.statusBars.innerHTML = this.bars(statusRows, true);

    const revRows = Object.entries(s.revenue_by_category).sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ key: k, label: k, value: v, text: money(v) }));
    this.revenueBars.innerHTML = revRows.length ? this.bars(revRows, false) : `<p class="muted">No revenue yet.</p>`;

    const latest = bookings.slice(0, 5);
    this.recent.innerHTML = latest.length ? latest.map((b) => `
      <tr>
        <td><b>${escapeHtml(b.booking_id)}</b></td>
        <td>${escapeHtml(b.customer_name)}<small>${escapeHtml(b.customer_email)}</small></td>
        <td>${escapeHtml(b.vehicle_name)}</td>
        <td>${dates(b)}</td>
        <td class="num"><b>${money(b.payment.total)}</b></td>
        <td>${statusPill(b.status)}</td>
      </tr>`).join("") : emptyRow(6, "No bookings yet.");
  }

  bars(rows, statusColours) {
    const max = Math.max(1, ...rows.map((r) => r.value));
    return rows.map((r) => `
      <div class="bar ${statusColours ? `bar--${escapeHtml(r.key)}` : ""}" title="${escapeHtml(r.label)}: ${escapeHtml(r.text)}">
        <span class="bar__label">${statusColours ? `<i class="dot"></i>` : ""}<span style="text-transform:capitalize">${escapeHtml(r.label)}</span></span>
        <span class="bar__track"><span class="bar__fill" style="width:${(r.value / max) * 100}%"></span></span>
        <span class="bar__value">${escapeHtml(r.text)}</span>
      </div>`).join("");
  }
}

class BookingsView {
  constructor(api, toast, onChange) {
    this.api = api;
    this.toast = toast;
    this.onChange = onChange;
    this.rows = document.getElementById("booking-rows");
    this.search = document.getElementById("booking-search");
    this.status = "";
    this.all = [];
    const tabs = document.getElementById("status-tabs");
    tabs.addEventListener("click", (e) => {
      const tab = e.target.closest(".tab");
      if (!tab) return;
      tabs.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
      this.status = tab.dataset.status;
      this.load();
    });
    this.search.addEventListener("input", () => this.render());
    this.rows.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action]");
      if (btn) this.act(btn.dataset.action, btn.dataset.id);
    });
  }

  async load() {
    this.all = await this.api.get("/api/admin/bookings", { status: this.status });
    this.render();
  }

  render() {
    const q = this.search.value.trim().toLowerCase();
    const list = q ? this.all.filter((b) =>
      [b.booking_id, b.customer_name, b.customer_email, b.vehicle_name].some((v) => v.toLowerCase().includes(q))) : this.all;
    this.rows.innerHTML = list.length ? list.map((b) => `
      <tr>
        <td><b>${escapeHtml(b.booking_id)}</b><small>${escapeHtml(prettyDate(b.created_at))}</small></td>
        <td>${escapeHtml(b.customer_name)}<small>${escapeHtml(b.customer_email)}</small></td>
        <td>${escapeHtml(b.vehicle_name)}<small>${escapeHtml(b.vehicle_category)}</small></td>
        <td>${dates(b)}</td>
        <td style="text-transform:capitalize">${escapeHtml(b.payment.method)}<small>fee ${money(b.payment.fee)}</small></td>
        <td class="num"><b>${money(b.payment.total)}</b></td>
        <td>${statusPill(b.status)}</td>
        <td>${b.status === "confirmed" ? `
          <div class="actions">
            <button class="btn btn--sm" data-action="return" data-id="${escapeHtml(b.booking_id)}">Returned</button>
            <button class="btn btn--sm btn--danger" data-action="cancel" data-id="${escapeHtml(b.booking_id)}">Cancel</button>
          </div>` : ""}</td>
      </tr>`).join("") : emptyRow(8, q ? "No bookings match your search." : "No bookings here.");
  }

  async act(action, id) {
    if (action === "cancel" && !confirm(`Cancel booking ${id}?`)) return;
    try {
      await this.api.post(`/api/admin/bookings/${encodeURIComponent(id)}/${action}`);
      this.toast.show(action === "cancel" ? `Booking ${id} cancelled` : `Car returned – ${id} completed`);
      await this.load();
      this.onChange();
    } catch (err) {
      this.toast.show(err.message, true);
    }
  }
}

class FleetView {
  constructor(api, toast) {
    this.api = api;
    this.toast = toast;
    this.rows = document.getElementById("fleet-rows");
    this.rows.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action]");
      if (btn) this.act(btn.dataset.action, btn.dataset.id);
    });
    this.rows.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.matches("[data-rate]")) this.act("rate", e.target.dataset.rate);
    });
  }

  async load() {
    const cars = await this.api.get("/api/admin/vehicles");
    this.rows.innerHTML = cars.map((v) => `
      <tr class="${v.active ? "" : "row--retired"}">
        <td class="car-cell"><b>${escapeHtml(v.name)}</b><small>${escapeHtml(v.vehicle_id)} · ${v.seats} seats · ${escapeHtml(v.transmission)}</small></td>
        <td><span class="badge" data-cat="${escapeHtml(v.category)}">${escapeHtml(v.category)}</span></td>
        <td><div class="rate">
          <input type="number" min="1" step="100" value="${v.daily_rate}" data-rate="${escapeHtml(v.vehicle_id)}" aria-label="Daily rate for ${escapeHtml(v.name)}">
          <button class="btn btn--sm" data-action="rate" data-id="${escapeHtml(v.vehicle_id)}">Save</button>
        </div></td>
        <td>${statusPill(v.active ? "active" : "retired")}${v.out_today ? `<small>out today</small>` : ""}</td>
        <td class="num">${v.bookings}<small>${v.upcoming} upcoming</small></td>
        <td class="num">${money(v.revenue)}</td>
        <td><div class="actions">
          <button class="btn btn--sm ${v.active ? "btn--danger" : ""}" data-action="${v.active ? "retire" : "activate"}" data-id="${escapeHtml(v.vehicle_id)}">
            ${v.active ? "Retire" : "Activate"}</button>
        </div></td>
      </tr>`).join("");
  }

  async act(action, id) {
    const body = action === "rate"
      ? { daily_rate: this.rows.querySelector(`[data-rate="${CSS.escape(id)}"]`).value }
      : { active: action === "activate" };
    if (action === "retire" && !confirm(`Retire ${id}? Customers will no longer see it.`)) return;
    try {
      const car = await this.api.post(`/api/admin/vehicles/${encodeURIComponent(id)}`, body);
      this.toast.show(action === "rate" ? `${car.name}: rate set to ${money(car.daily_rate)}/day`
        : `${car.name} is now ${car.active ? "active" : "retired"}`);
      await this.load();
    } catch (err) {
      this.toast.show(err.message, true);
    }
  }
}

class CustomersView {
  constructor(api) {
    this.api = api;
    this.rows = document.getElementById("customer-rows");
  }

  async load() {
    const list = await this.api.get("/api/admin/customers");
    this.rows.innerHTML = list.length ? list.map((c) => `
      <tr>
        <td><b>${escapeHtml(c.customer_id)}</b></td>
        <td>${escapeHtml(c.name)}</td>
        <td>${escapeHtml(c.email)}</td>
        <td>${escapeHtml(c.phone || "–")}</td>
        <td>${escapeHtml(c.license_no)}</td>
        <td class="num">${c.bookings}<small>last ${escapeHtml(c.last_booking || "–")}</small></td>
        <td class="num"><b>${money(c.spent)}</b></td>
      </tr>`).join("") : emptyRow(7, "No customers yet.");
  }
}

class AddCarDialog {
  constructor(api, toast, onAdded) {
    this.api = api;
    this.toast = toast;
    this.onAdded = onAdded;
    this.dialog = document.getElementById("car-dialog");
    this.form = document.getElementById("car-form");
    this.error = document.getElementById("car-error");
    this.dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => this.dialog.close()));
    this.form.elements.type.addEventListener("change", () => this.showExtras());
    this.form.addEventListener("submit", (e) => this.submit(e));
  }

  open() {
    this.form.reset();
    this.form.elements.year.value = new Date().getFullYear();
    this.error.textContent = "";
    this.showExtras();
    this.dialog.showModal();
  }

  showExtras() {
    const type = this.form.elements.type.value;
    this.form.querySelectorAll("[data-extra]").forEach((el) => { el.hidden = el.dataset.extra !== type; });
  }

  async submit(e) {
    e.preventDefault();
    if (!this.form.reportValidity()) return;
    const f = this.form.elements;
    const body = {
      type: f.type.value, make: f.make.value, model: f.model.value, year: f.year.value,
      seats: f.seats.value, daily_rate: f.daily_rate.value, transmission: f.transmission.value,
      range_km: f.range_km.value, four_wheel_drive: f.four_wheel_drive.checked, chauffeur: f.chauffeur.checked,
    };
    try {
      const car = await this.api.post("/api/admin/vehicles", body);
      this.dialog.close();
      this.toast.show(`${car.name} added as ${car.vehicle_id}`);
      this.onAdded();
    } catch (err) {
      this.error.textContent = err.message;
    }
  }
}

class AdminApp {
  constructor() {
    this.api = new ApiClient();
    this.toast = new Toast();
    this.current = "overview";
    this.views = {
      overview: new OverviewView(this.api),
      bookings: new BookingsView(this.api, this.toast, () => {}),
      fleet: new FleetView(this.api, this.toast),
      customers: new CustomersView(this.api),
    };
    this.gate = new AuthGate(this.api, () => this.show(this.current));
    this.carDialog = new AddCarDialog(this.api, this.toast, () => this.show("fleet"));
    document.getElementById("add-car-btn").addEventListener("click", () => this.carDialog.open());

    this.tabs = document.getElementById("main-tabs");
    this.tabs.addEventListener("click", (e) => {
      const tab = e.target.closest("[data-view]");
      if (tab) this.show(tab.dataset.view);
    });
    document.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => this.show(b.dataset.goto)));
  }

  async show(view) {
    this.current = view;
    this.tabs.querySelectorAll("[data-view]").forEach((t) => t.classList.toggle("is-active", t.dataset.view === view));
    document.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== view; });
    try {
      await this.views[view].load();
    } catch (err) {
      if (err.status === 401) this.gate.showLogin("Your session has ended. Please log in again.");
      else this.toast.show(err.message, true);
    }
  }

  init() {
    this.gate.check().catch(() => this.toast.show("Cannot reach the server. Is `python main.py` running?", true));
  }
}

new AdminApp().init();
