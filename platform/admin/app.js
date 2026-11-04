/* =========================================================================
 * Platform Admin Console — dependency-free SPA talking to /api/v1/admin
 * Real data only: every screen is backed by the running API and PostgreSQL.
 * ========================================================================= */

const API = "/api/v1";
const store = {
  get access() { return sessionStorage.getItem("pf_at"); },
  get refresh() { return sessionStorage.getItem("pf_rt"); },
  set tokens(pair) {
    sessionStorage.setItem("pf_at", pair.access_token);
    sessionStorage.setItem("pf_rt", pair.refresh_token);
  },
  clear() { sessionStorage.removeItem("pf_at"); sessionStorage.removeItem("pf_rt"); },
};

const state = { me: null, route: null, filters: {} };

/* ------------------------------------------------------------------ utils */
const $ = (sel, root = document) => root.querySelector(sel);

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function fmtNumber(n) {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(n);
}

function fmtBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(bytes), i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function fmtDay(value) { return value ? String(value).slice(5) : ""; }

function badge(text, kind = "") { return `<span class="badge ${kind}">${esc(text)}</span>`; }

function statusBadge(status) {
  const map = {
    open: "warn", pending: "info", reviewing: "info", resolved: "ok", rejected: "err",
    closed: "ok", active: "ok", blocked: "err", suspended: "warn", ringing: "warn",
    ended: "ok", declined: "err", missed: "warn", failed: "err", urgent: "err",
    high: "warn", normal: "info", low: "",
  };
  return badge(status, map[status] || "");
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("#toast-root").appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function spinner() { return `<div class="empty"><div class="spinner" style="margin:0 auto 10px"></div>Loading…</div>`; }

function empty(message) { return `<div class="empty">${esc(message)}</div>`; }

/* -------------------------------------------------------------------- api */
class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; this.detail = detail; }
}

async function apiFetch(path, options = {}, retry = true) {
  const headers = Object.assign({}, options.headers);
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  if (store.access) headers["Authorization"] = `Bearer ${store.access}`;

  const response = await fetch(`${API}${path}`, Object.assign({}, options, { headers }));

  if (response.status === 401 && retry && store.refresh) {
    const refreshed = await tryRefresh();
    if (refreshed) return apiFetch(path, options, false);
    logout();
    throw new ApiError(401, "Session expired");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch (_) { /* non-JSON error body */ }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  return type.includes("application/json") ? response.json() : response.text();
}

async function tryRefresh() {
  try {
    const response = await fetch(`${API}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: store.refresh }),
    });
    if (!response.ok) return false;
    store.tokens = await response.json();
    return true;
  } catch (_) {
    return false;
  }
}

const api = {
  get: (path) => apiFetch(path),
  post: (path, body) => apiFetch(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  patch: (path, body) => apiFetch(path, { method: "PATCH", body: JSON.stringify(body ?? {}) }),
  put: (path, body) => apiFetch(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
  del: (path) => apiFetch(path, { method: "DELETE" }),
};

function can(permission) {
  if (!state.me) return false;
  return state.me.permissions.includes(permission) || state.me.role === "super_admin";
}

async function downloadExport(path, filename) {
  const response = await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${store.access}` } });
  if (!response.ok) { toast("Export failed", "err"); return; }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  toast(`Downloaded ${filename}`, "ok");
}

/* ----------------------------------------------------------------- modal */
function openModal({ title, body, footer, width }) {
  const root = $("#modal-root");
  root.innerHTML = `
    <div class="scrim" data-scrim>
      <div class="modal" role="dialog" aria-modal="true" ${width ? `style="width:min(${width},100%)"` : ""}>
        <header><h3>${esc(title)}</h3><div class="spacer"></div>
          <button class="btn ghost sm" data-close>Close</button></header>
        <div class="body">${body}</div>
        ${footer ? `<footer>${footer}</footer>` : ""}
      </div>
    </div>`;
  const close = () => { root.innerHTML = ""; };
  root.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
  root.querySelector("[data-scrim]").addEventListener("click", (event) => {
    if (event.target.hasAttribute("data-scrim")) close();
  });
  document.addEventListener("keydown", function onKey(event) {
    if (event.key === "Escape") { close(); document.removeEventListener("keydown", onKey); }
  });
  return { close, root };
}

async function confirmAction({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const { close, root } = openModal({
      title,
      body: `<p class="muted" style="margin:0">${esc(message)}</p>`,
      footer: `<button class="btn ghost" data-no>Cancel</button>
               <button class="btn ${danger ? "danger" : "primary"}" data-yes>${esc(confirmLabel)}</button>`,
    });
    root.querySelector("[data-no]").addEventListener("click", () => { close(); resolve(false); });
    root.querySelector("[data-yes]").addEventListener("click", () => { close(); resolve(true); });
  });
}

/* ----------------------------------------------------------------- charts */
function lineChart(points, { height = 220 } = {}) {
  if (!points.length) return empty("No data for this period");
  const width = 720, pad = { t: 16, r: 12, b: 26, l: 38 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const maxMessages = Math.max(...points.map((p) => p.messages), 1);
  const maxUsers = Math.max(...points.map((p) => p.users), 1);
  const max = Math.max(maxMessages, maxUsers);
  const x = (i) => pad.l + (points.length === 1 ? innerW / 2 : (i * innerW) / (points.length - 1));
  const y = (v) => pad.t + innerH - (v / max) * innerH;

  const linePath = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.messages).toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L${x(points.length - 1).toFixed(1)},${pad.t + innerH} L${x(0).toFixed(1)},${pad.t + innerH} Z`;
  const barW = Math.max(3, Math.min(18, innerW / points.length / 2.6));

  const bars = points.map((p, i) => {
    const barH = (p.users / max) * innerH;
    return `<rect x="${(x(i) - barW / 2).toFixed(1)}" y="${(pad.t + innerH - barH).toFixed(1)}"
      width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" rx="3" fill="#7a5af8" opacity=".35"></rect>`;
  }).join("");

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const gy = pad.t + innerH - ratio * innerH;
    return `<line x1="${pad.l}" x2="${width - pad.r}" y1="${gy}" y2="${gy}" stroke="#eef1f6" />
      <text x="${pad.l - 8}" y="${gy + 4}" text-anchor="end" font-size="10" fill="#8a97ab">
        ${Math.round(max * ratio)}</text>`;
  }).join("");

  const step = Math.ceil(points.length / 8);
  const labels = points.map((p, i) => (i % step === 0 || i === points.length - 1)
    ? `<text x="${x(i)}" y="${height - 6}" text-anchor="middle" font-size="10" fill="#8a97ab">${esc(fmtDay(p.date))}</text>`
    : "").join("");

  return `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img"
      aria-label="Daily new users and messages">
      ${gridLines}${bars}
      <path d="${areaPath}" fill="#2aabee" opacity=".12"></path>
      <path d="${linePath}" fill="none" stroke="#2aabee" stroke-width="2.5" stroke-linejoin="round"></path>
      ${labels}
    </svg>`;
}

