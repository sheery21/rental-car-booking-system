import { ApiClient, CATEGORY_RULES, carArt, escapeHtml, money } from "./common.js";

/** Landing page: fills the hero facts and the category cards from the live fleet. */
class LandingPage {
  constructor(api) {
    this.api = api;
    this.facts = document.getElementById("hero-facts");
    this.cats = document.getElementById("cats");
  }

  async init() {
    try {
      this.render(await this.api.get("/api/vehicles"));
    } catch {
      this.cats.innerHTML = `<p class="muted">Could not load the fleet. Is the server running?</p>`;
    }
  }

  static groupByCategory(vehicles) {
    const groups = new Map();
    for (const v of vehicles) {
      if (!groups.has(v.category)) groups.set(v.category, []);
      groups.get(v.category).push(v);
    }
    return groups;
  }

  render(vehicles) {
    const groups = LandingPage.groupByCategory(vehicles);
    const cheapest = Math.min(...vehicles.map((v) => v.daily_rate));
    const values = [vehicles.length, groups.size, vehicles.length ? `${money(cheapest)}/day` : "–"];
    this.facts.querySelectorAll("dd").forEach((dd, i) => { dd.textContent = values[i]; });

    const order = ["Economy", "SUV", "Electric", "Luxury"];
    const names = [...groups.keys()].sort((a, b) => order.indexOf(a) - order.indexOf(b));
    this.cats.innerHTML = names.map((name) => {
      const cars = groups.get(name);
      const from = Math.min(...cars.map((c) => c.daily_rate));
      const models = cars.map((c) => `${c.make} ${c.model}`).join(", ");
      return `
        <a class="cat" data-cat="${escapeHtml(name)}" href="/book?category=${encodeURIComponent(name)}">
          <div class="cat__art">${carArt(name)}</div>
          <div class="cat__body">
            <span class="badge">${cars.length} car${cars.length > 1 ? "s" : ""}</span>
            <h3>${escapeHtml(name)}</h3>
            <div class="cat__price">from <b>${money(from)}</b> / day</div>
            <div class="cat__rule">${escapeHtml(CATEGORY_RULES[name] ?? "")}</div>
            <div class="cat__rule">${escapeHtml(models)}</div>
            <span class="cat__link">See ${escapeHtml(name)} cars →</span>
          </div>
        </a>`;
    }).join("");
  }
}

new LandingPage(new ApiClient()).init();
