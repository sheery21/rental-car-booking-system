import { ApiClient, Toast, addDays, carArt, escapeHtml, isoDate, money, prettyDate } from "./common.js";

/* Customer booking page:
 *   FleetView      – car cards for the chosen dates
 *   BookingDialog  – booking form, live price quote, confirmation with booking ID
 *   BookingFinder  – "My bookings": all bookings for an email (ID optional), online cancel
 *   BookPage       – wires everything together
 */

class FleetView {
  constructor(grid, note, onBook) {
    this.grid = grid;
    this.note = note;
    this.vehicles = [];
    grid.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-book]");
      if (btn) onBook(this.vehicles.find((v) => v.vehicle_id === btn.dataset.book));
    });
  }

  render(vehicles, range) {
    this.vehicles = vehicles;
    const free = vehicles.filter((v) => v.available).length;
    this.note.textContent = `${free} of ${vehicles.length} free · ${prettyDate(range.start)} → ${prettyDate(range.end)}`;
    this.grid.innerHTML = vehicles.length
      ? vehicles.map((v) => this.card(v)).join("")
      : `<div class="empty">No cars in this category.</div>`;
  }

  card(v) {
    const avail = v.available
      ? `<span class="avail avail--yes">● Free for your dates</span>`
      : `<span class="avail avail--no">● Booked for these dates</span>`;
    return `
      <article class="card" data-cat="${escapeHtml(v.category)}">
        <div class="card__art">${carArt(v.category)}</div>
        <div class="card__body">
          <div class="card__title">
            <h3>${escapeHtml(v.make)} ${escapeHtml(v.model)} <small class="muted">${v.year}</small></h3>
            <span class="badge">${escapeHtml(v.category)}</span>
          </div>
          <ul class="chips">${v.features.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul>
          ${avail}
          <div class="card__foot">
            <div class="price"><b>${money(v.daily_rate)}</b> <span>/ day</span></div>
            <button class="btn btn--primary btn--sm" data-book="${escapeHtml(v.vehicle_id)}" ${v.available ? "" : "disabled"}>Book now</button>
          </div>
        </div>
      </article>`;
  }
}

class BookingDialog {
  constructor(api, onBooked) {
    this.api = api;
    this.onBooked = onBooked;
    this.dialog = document.getElementById("booking-dialog");
    this.form = document.getElementById("booking-form");
    this.quoteEl = document.getElementById("quote");
    this.errorEl = document.getElementById("form-error");
    this.confirmBtn = document.getElementById("confirm-btn");
    this.copyFeedback = document.getElementById("copy-booking-feedback");
    this.vehicle = null;
    this.quoteTimer = null;

    this.dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => this.dialog.close()));
    document.getElementById("copy-booking-id").addEventListener("click", () => this.copyBookingId());
    this.form.addEventListener("change", (e) => {
      if (["start_date", "end_date", "payment_method"].includes(e.target.name)) this.scheduleQuote();
    });
    this.form.addEventListener("submit", (e) => this.submit(e));
  }

  step(name) {
    this.dialog.querySelectorAll("[data-step]").forEach((el) => { el.hidden = el.dataset.step !== name; });
  }

  open(vehicle, range) {
    this.vehicle = vehicle;
    this.step("form");
    this.dialog.dataset.cat = vehicle.category;
    document.getElementById("dlg-category").textContent = vehicle.category;
    document.getElementById("dlg-title").textContent = vehicle.name;
    const f = this.form.elements;
    const today = isoDate(new Date());
    f.start_date.min = today;
    f.end_date.min = addDays(today, 1);
    f.start_date.value = range.start;
    f.end_date.value = range.end;
    this.errorEl.textContent = "";
    this.dialog.showModal();
    this.updateQuote();
  }

  async copyBookingId() {
    const bookingId = document.getElementById("done-id").textContent;
    try {
      await navigator.clipboard.writeText(bookingId);
      this.copyFeedback.textContent = "Booking ID copied.";
    } catch {
      this.copyFeedback.textContent = "Copy failed. Please copy the ID manually.";
    }
  }

  scheduleQuote() {
    clearTimeout(this.quoteTimer);
    this.quoteTimer = setTimeout(() => this.updateQuote(), 150);
  }

  async updateQuote() {
    const f = this.form.elements;
    if (!f.start_date.value || !f.end_date.value) return;
    try {
      const q = await this.api.get("/api/quote", {
        vehicle_id: this.vehicle.vehicle_id,
        start: f.start_date.value,
        end: f.end_date.value,
        payment_method: f.payment_method.value,
      });
      const adjust = q.rent - q.base;
      const adjustLabel = adjust < 0 ? "Category discount" : "Insurance / extras";
      this.quoteEl.innerHTML = `
        <dl>
          <dt>${money(q.daily_rate)} × ${q.days} day${q.days > 1 ? "s" : ""}</dt><dd>${money(q.base)}</dd>
          ${adjust !== 0 ? `<dt>${adjustLabel}</dt><dd>${adjust < 0 ? "−" : "+"} ${money(Math.abs(adjust))}</dd>` : ""}
          <dt>Payment fee</dt><dd>${money(q.fee)}</dd>
          <dt class="total">Total</dt><dd class="total">${money(q.total)}</dd>
        </dl>
        ${q.available ? "" : `<div class="clash">This car is already booked for these dates.</div>`}`;
      this.confirmBtn.disabled = !q.available;
      this.errorEl.textContent = "";
    } catch (err) {
      this.quoteEl.textContent = err.message;
      this.confirmBtn.disabled = true;
    }
  }

  async submit(e) {
    e.preventDefault();
    if (e.submitter?.id !== "confirm-btn" || !this.form.reportValidity()) return;
    const body = Object.fromEntries(new FormData(this.form));
    body.vehicle_id = this.vehicle.vehicle_id;
    this.confirmBtn.disabled = true;
    try {
      const booking = await this.api.post("/api/bookings", body);
      document.getElementById("done-id").textContent = booking.booking_id;
      this.copyFeedback.textContent = "";
      document.getElementById("done-text").textContent =
        `${booking.vehicle_name} · ${prettyDate(booking.start_date)} → ${prettyDate(booking.end_date)} · ` +
        `${money(booking.payment.total)} (${booking.payment.method}). Keep this ID to find or cancel your booking.`;
      this.step("done");
      this.onBooked(booking);
    } catch (err) {
      this.errorEl.textContent = err.message;
    } finally {
      this.confirmBtn.disabled = false;
    }
  }
}