/* ------------------------------------------------------------------ shell */
const NAV = [
  { path: "#/dashboard", label: "Dashboard", icon: "▦", perm: "stats.read" },
  { path: "#/users", label: "Users", icon: "☰", perm: "users.read" },
  { path: "#/chats", label: "Groups & Channels", icon: "◍", perm: "chats.read" },
  { path: "#/reports", label: "Reports", icon: "⚑", perm: "reports.read" },
  { path: "#/tickets", label: "Support", icon: "✉", perm: "tickets.read" },
  { path: "#/roles", label: "Staff & Roles", icon: "◈", perm: "users.read" },
  { path: "#/settings", label: "Platform settings", icon: "⚙", perm: "settings.write" },
  { path: "#/audit", label: "Audit & security", icon: "◎", perm: "audit.read" },
  { path: "#/monitoring", label: "Monitoring", icon: "◔", perm: "monitoring.read" },
];

function renderShell() {
  const navItems = NAV.filter((item) => can(item.perm));
  document.title = "Platform Admin";
  $("#app").innerHTML = `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">P</div>
          <div><h1>Platform Admin</h1><small>${esc(state.me.role || "staff")}</small></div>
        </div>
        <nav class="nav">
          ${navItems.map((item) => `<a href="${item.path}" data-nav="${item.path}">
            <span class="ic">${item.icon}</span>${esc(item.label)}</a>`).join("")}
        </nav>
        <div class="spacer"></div>
        <div class="who"><b>${esc(state.me.display_name)}</b>@${esc(state.me.username)}</div>
        <button class="btn ghost sm" id="logout" style="margin:8px 12px 0;color:#c8d3e3;border-color:#2a3a52">
          Sign out</button>
      </aside>
      <div class="main">
        <header class="topbar">
          <h2 id="page-title">Dashboard</h2>
          <span class="env" id="env-chip">production</span>
          <div class="spacer"></div>
          <button class="btn ghost sm" id="refresh">Refresh</button>
        </header>
        <div class="content" id="view">${spinner()}</div>
      </div>
    </div>`;

  $("#logout").addEventListener("click", logout);
  $("#refresh").addEventListener("click", () => route());
  window.addEventListener("hashchange", route);
  route();
}

function highlightNav(path) {
  document.querySelectorAll("[data-nav]").forEach((link) => {
    link.classList.toggle("active", link.dataset.nav === path);
  });
}

function setTitle(title) {
  const node = $("#page-title");
  if (node) node.textContent = title;
}

function logout() {
  const token = store.access;
  store.clear();
  state.me = null;
  if (token) {
    fetch(`${API}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } })
      .catch(() => {});
  }
  renderLogin();
}

/* ------------------------------------------------------------------ login */
function renderLogin(message = "") {
  document.title = "Sign in · Platform Admin";
  $("#app").innerHTML = `
    <div class="login-wrap">
      <div class="login-card">
        <div class="brand">
          <div class="brand-mark">P</div>
          <div><h1>Platform Admin</h1></div>
        </div>
        <p class="muted small">Sign in with a staff account to manage the platform.</p>
        ${message ? `<div class="badge err" style="margin-bottom:8px">${esc(message)}</div>` : ""}
        <form id="login-form">
          <label class="field">Username
            <input name="username" autocomplete="username" required />
          </label>
          <label class="field">Password
            <input name="password" type="password" autocomplete="current-password" required />
          </label>
          <button class="btn primary" type="submit">Sign in</button>
        </form>
      </div>
    </div>`;

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    const button = event.target.querySelector("button");
    button.disabled = true;
    button.textContent = "Signing in…";
    try {
      const response = await fetch(`${API}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: data.get("username"),
          password: data.get("password"),
          device_name: "admin-console",
          platform: "web",
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "Login failed");
      }
      store.tokens = await response.json();
      const me = await api.get("/admin/me");
      if (!me.role) throw new Error("This account has no administrative role");
      state.me = me;
      location.hash = "#/dashboard";
      renderShell();
    } catch (error) {
      store.clear();
      renderLogin(error.message);
    }
  });
}

/* ----------------------------------------------------------------- router */
async function route() {
  if (!state.me) { renderLogin(); return; }
  const path = location.hash || "#/dashboard";
  highlightNav(path);
  const view = $("#view");
  if (!view) return;

  try {
    switch (path) {
      case "#/dashboard": setTitle("Dashboard"); await pageDashboard(view); break;
      case "#/users": setTitle("Users"); await pageUsers(view); break;
      case "#/chats": setTitle("Groups & Channels"); await pageChats(view); break;
      case "#/reports": setTitle("Reports"); await pageReports(view); break;
      case "#/tickets": setTitle("Support"); await pageTickets(view); break;
      case "#/roles": setTitle("Staff & Roles"); await pageRoles(view); break;
      case "#/settings": setTitle("Platform settings"); await pageSettings(view); break;
      case "#/audit": setTitle("Audit & security"); await pageAudit(view); break;
      case "#/monitoring": setTitle("Monitoring"); await pageMonitoring(view); break;
      default: setTitle("Dashboard"); await pageDashboard(view);
    }
  } catch (error) {
    if (error.status === 401) return;
    view.innerHTML = `<div class="panel"><div class="panel-body">
        <div class="badge err">Request failed</div>
        <p class="muted">${esc(error.message)}</p></div></div>`;
  }
}

/* -------------------------------------------------------------- dashboard */
async function pageDashboard(view) {
  view.innerHTML = spinner();
  const [stats, series, health] = await Promise.all([
    api.get("/admin/stats"),
    api.get("/admin/stats/timeseries?days=14").catch(() => []),
    can("monitoring.read") ? api.get("/admin/health").catch(() => null) : Promise.resolve(null),
  ]);

  const kpis = [
    ["Total users", stats.total_users, `${fmtNumber(stats.new_users_today)} joined today`],
    ["Online now", stats.online_users, "live WebSocket sessions"],
    ["Active today", stats.active_today, `${fmtNumber(stats.active_month)} in last 30 days`],
    ["Messages", stats.total_messages, `${fmtNumber(stats.messages_today)} sent today`],
    ["Groups", stats.total_groups, "private groups"],
    ["Channels", stats.total_channels, "broadcast channels"],
    ["Files", stats.total_files, fmtBytes(stats.storage_bytes) + " stored"],
    ["Open reports", stats.open_reports, `${fmtNumber(stats.open_tickets)} open tickets`],
  ];

  view.innerHTML = `
    <div class="cards">
      ${kpis.map(([label, value, foot]) => `
        <div class="card">
          <h3>${esc(label)}</h3>
          <div class="kpi-value">${esc(fmtNumber(value))}</div>
          <div class="kpi-foot">${esc(foot)}</div>
        </div>`).join("")}
    </div>

    <div class="grid-2">
      <div class="panel">
        <div class="panel-head"><h3>Growth & activity — last 14 days</h3></div>
        ${lineChart(series)}
        <div class="legend">
          <span><i style="background:#2aabee"></i>Messages</span>
          <span><i style="background:#7a5af8;opacity:.55"></i>New users</span>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h3>System health</h3></div>
        <div class="panel-body">
          ${health ? `
            <div class="health-row"><span class="muted">Database</span>${badge(health.database, health.database === "ok" ? "ok" : "err")}</div>
            <div class="health-row"><span class="muted">Redis</span>${badge(health.redis, health.redis === "ok" || health.redis === "disabled" ? "ok" : "err")}</div>
            <div class="health-row"><span class="muted">API</span>${badge(health.api, "ok")}</div>
            <div class="health-row"><span class="muted">Live sockets</span><b>${fmtNumber(health.websocket_connections)}</b></div>
            <div class="health-row"><span class="muted">CPU</span>
              <span class="meter"><span style="width:${Math.min(100, health.cpu_percent)}%"></span></span>
              <b>${health.cpu_percent.toFixed(0)}%</b></div>
            <div class="health-row"><span class="muted">Memory</span>
              <span class="meter"><span style="width:${Math.min(100, health.memory_percent)}%;background:#f79009"></span></span>
              <b>${health.memory_percent.toFixed(0)}%</b></div>
            <div class="health-row"><span class="muted">Disk</span>
              <span class="meter"><span style="width:${Math.min(100, health.disk_percent)}%;background:#7a5af8"></span></span>
              <b>${health.disk_percent.toFixed(0)}%</b></div>
            <div class="health-row"><span class="muted">Uptime</span><b>${Math.floor(health.uptime_seconds / 60)} min</b></div>
          ` : `<p class="muted small">Monitoring permission required.</p>`}
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Quick actions</h3><div class="spacer"></div></div>
      <div class="panel-body toolbar">
        <a class="btn ghost" href="#/users">Manage users</a>
        <a class="btn ghost" href="#/reports">Review reports</a>
        <a class="btn ghost" href="#/tickets">Open support queue</a>
        ${can("export.data") ? `
          <button class="btn ghost" id="exp-users">Export users CSV</button>
          <button class="btn ghost" id="exp-pdf">Download summary PDF</button>` : ""}
      </div>
    </div>`;

  if (can("export.data")) {
    $("#exp-users")?.addEventListener("click", () => downloadExport("/admin/export/users.csv", "users.csv"));
    $("#exp-pdf")?.addEventListener("click", () => downloadExport("/admin/export/summary.pdf", "platform-summary.pdf"));
  }
}