class BookingFinder {
  constructor(api, toast, onChange) {
    this.api = api;
    this.toast = toast;
    this.onChange = onChange;
    this.form = document.getElementById("find-form");
    this.result = document.getElementById("find-result");
    this.query = null;     // { email, booking_id } of the last search
    this.bookings = [];
    this.form.addEventListener("submit", (e) => { e.preventDefault(); this.find(); });
    this.result.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-cancel]");
      if (btn) this.cancel(btn.dataset.cancel);
    });
  }

  /** Email is required; booking ID is optional (empty = all bookings for that email). */
  async find(query = Object.fromEntries(new FormData(this.form))) {
    this.query = query;
    try {
      this.bookings = await this.api.post("/api/bookings/lookup", query);
      this.render();
    } catch (err) {
      this.bookings = [];
      this.result.innerHTML = `<p class="form-error">${escapeHtml(err.message)}</p>`;
    }
  }

  render() {
    const n = this.bookings.length;
    const count = this.query.booking_id ? "" : `<p class="find__count">${n} booking${n > 1 ? "s" : ""} for ${escapeHtml(this.query.email)}</p>`;
    this.result.innerHTML = count + this.bookings.map((b) => `
      <div class="booking-card">
        <dl><dt>Booking</dt><dd>${escapeHtml(b.booking_id)}</dd></dl>
        <dl><dt>Car</dt><dd>${escapeHtml(b.vehicle_name)}</dd></dl>
        <dl><dt>Dates</dt><dd>${prettyDate(b.start_date)} → ${prettyDate(b.end_date)}</dd></dl>
        <dl><dt>Total · <span class="status status--${escapeHtml(b.status)}">${escapeHtml(b.status)}</span></dt><dd>${money(b.payment.total)}</dd></dl>
        ${b.can_cancel ? `<button class="btn btn--danger" data-cancel="${escapeHtml(b.booking_id)}">Cancel booking</button>` : ""}
      </div>`).join("");
  }

  async cancel(bookingId) {
    if (!confirm(`Cancel booking ${bookingId}?`)) return;
    try {
      await this.api.post(`/api/bookings/${encodeURIComponent(bookingId)}/cancel`, { email: this.query.email });
      this.toast.show(`Booking ${bookingId} cancelled`);
      await this.find(this.query);  // refresh the list
      this.onChange();
    } catch (err) {
      this.toast.show(err.message, true);
    }
  }
}

class BookPage {
  constructor() {
    this.api = new ApiClient();
    this.toast = new Toast();
    this.searchForm = document.getElementById("search-form");
    this.dialog = new BookingDialog(this.api, () => this.loadFleet());
    this.fleet = new FleetView(document.getElementById("fleet-grid"), document.getElementById("fleet-note"),
      (vehicle) => this.dialog.open(vehicle, this.range()));
    this.finder = new BookingFinder(this.api, this.toast, () => this.loadFleet());
  }

  range() {
    const f = this.searchForm.elements;
    return { start: f.start.value, end: f.end.value };
  }

  async init() {
    const f = this.searchForm.elements;
    const today = isoDate(new Date());
    f.start.min = today;
    f.end.min = addDays(today, 1);
    f.start.value = today;
    f.end.value = addDays(today, 3);
    f.start.addEventListener("change", () => {
      f.end.min = addDays(f.start.value, 1);
      if (f.end.value <= f.start.value) f.end.value = addDays(f.start.value, 1);
    });
    this.searchForm.addEventListener("submit", (e) => { e.preventDefault(); this.loadFleet(); });
    f.category.addEventListener("change", () => this.loadFleet());

    try {
      const meta = await this.api.get("/api/meta");
      f.category.insertAdjacentHTML("beforeend",
        meta.categories.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join(""));
      const wanted = new URLSearchParams(location.search).get("category");
      if (wanted && meta.categories.includes(wanted)) f.category.value = wanted;
      await this.loadFleet();
    } catch {
      this.toast.show("Cannot reach the server. Is `python main.py` running?", true);
    }
  }

  async loadFleet() {
    try {
      const params = { category: this.searchForm.elements.category.value, ...this.range() };
      this.fleet.render(await this.api.get("/api/vehicles", params), this.range());
    } catch (err) {
      this.toast.show(err.message, true);
    }
  }
}

new BookPage().init();