/* ------------------------------------------------------------------ users */
async function pageUsers(view) {
  const query = state.filters.users || { q: "", status: "", offset: 0 };
  const params = new URLSearchParams({ limit: "25", offset: String(query.offset) });
  if (query.q) params.set("q", query.q);
  if (query.status) params.set("status", query.status);

  const data = await api.get(`/admin/users?${params}`);
  const roles = await api.get("/admin/roles").catch(() => []);

  view.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <h3>Users <span class="muted small">(${fmtNumber(data.total)})</span></h3>
        <div class="spacer"></div>
        <div class="toolbar">
          <input id="user-q" placeholder="Search username or name" value="${esc(query.q)}" style="width:230px" />
          <select id="user-status" style="width:150px">
            ${["", "active", "blocked", "suspended"].map((value) =>
              `<option value="${value}" ${query.status === value ? "selected" : ""}>
                 ${value ? value[0].toUpperCase() + value.slice(1) : "All statuses"}</option>`).join("")}
          </select>
          <button class="btn primary sm" id="user-search">Search</button>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr>
            <th>Username</th><th>Name</th><th>Role</th><th>Status</th><th>Last seen</th>
            <th>Registered</th><th>Actions</th>
          </tr></thead>
          <tbody>
            ${data.items.length ? data.items.map((user) => `
              <tr>
                <td class="mono">@${esc(user.username)}</td>
                <td>${esc(user.display_name)}</td>
                <td>${user.role ? badge(user.role, "staff") : `<span class="muted">user</span>`}</td>
                <td>${user.is_blocked ? badge("blocked", "err")
                     : user.suspended_until && new Date(user.suspended_until) > new Date()
                       ? badge("suspended", "warn")
                       : user.is_active ? badge("active", "ok") : badge("deactivated", "")}</td>
                <td class="small muted">${esc(fmtDate(user.last_seen_at))}</td>
                <td class="small muted">${esc(fmtDate(user.created_at))}</td>
                <td class="actions">
                  <button class="btn ghost sm" data-detail="${user.id}">Details</button>
                  ${can("users.block") ? `<button class="btn ${user.is_blocked ? "ghost" : "danger"} sm"
                     data-block="${user.id}" data-blocked="${user.is_blocked}">
                     ${user.is_blocked ? "Unblock" : "Block"}</button>` : ""}
                  ${can("roles.write") ? `<button class="btn ghost sm" data-role="${user.id}">Role</button>` : ""}
                </td>
              </tr>`).join("") : `<tr><td colspan="7">${empty("No users match this filter")}</td></tr>`}
          </tbody>
        </table>
      </div>
      <div class="pager">
        <button class="btn ghost sm" id="prev" ${query.offset === 0 ? "disabled" : ""}>Previous</button>
        <button class="btn ghost sm" id="next" ${query.offset + 25 >= data.total ? "disabled" : ""}>Next</button>
        <div class="spacer"></div>
        <span class="muted small">Showing ${data.items.length} of ${fmtNumber(data.total)}</span>
      </div>
    </div>`;

  const applyFilter = () => {
    state.filters.users = {
      q: $("#user-q").value.trim(),
      status: $("#user-status").value,
      offset: 0,
    };
    route();
  };
  $("#user-search").addEventListener("click", applyFilter);
  $("#user-q").addEventListener("keydown", (event) => { if (event.key === "Enter") applyFilter(); });
  $("#user-status").addEventListener("change", applyFilter);
  $("#prev").addEventListener("click", () => {
    state.filters.users = { ...query, offset: Math.max(0, query.offset - 25) }; route();
  });
  $("#next").addEventListener("click", () => {
    state.filters.users = { ...query, offset: query.offset + 25 }; route();
  });

  view.querySelectorAll("[data-detail]").forEach((button) => {
    button.addEventListener("click", () => showUserDetail(button.dataset.detail));
  });
  view.querySelectorAll("[data-block]").forEach((button) => {
    button.addEventListener("click", async () => {
      const blocked = button.dataset.blocked === "true";
      const ok = await confirmAction({
        title: blocked ? "Unblock user" : "Block user",
        message: blocked
          ? "The account will be able to sign in again immediately."
          : "The account will be locked out instantly and its access tokens rejected.",
        confirmLabel: blocked ? "Unblock" : "Block",
        danger: !blocked,
      });
      if (!ok) return;
      try {
        await api.post(`/admin/users/${button.dataset.block}/block`,
          { blocked: !blocked, reason: blocked ? null : "Blocked by staff" });
        toast(blocked ? "User unblocked" : "User blocked", "ok");
        route();
      } catch (error) { toast(error.message, "err"); }
    });
  });
  view.querySelectorAll("[data-role]").forEach((button) => {
    button.addEventListener("click", () => showRoleDialog(button.dataset.role, roles));
  });
}

async function showUserDetail(userId) {
  const data = await api.get(`/admin/users/${userId}`);
  const user = data.user;
  const { root } = openModal({
    title: `@${user.username}`,
    width: "620px",
    body: `
      <dl class="kv">
        <dt>Display name</dt><dd>${esc(user.display_name)}</dd>
        <dt>User ID</dt><dd class="mono">${esc(user.id)}</dd>
        <dt>Status</dt><dd>${user.is_blocked ? badge("blocked", "err") : badge("active", "ok")}</dd>
        <dt>Role</dt><dd>${user.role ? badge(user.role, "staff") : "—"}</dd>
        <dt>Registered</dt><dd>${esc(fmtDate(user.created_at))}</dd>
        <dt>Last seen</dt><dd>${esc(fmtDate(user.last_seen_at))}</dd>
        ${user.block_reason ? `<dt>Block reason</dt><dd>${esc(user.block_reason)}</dd>` : ""}
        ${user.suspended_until ? `<dt>Suspended until</dt><dd>${esc(fmtDate(user.suspended_until))}</dd>` : ""}
      </dl>
      <div class="grid-2 even" style="gap:10px">
        ${Object.entries(data.stats).map(([key, value]) => `
          <div class="card" style="box-shadow:none">
            <h3>${esc(key.replace(/_/g, " "))}</h3>
            <div class="kpi-value" style="font-size:20px">${fmtNumber(value)}</div>
          </div>`).join("")}
      </div>
      <div>
        <h4 style="margin:12px 0 8px;font-size:13px">Recent security events</h4>
        ${data.security_events.length ? `<table class="data"><thead><tr>
            <th>Event</th><th>IP</th><th>When</th></tr></thead><tbody>
            ${data.security_events.map((event) => `<tr>
              <td>${esc(event.type)}</td><td class="mono">${esc(event.ip || "—")}</td>
              <td class="small muted">${esc(fmtDate(event.created_at))}</td></tr>`).join("")}
          </tbody></table>` : `<p class="muted small">No security events recorded.</p>`}
      </div>`,
    footer: `${can("users.write") ? `<button class="btn danger" data-suspend>Suspend 7 days</button>` : ""}
             <button class="btn ghost" data-close>Close</button>`,
  });

  root.querySelector("[data-suspend]")?.addEventListener("click", async () => {
    const until = new Date(Date.now() + 7 * 86400000).toISOString();
    try {
      await api.post(`/admin/users/${userId}/suspend`, { until, reason: "Suspended by staff" });
      toast("User suspended for 7 days", "ok");
      root.innerHTML = ""; route();
    } catch (error) { toast(error.message, "err"); }
  });
}

function showRoleDialog(userId, roles) {
  const { close, root } = openModal({
    title: "Assign administrative role",
    body: `
      <label class="field">Role
        <select id="role-select">
          <option value="none">No administrative access</option>
          ${roles.map((role) => `<option value="${esc(role.name)}">${esc(role.name)} — ${esc(role.description || "")}</option>`).join("")}
        </select>
      </label>
      <p class="muted small">Permissions apply immediately to the next request.</p>`,
    footer: `<button class="btn ghost" data-close>Cancel</button>
             <button class="btn primary" id="assign">Assign</button>`,
  });
  root.querySelector("#assign").addEventListener("click", async () => {
    try {
      await api.post(`/admin/users/${userId}/role`, { role: root.querySelector("#role-select").value });
      toast("Role updated", "ok"); close(); route();
    } catch (error) { toast(error.message, "err"); }
  });
}

/* ------------------------------------------------------------------ chats */
async function pageChats(view) {
  const query = state.filters.chats || { q: "", type: "", offset: 0 };
  const params = new URLSearchParams({ limit: "25", offset: String(query.offset) });
  if (query.q) params.set("q", query.q);
  if (query.type) params.set("type", query.type);

  const data = await api.get(`/admin/chats?${params}`);
  view.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <h3>Groups & Channels <span class="muted small">(${fmtNumber(data.total)})</span></h3>
        <div class="spacer"></div>
        <div class="toolbar">
          <input id="chat-q" placeholder="Search title or username" value="${esc(query.q)}" style="width:220px" />
          <select id="chat-type" style="width:150px">
            <option value="">All types</option>
            ${["group", "channel", "private", "saved"].map((type) =>
              `<option value="${type}" ${query.type === type ? "selected" : ""}>${type}</option>`).join("")}
          </select>
          <button class="btn primary sm" id="chat-search">Filter</button>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr><th>Title</th><th>Type</th><th>Username</th><th>Members</th><th>Created</th><th>Actions</th></tr></thead>
          <tbody>
            ${data.items.length ? data.items.map((chat) => `
              <tr>
                <td>${esc(chat.title || "(private chat)")}</td>
                <td>${badge(chat.type, chat.type === "channel" ? "info" : "")}</td>
                <td class="mono small">${chat.username ? "@" + esc(chat.username) : "—"}</td>
                <td>${fmtNumber(chat.member_count)}</td>
                <td class="small muted">${esc(fmtDate(chat.created_at))}</td>
                <td class="actions">
                  <button class="btn ghost sm" data-chat="${chat.id}">Inspect</button>
                  ${can("chats.write") && chat.type !== "private" && chat.type !== "saved"
                    ? `<button class="btn danger sm" data-del-chat="${chat.id}">Delete</button>` : ""}
                </td>
              </tr>`).join("") : `<tr><td colspan="6">${empty("No chats found")}</td></tr>`}
          </tbody>
        </table>
      </div>
      <div class="pager">
        <button class="btn ghost sm" id="prev" ${query.offset === 0 ? "disabled" : ""}>Previous</button>
        <button class="btn ghost sm" id="next" ${query.offset + 25 >= data.total ? "disabled" : ""}>Next</button>
        <div class="spacer"></div><span class="muted small">Total ${fmtNumber(data.total)}</span>
      </div>
    </div>`;

  $("#chat-search").addEventListener("click", () => {
    state.filters.chats = { q: $("#chat-q").value.trim(), type: $("#chat-type").value, offset: 0 };
    route();
  });
  $("#prev").addEventListener("click", () => { state.filters.chats = { ...query, offset: Math.max(0, query.offset - 25) }; route(); });
  $("#next").addEventListener("click", () => { state.filters.chats = { ...query, offset: query.offset + 25 }; route(); });

  view.querySelectorAll("[data-chat]").forEach((button) => {
    button.addEventListener("click", async () => {
      const detail = await api.get(`/admin/chats/${button.dataset.chat}`);
      openModal({
        title: detail.chat.title || "Private chat",
        width: "640px",
        body: `
          <dl class="kv">
            <dt>Chat ID</dt><dd class="mono">${esc(detail.chat.id)}</dd>
            <dt>Type</dt><dd>${badge(detail.chat.type)}</dd>
            <dt>Username</dt><dd>${detail.chat.username ? "@" + esc(detail.chat.username) : "—"}</dd>
            <dt>Members</dt><dd>${fmtNumber(detail.chat.member_count)}</dd>
            <dt>Messages</dt><dd>${fmtNumber(detail.message_count)}</dd>
            <dt>Created</dt><dd>${esc(fmtDate(detail.chat.created_at))}</dd>
          </dl>
          <h4 style="margin:10px 0 6px;font-size:13px">Members</h4>
          <table class="data"><thead><tr><th>Username</th><th>Name</th><th>Role</th></tr></thead>
            <tbody>${detail.members.map((member) => `<tr>
              <td class="mono">@${esc(member.username)}</td><td>${esc(member.display_name)}</td>
              <td>${badge(member.role, member.role === "owner" ? "staff" : "")}</td></tr>`).join("")}
            </tbody></table>`,
        footer: `<button class="btn ghost" data-close>Close</button>`,
      });
    });
  });
  view.querySelectorAll("[data-del-chat]").forEach((button) => {
    button.addEventListener("click", async () => {
      const ok = await confirmAction({
        title: "Delete chat",
        message: "This permanently deletes the chat and all of its messages.",
        confirmLabel: "Delete", danger: true,
      });
      if (!ok) return;
      try { await api.del(`/admin/chats/${button.dataset.delChat}`); toast("Chat deleted", "ok"); route(); }
      catch (error) { toast(error.message, "err"); }
    });
  });
}

/* ---------------------------------------------------------------- reports */
async function pageReports(view) {
  const statusFilter = state.filters.reports || "";
  const data = await api.get(`/admin/reports?limit=50${statusFilter ? `&status=${statusFilter}` : ""}`);
  view.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <h3>Abuse reports <span class="muted small">(${fmtNumber(data.total)})</span></h3>
        <div class="spacer"></div>
        <select id="report-status" style="width:170px">
          ${["", "open", "reviewing", "resolved", "rejected"].map((value) =>
            `<option value="${value}" ${statusFilter === value ? "selected" : ""}>
               ${value ? value[0].toUpperCase() + value.slice(1) : "All statuses"}</option>`).join("")}
        </select>
      </div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr><th>Target</th><th>Reason</th><th>Description</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead>
          <tbody>
            ${data.items.length ? data.items.map((report) => `
              <tr>
                <td>${badge(report.target_type)}<div class="mono small muted">${esc(report.target_id.slice(0, 12))}…</div></td>
                <td>${esc(report.reason)}</td>
                <td class="small">${esc(report.description || "—")}</td>
                <td>${statusBadge(report.status)}</td>
                <td class="small muted">${esc(fmtDate(report.created_at))}</td>
                <td class="actions">
                  ${can("reports.write") && ["open", "reviewing"].includes(report.status)
                    ? `<button class="btn ghost sm" data-review="${report.id}">Review</button>` : ""}
                </td>
              </tr>`).join("") : `<tr><td colspan="6">${empty("No reports")}</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>`;

  $("#report-status").addEventListener("change", (event) => {
    state.filters.reports = event.target.value; route();
  });
  view.querySelectorAll("[data-review]").forEach((button) => {
    button.addEventListener("click", () => {
      const { close, root } = openModal({
        title: "Resolve report",
        body: `
          <label class="field">Decision
            <select id="decision">
              <option value="resolved">Resolved — action taken</option>
              <option value="rejected">Rejected — no violation</option>
              <option value="reviewing">Keep under review</option>
            </select>
          </label>
          <label class="field">Resolution note
            <textarea id="resolution" placeholder="What did you do?"></textarea>
          </label>`,
        footer: `<button class="btn ghost" data-close>Cancel</button>
                 <button class="btn primary" id="save">Save decision</button>`,
      });
      root.querySelector("#save").addEventListener("click", async () => {
        try {
          await api.post(`/admin/reports/${button.dataset.review}/resolve`, {
            status: root.querySelector("#decision").value,
            resolution: root.querySelector("#resolution").value || null,
          });
          toast("Report updated", "ok"); close(); route();
        } catch (error) { toast(error.message, "err"); }
      });
    });
  });
}

/* ---------------------------------------------------------------- tickets */
async function pageTickets(view) {
  const filter = state.filters.tickets || { status: "", assigned: "" };
  const params = new URLSearchParams({ limit: "50" });
  if (filter.status) params.set("status", filter.status);
  if (filter.assigned) params.set("assigned", filter.assigned);

  const [data, performance] = await Promise.all([
    api.get(`/admin/tickets?${params}`),
    api.get("/admin/tickets-performance").catch(() => []),
  ]);

  view.innerHTML = `
    <div class="grid-2 even">
      <div class="panel">
        <div class="panel-head">
          <h3>Support queue <span class="muted small">(${fmtNumber(data.total)})</span></h3>
          <div class="spacer"></div>
          <select id="ticket-status" style="width:140px">
            ${["", "open", "pending", "resolved", "closed"].map((value) =>
              `<option value="${value}" ${filter.status === value ? "selected" : ""}>
                 ${value ? value[0].toUpperCase() + value.slice(1) : "All"}</option>`).join("")}
          </select>
          <select id="ticket-assigned" style="width:150px">
            <option value="">Everyone</option>
            <option value="unassigned" ${filter.assigned === "unassigned" ? "selected" : ""}>Unassigned</option>
            <option value="me" ${filter.assigned === "me" ? "selected" : ""}>Assigned to me</option>
          </select>
        </div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>Subject</th><th>Priority</th><th>Status</th><th>Created</th><th></th></tr></thead>
            <tbody>
              ${data.items.length ? data.items.map((ticket) => `
                <tr>
                  <td>${esc(ticket.subject)}<div class="small muted">${esc(ticket.category)}</div></td>
                  <td>${statusBadge(ticket.priority)}</td>
                  <td>${statusBadge(ticket.status)}</td>
                  <td class="small muted">${esc(fmtDate(ticket.created_at))}</td>
                  <td><button class="btn ghost sm" data-ticket="${ticket.id}">Open</button></td>
                </tr>`).join("") : `<tr><td colspan="5">${empty("Queue is empty")}</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head"><h3>Agent performance</h3></div>
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>Agent</th><th>Assigned</th><th>Resolved</th><th>Replies</th></tr></thead>
            <tbody>
              ${performance.length ? performance.map((row) => `<tr>
                <td class="mono">@${esc(row.username)}</td>
                <td>${fmtNumber(row.assigned)}</td>
                <td>${fmtNumber(row.resolved)}</td>
                <td>${fmtNumber(row.replies)}</td></tr>`).join("")
                : `<tr><td colspan="4">${empty("No staff activity yet")}</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>
    </div>`;

  $("#ticket-status").addEventListener("change", (event) => {
    state.filters.tickets = { ...filter, status: event.target.value }; route();
  });
  $("#ticket-assigned").addEventListener("change", (event) => {
    state.filters.tickets = { ...filter, assigned: event.target.value }; route();
  });
  view.querySelectorAll("[data-ticket]").forEach((button) => {
    button.addEventListener("click", () => openTicket(button.dataset.ticket));
  });
}

async function openTicket(ticketId) {
  const detail = await api.get(`/admin/tickets/${ticketId}`);
  const ticket = detail.ticket;
  const { root } = openModal({
    title: ticket.subject,
    width: "680px",
    body: `
      <div class="toolbar">
        ${statusBadge(ticket.status)} ${statusBadge(ticket.priority)}
        <span class="muted small">${esc(ticket.category)} · opened ${esc(fmtDate(ticket.created_at))}</span>
      </div>
      <div class="thread">
        ${detail.messages.map((message) => `
          <div class="bubble ${message.is_staff ? "staff" : ""}">
            <div class="meta">${message.is_staff ? "Staff" : "User"} · ${esc(fmtDate(message.created_at))}</div>
            ${esc(message.body)}
          </div>`).join("")}
      </div>
      ${can("tickets.write") ? `
        <label class="field">Reply as staff
          <textarea id="reply-body" placeholder="Type your reply…"></textarea>
        </label>
        <div class="toolbar">
          <select id="ticket-priority" style="width:130px">
            ${["low", "normal", "high", "urgent"].map((value) =>
              `<option value="${value}" ${ticket.priority === value ? "selected" : ""}>${value}</option>`).join("")}
          </select>
          <select id="ticket-state" style="width:130px">
            ${["open", "pending", "resolved", "closed"].map((value) =>
              `<option value="${value}" ${ticket.status === value ? "selected" : ""}>${value}</option>`).join("")}
          </select>
          <button class="btn ghost sm" id="assign-me">Assign to me</button>
        </div>` : ""}`,
    footer: `${can("tickets.write") ? `<button class="btn primary" id="send-reply">Send reply</button>` : ""}
             <button class="btn ghost" data-close>Close</button>`,
  });

  if (!can("tickets.write")) return;

  root.querySelector("#send-reply").addEventListener("click", async () => {
    const body = root.querySelector("#reply-body").value.trim();
    if (!body) { toast("Reply cannot be empty", "err"); return; }
    try {
      await api.post(`/admin/tickets/${ticketId}/reply`, { body });
      await api.patch(`/admin/tickets/${ticketId}`, {
        priority: root.querySelector("#ticket-priority").value,
        status: root.querySelector("#ticket-state").value,
      });
      toast("Reply sent", "ok");
      root.innerHTML = "";
      await openTicket(ticketId);
      route();
    } catch (error) { toast(error.message, "err"); }
  });

  root.querySelector("#assign-me").addEventListener("click", async () => {
    try {
      await api.patch(`/admin/tickets/${ticketId}`, { assigned_to: state.me.id });
      toast("Ticket assigned to you", "ok");
      root.innerHTML = "";
      await openTicket(ticketId);
      route();
    } catch (error) { toast(error.message, "err"); }
  });
}

/* ------------------------------------------------------------------ roles */
async function pageRoles(view) {
  const roles = await api.get("/admin/roles");
  view.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <h3>Roles & permissions</h3><div class="spacer"></div>
        ${can("roles.write") ? `<button class="btn primary sm" id="new-role">New role</button>` : ""}
      </div>
      <div class="table-wrap">
        <table class="data">
          <thead><tr><th>Role</th><th>Description</th><th>Permissions</th><th></th></tr></thead>
          <tbody>
            ${roles.map((role) => `
              <tr>
                <td>${badge(role.name, role.is_system ? "staff" : "")}</td>
                <td class="small muted">${esc(role.description || "—")}</td>
                <td><div class="chip-row">${role.permissions.map((permission) =>
                  `<span class="badge">${esc(permission)}</span>`).join("")}</div></td>
                <td>${can("roles.write") && role.name !== "super_admin"
                  ? `<button class="btn ghost sm" data-edit="${role.id}">Edit</button>` : ""}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;

  $("#new-role")?.addEventListener("click", () => roleForm(null, roles));
  view.querySelectorAll("[data-edit]").forEach((button) => {
    button.addEventListener("click", () => {
      const role = roles.find((item) => item.id === button.dataset.edit);
      roleForm(role, roles);
    });
  });
}

function roleForm(role, allRoles) {
  const ALL = ["users.read", "users.write", "users.block", "chats.read", "chats.write",
    "messages.read", "messages.delete", "reports.read", "reports.write", "tickets.read",
    "tickets.write", "roles.write", "settings.write", "audit.read", "monitoring.read",
    "stats.read", "export.data"];
  const selected = new Set(role ? role.permissions : ["stats.read"]);
  const { close, root } = openModal({
    title: role ? `Edit role ${role.name}` : "New role",
    width: "560px",
    body: `
      <label class="field">Name
        <input id="role-name" value="${role ? esc(role.name) : ""}" ${role ? "disabled" : ""}
               placeholder="chat_moderator" />
      </label>
      <label class="field">Description
        <input id="role-desc" value="${role ? esc(role.description || "") : ""}" />
      </label>
      <div class="field"><span class="muted">Permissions</span>
        <div class="chip-row" id="perms">
          ${ALL.map((permission) => `
            <label class="badge" style="cursor:pointer;gap:8px">
              <input type="checkbox" value="${permission}" ${selected.has(permission) ? "checked" : ""}
                     style="width:auto;margin:0" />${permission}
            </label>`).join("")}
        </div>
      </div>`,
    footer: `<button class="btn ghost" data-close>Cancel</button>
             <button class="btn primary" id="save-role">Save</button>`,
  });
  root.querySelector("#save-role").addEventListener("click", async () => {
    const permissions = [...root.querySelectorAll("#perms input:checked")].map((input) => input.value);
    try {
      if (role) {
        await api.patch(`/admin/roles/${role.id}`, {
          description: root.querySelector("#role-desc").value || null,
          permissions,
        });
      } else {
        await api.post("/admin/roles", {
          name: root.querySelector("#role-name").value.trim(),
          description: root.querySelector("#role-desc").value || null,
          permissions,
        });
      }
      toast("Role saved", "ok"); close(); route();
    } catch (error) { toast(error.message, "err"); }
  });
  void allRoles;
}

/* --------------------------------------------------------------- settings */
async function pageSettings(view) {
  const [settings, stats] = await Promise.all([
    api.get("/admin/settings"),
    api.get("/admin/stats").catch(() => null),
  ]);
  const maintenance = settings.find((row) => row.key === "maintenance");
  const isOn = Boolean(maintenance?.value?.enabled);

  view.innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <h3>Maintenance mode</h3><div class="spacer"></div>
        ${isOn ? badge("active", "warn") : badge("disabled", "ok")}
        <button class="btn ${isOn ? "ghost" : "danger"} sm" id="toggle-maintenance">
          ${isOn ? "Disable" : "Enable"}</button>
      </div>
      <div class="panel-body">
        <p class="muted small" style="margin:0">
          While enabled, every non-authentication API request is answered with HTTP 503.
          Administrators keep access to this console.
        </p>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><h3>Platform configuration</h3><div class="spacer"></div>
        <button class="btn primary sm" id="save-settings">Save changes</button></div>
      <div class="panel-body" style="display:grid;gap:14px">
        ${settings.filter((row) => row.key !== "maintenance").map((row) => `
          <div class="card" style="box-shadow:none">
            <h3>${esc(row.key)}</h3>
            <textarea data-key="${esc(row.key)}" class="mono small"
              style="min-height:74px">${esc(JSON.stringify(row.value, null, 2))}</textarea>
          </div>`).join("")}
      </div>
    </div>

    ${stats ? `<div class="panel">
      <div class="panel-head"><h3>Data exports</h3></div>
      <div class="panel-body toolbar">
        ${["users", "messages", "chats", "reports", "tickets", "calls"].map((dataset) =>
          `<button class="btn ghost sm" data-export="${dataset}">${dataset}.csv</button>`).join("")}
        <button class="btn ghost sm" data-export-pdf>summary.pdf</button>
      </div>
    </div>` : ""}`;

  $("#toggle-maintenance").addEventListener("click", async () => {
    try {
      await api.post("/admin/maintenance", { enabled: !isOn, message: "Scheduled maintenance" });
      toast(`Maintenance ${!isOn ? "enabled" : "disabled"}`, "ok");
      route();
    } catch (error) { toast(error.message, "err"); }
  });

  $("#save-settings").addEventListener("click", async () => {
    const values = {};
    let invalid = false;
    view.querySelectorAll("textarea[data-key]").forEach((area) => {
      try { values[area.dataset.key] = JSON.parse(area.value); }
      catch (_) { invalid = true; toast(`Invalid JSON in ${area.dataset.key}`, "err"); }
    });
    if (invalid) return;
    try {
      await api.put("/admin/settings", { values });
      toast("Settings saved", "ok");
      route();
    } catch (error) { toast(error.message, "err"); }
  });

  view.querySelectorAll("[data-export]").forEach((button) => {
    button.addEventListener("click", () =>
      downloadExport(`/admin/export/${button.dataset.export}.csv`, `${button.dataset.export}.csv`));
  });
  view.querySelector("[data-export-pdf]")?.addEventListener("click", () =>
    downloadExport("/admin/export/summary.pdf", "platform-summary.pdf"));
}

/* ------------------------------------------------------------------ audit */
async function pageAudit(view) {
  const [audit, events] = await Promise.all([
    api.get("/admin/audit?limit=100"),
    api.get("/admin/security-events?limit=100").catch(() => []),
  ]);

  view.innerHTML = `
    <div class="grid-2 even">
      <div class="panel">
        <div class="panel-head"><h3>Administrative audit trail</h3></div>
        <div class="table-wrap" style="max-height:520px">
          <table class="data">
            <thead><tr><th>Action</th><th>Target</th><th>IP</th><th>When</th></tr></thead>
            <tbody>
              ${audit.length ? audit.map((entry) => `<tr>
                <td><b>${esc(entry.action)}</b>${entry.meta && Object.keys(entry.meta).length
                  ? `<div class="small muted mono">${esc(JSON.stringify(entry.meta))}</div>` : ""}</td>
                <td class="small">${esc(entry.target_type || "—")}<div class="mono muted">${esc((entry.target_id || "").slice(0, 12))}</div></td>
                <td class="mono small">${esc(entry.ip || "—")}</td>
                <td class="small muted">${esc(fmtDate(entry.created_at))}</td></tr>`).join("")
                : `<tr><td colspan="4">${empty("No audit entries")}</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h3>Security events</h3></div>
        <div class="table-wrap" style="max-height:520px">
          <table class="data">
            <thead><tr><th>Event</th><th>IP</th><th>When</th></tr></thead>
            <tbody>
              ${events.length ? events.map((event) => `<tr>
                <td>${badge(event.type, event.type.includes("failed") || event.type.includes("reuse") ? "err" : "info")}</td>
                <td class="mono small">${esc(event.ip || "—")}</td>
                <td class="small muted">${esc(fmtDate(event.created_at))}</td></tr>`).join("")
                : `<tr><td colspan="3">${empty("No security events")}</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>
    </div>`;
}

/* ------------------------------------------------------------- monitoring */
async function pageMonitoring(view) {
  const [health, errors, ready] = await Promise.all([
    api.get("/admin/health"),
    api.get("/admin/monitoring/errors?limit=50"),
    fetch(`${API}/readyz`).then((response) => response.json()).catch(() => null),
  ]);

  const meterRow = (label, percent, color) => `
    <div class="health-row">
      <span class="muted">${label}</span>
      <span class="meter"><span style="width:${Math.min(100, percent)}%;background:${color}"></span></span>
      <b>${percent.toFixed(1)}%</b>
    </div>`;

  view.innerHTML = `
    <div class="cards">
      <div class="card"><h3>Database</h3>
        <div class="kpi-value" style="font-size:20px">${badge(health.database, health.database === "ok" ? "ok" : "err")}</div>
        <div class="kpi-foot">${esc(ready?.checks?.database || "")}</div></div>
      <div class="card"><h3>Redis</h3>
        <div class="kpi-value" style="font-size:20px">${badge(health.redis, health.redis === "ok" || health.redis === "disabled" ? "ok" : "err")}</div>
        <div class="kpi-foot">${esc(ready?.checks?.redis || "")}</div></div>
      <div class="card"><h3>API</h3>
        <div class="kpi-value" style="font-size:20px">${badge(health.api, "ok")}</div>
        <div class="kpi-foot">version ${esc(health.version)}</div></div>
      <div class="card"><h3>Live sockets</h3>
        <div class="kpi-value">${fmtNumber(health.websocket_connections)}</div>
        <div class="kpi-foot">connected clients</div></div>
    </div>

    <div class="grid-2">
      <div class="panel">
        <div class="panel-head"><h3>Captured server errors</h3><div class="spacer"></div>
          <button class="btn ghost sm" id="reload">Reload</button></div>
        <div class="table-wrap" style="max-height:420px">
          <table class="data">
            <thead><tr><th>Level</th><th>Message</th><th>Route</th><th>When</th></tr></thead>
            <tbody>
              ${errors.length ? errors.map((error) => `<tr>
                <td>${badge(error.level, "err")}</td>
                <td class="small">${esc(error.message)}</td>
                <td class="mono small">${esc(error.method || "")} ${esc(error.path || "")}</td>
                <td class="small muted">${esc(fmtDate(error.created_at))}</td></tr>`).join("")
                : `<tr><td colspan="4">${empty("No errors recorded — good news")}</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><h3>Host resources</h3></div>
        <div class="panel-body">
          ${meterRow("CPU", health.cpu_percent, "#2aabee")}
          ${meterRow("Memory", health.memory_percent, "#f79009")}
          ${meterRow("Disk", health.disk_percent, "#7a5af8")}
          <div class="health-row"><span class="muted">Memory used</span><b>${fmtBytes(health.memory_used_bytes)}</b></div>
          <div class="health-row"><span class="muted">Process uptime</span><b>${Math.floor(health.uptime_seconds / 60)} min</b></div>
        </div>
      </div>
    </div>`;

  $("#reload").addEventListener("click", route);
}

/* ------------------------------------------------------------------- boot */
async function boot() {
  const envChip = $("#env-chip");
  fetch(`${API}/healthz`).then((r) => r.json()).then((data) => {
    if (envChip) envChip.textContent = `v${data.version}`;
  }).catch(() => {});

  if (!store.access) { renderLogin(); return; }
  try {
    const me = await api.get("/admin/me");
    if (!me.role) throw new Error("no role");
    state.me = me;
    renderShell();
  } catch (_) {
    store.clear();
    renderLogin();
  }
}

boot();
