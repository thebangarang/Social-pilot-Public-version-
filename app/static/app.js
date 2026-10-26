/* Social Pilot — shell logic (vanilla JS, no build step). */
"use strict";

const state = { me: null, themes: [], users: [], brands: [] };

/* ---------- fetch helper ---------- */

async function api(path, opts = {}) {
  const { timeoutMs, headers, ...rest } = opts;
  const controller = new AbortController();
  const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const res = await fetch(path, {
      headers: rest.body instanceof FormData
        ? (headers || {})
        : { "Content-Type": "application/json", ...(headers || {}) },
      ...rest,
      signal: controller.signal,
    });
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.detail || res.statusText || t("error.generic"));
      err.status = res.status;
      throw err;
    }
    return data;
  } catch (e) {
    if (e.name === "AbortError") {
      const err = new Error(t("error.timeout"));
      err.timeout = true;
      throw err;
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/* Surface any uncaught JS error / unhandled rejection so failures are never silent. */
function surfaceError(msg) {
  const el = document.getElementById("chat-messages");
  if (el) {
    el.insertAdjacentHTML("beforeend", `<div class="error-msg">${t("error.unexpected", { msg: esc(msg) })}</div>`);
    el.scrollTop = el.scrollHeight;
  }
}
window.addEventListener("error", (e) => surfaceError(e.message || t("error.js")));
window.addEventListener("unhandledrejection", (e) => {
  const r = e.reason;
  surfaceError((r && (r.message || String(r))) || t("error.promise"));
});

/* ---------- lightbox (full-size media preview) ---------- */
function openLightbox(src) {
  document.getElementById("lightbox-img").src = src;
  document.getElementById("lightbox").hidden = false;
}
function closeLightbox() { document.getElementById("lightbox").hidden = true; }

async function copyUrl(text, btn) {
  const done = () => {
    if (btn) { const old = btn.textContent; btn.textContent = t("common.copied"); setTimeout(() => { btn.textContent = old; }, 1500); }
  };
  try {
    await navigator.clipboard.writeText(text);
    done();
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (e2) { showAlert(t("common.copy_failed")); }
    ta.remove();
  }
}

/* ---------- styled modals (replace native confirm / prompt / alert) ---------- */
let confirmCallback = null;
function askConfirm(title, text, onOk) {
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-text").textContent = text;
  document.getElementById("confirm-cancel").style.display = "";
  confirmCallback = onOk;
  document.getElementById("confirm-modal").hidden = false;
}
function showAlert(text, title = "Social Pilot") {
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-text").textContent = text;
  document.getElementById("confirm-cancel").style.display = "none";
  confirmCallback = null;  // OK just closes
  document.getElementById("confirm-modal").hidden = false;
}
function closeConfirmModal() { document.getElementById("confirm-modal").hidden = true; }
function okConfirm() {
  closeConfirmModal();
  const cb = confirmCallback; confirmCallback = null;
  if (cb) cb();
}

let promptCallback = null;
function askPrompt(title, placeholder, onOk, type = "text") {
  document.getElementById("prompt-title").textContent = title;
  const inp = document.getElementById("prompt-input");
  inp.type = type;
  inp.placeholder = placeholder || "";
  inp.value = "";
  promptCallback = onOk;
  document.getElementById("prompt-modal").hidden = false;
  inp.focus();
}
function closePromptModal() { document.getElementById("prompt-modal").hidden = true; }
function okPrompt() {
  const v = document.getElementById("prompt-input").value;
  closePromptModal();
  const cb = promptCallback; promptCallback = null;
  if (cb) cb(v);
}

/* ---------- theme ---------- */

function applyTheme(t) {
  const r = document.documentElement.style;
  r.setProperty("--sp-bg", t.bg);
  r.setProperty("--sp-surface", t.surface);
  r.setProperty("--sp-border", t.border);
  r.setProperty("--sp-fg", t.fg);
  r.setProperty("--sp-muted", t.muted);
  r.setProperty("--sp-accent", t.accent);
  r.setProperty("--sp-accent-2", t.accent_2);
  r.setProperty("--sp-success", t.success);
  r.setProperty("--sp-warning", t.warning);
  r.setProperty("--sp-danger", t.danger);
}

/* ---------- tabs ---------- */

function initTabs() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((i) => i.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      item.classList.add("active");
      document.getElementById("tab-" + item.dataset.tab).classList.add("active");
      if (item.dataset.tab === "performance") loadPerformance();
      if (item.dataset.tab === "community") loadCommunity();
      if (item.dataset.tab === "calendar") loadCalendar();
    });
  });
}

/* ---------- account (password + 2FA) ---------- */

function render2FA() {
  const box = document.getElementById("2fa-box");
  const enabled = state.me.totp_enabled;
  box.innerHTML = `
    <div class="card-title">${t("settings.security")}</div>
    <div class="status-pill ${enabled ? "ok" : "off"}">
      <span class="led"></span> ${t("settings.2fa_label", { state: enabled ? t("settings.2fa_on") : t("settings.2fa_off") })}
    </div>
    <div class="spacer"></div>
    <div id="2fa-control">${enabled
      ? `<button class="btn-sm btn-danger" id="2fa-disable-btn">${t("settings.2fa_disable_btn")}</button>`
      : `<button class="btn-sm" id="2fa-start-btn">${t("settings.2fa_enable_btn")}</button>`}
    </div>
    <div class="muted" id="2fa-msg" style="margin-top:10px"></div>
  `;

  if (enabled) {
    document.getElementById("2fa-disable-btn").addEventListener("click", () => {
      askPrompt(t("settings.2fa_disable_title"), t("settings.2fa_password_prompt"), async (pw) => {
        if (!pw) return;
        const msg = document.getElementById("2fa-msg");
        try {
          await api("/auth/2fa/disable", { method: "POST", body: JSON.stringify({ password: pw }) });
          state.me.totp_enabled = false;
          msg.textContent = t("settings.2fa_disabled");
          render2FA();
        } catch (e) { msg.textContent = e.message; }
      }, "password");
    });
  } else {
    document.getElementById("2fa-start-btn").addEventListener("click", async () => {
      const msg = document.getElementById("2fa-msg");
      try {
        const s = await api("/auth/2fa/setup");
        document.getElementById("2fa-control").innerHTML = `
          <div class="qr-box"><img src="${s.qr_data_uri}" alt="QR code"></div>
          <div class="secret">${esc(s.secret)}</div>
          <div class="spacer"></div>
          <div class="inline-form">
            <input id="2fa-code" inputmode="numeric" maxlength="8" placeholder="${t("settings.2fa_code_placeholder")}">
            <button class="btn-sm" id="2fa-enable-btn">${t("common.validate")}</button>
          </div>`;
        document.getElementById("2fa-enable-btn").addEventListener("click", async () => {
          const code = document.getElementById("2fa-code").value.trim();
          try {
            await api("/auth/2fa/enable", { method: "POST", body: JSON.stringify({ code }) });
            state.me.totp_enabled = true;
            msg.textContent = t("settings.2fa_enabled");
            render2FA();
          } catch (e) { msg.textContent = e.message; }
        });
      } catch (e) { msg.textContent = e.message; }
    });
  }
}

function initAccount() {
  document.getElementById("password-btn").addEventListener("click", async () => {
    const msg = document.getElementById("password-msg");
    msg.textContent = "";
    try {
      await api("/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: document.getElementById("cur-password").value,
          new_password: document.getElementById("new-password").value,
        }),
      });
      document.getElementById("cur-password").value = "";
      document.getElementById("new-password").value = "";
      msg.textContent = t("settings.password_changed");
    } catch (e) { msg.textContent = e.message; }
  });
  render2FA();
}

/* ---------- theme ---------- */

async function renderThemes() {
  const wrap = document.getElementById("swatches");
  const msg = document.getElementById("theme-msg");
  state.themes = await api("/theme/all");
  const isAdmin = state.me.role === "admin";
  wrap.innerHTML = state.themes.map((t) => `
    <div class="swatch ${t.is_active ? "active" : ""}" data-id="${esc(t.id)}">
      <div class="swatch-name">${esc(t.name)}${t.is_active ? " ✓" : ""}</div>
      <div class="swatch-row"><span style="background:${t.bg}"></span><span style="background:${t.surface}"></span><span style="background:${t.fg}"></span><span style="background:${t.accent}"></span></div>
    </div>`).join("");
  if (!isAdmin) msg.textContent = t("settings.theme_admin_only");
  wrap.querySelectorAll(".swatch").forEach((s) => {
    s.addEventListener("click", async () => {
      if (!isAdmin) return;
      try {
        const t = await api(`/theme/${s.dataset.id}/activate`, { method: "POST" });
        applyTheme(t);
        renderThemes();
      } catch (e) { msg.textContent = e.message; }
    });
  });
}

/* ---------- users (admin) ---------- */

function userRow(u) {
  const pubCell = u.role === "admin"
    ? "✓"
    : `${u.can_publish ? "✓" : "—"} <button class="btn-sm" data-act="canpub" data-id="${u.id}">${u.can_publish ? t("settings.user_revoke_publish") : t("settings.user_grant_publish")}</button>`;
  return `
    <tr>
      <td>${esc(u.name)}</td>
      <td class="mono">${esc(u.email)}</td>
      <td><span class="badge">${esc(u.role)}</span></td>
      <td>${pubCell}</td>
      <td>${u.totp_enabled ? "2FA ✓" : "—"}</td>
      <td>${u.is_active ? t("common.active") : `<span style='color:var(--sp-danger)'>${t("common.inactive")}</span>`}</td>
      <td>
        <button class="btn-sm" data-act="toggle" data-id="${u.id}">${u.is_active ? t("common.deactivate") : t("common.activate")}</button>
        <button class="btn-sm" data-act="reset-2fa" data-id="${u.id}">${t("settings.user_reset_2fa")}</button>
        <button class="btn-sm btn-danger" data-act="delete" data-id="${u.id}">${t("common.delete_short")}</button>
      </td>
    </tr>`;
}

async function renderUsers() {
  const table = document.getElementById("users-table");
  state.users = await api("/users");
  table.innerHTML = `
    <tr><th>${t("settings.user_name")}</th><th>${t("settings.user_email")}</th><th>${t("settings.user_role")}</th><th>${t("settings.user_publish")}</th><th>${t("settings.user_2fa")}</th><th>${t("settings.user_status")}</th><th>${t("settings.user_actions")}</th></tr>
    ${state.users.map(userRow).join("")}`;
}

async function initUsers() {
  if (state.me.role !== "admin") return;
  document.getElementById("users-card").hidden = false;
  await renderUsers();

  document.getElementById("create-user-btn").addEventListener("click", async () => {
    const msg = document.getElementById("users-msg");
    msg.textContent = "";
    try {
      await api("/users", {
        method: "POST",
        body: JSON.stringify({
          name: document.getElementById("nu-name").value.trim(),
          email: document.getElementById("nu-email").value.trim(),
          password: document.getElementById("nu-password").value,
          role: document.getElementById("nu-role").value,
          can_publish: document.getElementById("nu-can-publish").checked,
        }),
      });
      ["nu-name", "nu-email", "nu-password"].forEach((id) => (document.getElementById(id).value = ""));
      msg.textContent = t("settings.user_created");
      await renderUsers();
    } catch (e) { msg.textContent = e.message; }
  });

  document.getElementById("users-table").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const id = btn.dataset.id;
    const act = btn.dataset.act;
    const msg = document.getElementById("users-msg");
    msg.textContent = "";
    try {
      if (act === "toggle") {
        const u = state.users.find((x) => x.id === id);
        await api(`/users/${id}`, { method: "PATCH", body: JSON.stringify({ is_active: !u.is_active }) });
      } else if (act === "canpub") {
        const u = state.users.find((x) => x.id === id);
        await api(`/users/${id}`, { method: "PATCH", body: JSON.stringify({ can_publish: !u.can_publish }) });
      } else if (act === "reset-2fa") {
        await api(`/users/${id}/reset-2fa`, { method: "POST" });
      } else if (act === "delete") {
        askConfirm(t("settings.delete_user_title"), t("settings.delete_user_text"), async () => {
          try {
            await api(`/users/${id}`, { method: "DELETE" });
            await renderUsers();
          } catch (e2) { msg.textContent = e2.message; }
        });
        return;
      }
      await renderUsers();
    } catch (e) { msg.textContent = e.message; }
  });
}

/* ---------- social connections ---------- */

const PLATFORM_LABELS = { tiktok: "TikTok", instagram: "Instagram", facebook: "Facebook", linkedin: "LinkedIn", x: "X", threads: "Threads", pinterest: "Pinterest", bluesky: "Bluesky" };

function accountRow(a, isAdmin) {
  const label = PLATFORM_LABELS[a.platform] || a.platform;
  const mock = a.account_meta && a.account_meta.mock ? " (mock)" : "";
  const def = a.is_default
    ? `<span class="badge" style="border-color:var(--sp-accent);color:var(--sp-accent)">${t("common.default")}</span>` : "";
  const brandCell = isAdmin
    ? `<select class="btn-sm" data-brand-account="${a.id}">
        <option value="">${t("brands.unassigned")}</option>
        ${state.brands.map((b) => `<option value="${b.id}" ${a.brand_id === b.id ? "selected" : ""}>${esc(b.name)}</option>`).join("")}
      </select>`
    : `<span class="muted">${brandName(a.brand_id)}</span>`;
  return `<tr>
    <td><span class="badge">${esc(label)}</span></td>
    <td class="mono">${esc(a.handle)}${mock}</td>
    <td>${brandCell}</td>
    <td>${a.is_active ? t("common.active") : t("common.inactive")} ${def}</td>
    ${isAdmin ? `<td>
      ${a.is_default ? "" : `<button class="btn-sm" data-default="${a.id}">${t("common.set_default")}</button>`}
      <button class="btn-sm btn-danger" data-revoke="${a.id}">${t("settings.revoke")}</button>
    </td>` : ""}
  </tr>`;
}

function openBlueskyModal() {
  document.getElementById("bsky-handle").value = "";
  document.getElementById("bsky-password").value = "";
  document.getElementById("bsky-msg").textContent = "";
  document.getElementById("connect-modal").hidden = false;
}

async function submitBlueskyConnect() {
  const handle = document.getElementById("bsky-handle").value.trim();
  const app_password = document.getElementById("bsky-password").value;
  const msg = document.getElementById("bsky-msg");
  msg.textContent = "";
  if (!handle || !app_password) { msg.textContent = t("connect.required"); return; }
  const btn = document.getElementById("bsky-connect-btn");
  btn.disabled = true;
  try {
    await api("/connect/bluesky", { method: "POST", body: JSON.stringify({ handle, app_password }) });
    document.getElementById("connect-modal").hidden = true;
    await renderConnect();
  } catch (e) {
    msg.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function renderConnect() {
  const isAdmin = state.me.role === "admin";
  const btnWrap = document.getElementById("connect-buttons");
  const msg = document.getElementById("connect-msg");
  btnWrap.style.display = isAdmin ? "" : "none";
  document.getElementById("connect-note").style.display = isAdmin ? "" : "none";
  const accounts = await api("/connect");
  genState.accounts = accounts;
  renderGenNetPicker();
  document.getElementById("accounts-table").innerHTML =
    `<tr><th>${t("perf.platform")}</th><th>${t("connect.account")}</th><th>${t("connect.brand")}</th><th>${t("connect.status")}</th>${isAdmin ? `<th>${t("connect.action")}</th>` : ""}</tr>` +
    accounts.map((a) => accountRow(a, isAdmin)).join("");

  if (!isAdmin) return;
  btnWrap.querySelectorAll("button[data-platform]").forEach((b) => {
    b.onclick = async () => {
      msg.textContent = "";
      if (b.dataset.platform === "bluesky") { openBlueskyModal(); return; }
      if (b.dataset.platform === "tiktok") { window.location.href = "/oauth/tiktok/authorize"; return; }
      if (b.dataset.platform === "instagram") { window.location.href = "/oauth/instagram/authorize"; return; }
      if (b.dataset.platform === "threads") { window.location.href = "/oauth/threads/authorize"; return; }
      if (b.dataset.platform === "facebook") { window.location.href = "/oauth/facebook/authorize"; return; }
      if (b.dataset.platform === "linkedin") { window.location.href = "/oauth/linkedin/authorize"; return; }
      if (b.dataset.platform === "pinterest") { window.location.href = "/oauth/pinterest/authorize"; return; }
      if (b.dataset.platform === "x") { window.location.href = "/oauth/x/authorize"; return; }
      try {
        await api(`/connect/${b.dataset.platform}/mock`, { method: "POST" });
        await renderConnect();
      } catch (e) { msg.textContent = e.message; }
    };
  });
  document.getElementById("bsky-connect-btn").onclick = submitBlueskyConnect;
  document.getElementById("bsky-close").onclick = () => { document.getElementById("connect-modal").hidden = true; };
  document.getElementById("bsky-cancel").onclick = () => { document.getElementById("connect-modal").hidden = true; };
  document.getElementById("accounts-table").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-default], button[data-revoke]");
    if (!btn) return;
    msg.textContent = "";
    try {
      if (btn.dataset.default) {
        await api(`/connect/${btn.dataset.default}/default`, { method: "POST" });
      } else {
        await api(`/connect/${btn.dataset.revoke}`, { method: "DELETE" });
      }
      await renderConnect();
    } catch (err) { msg.textContent = err.message; }
  });
}

/* ---------- brands (multi-brand whitelabel) ---------- */

let activeBrandId = "";
let pendingLogoBrandId = "";

function brandLogoHtml(b) {
  return b.logo_path
    ? `<img class="brand-logo-thumb" src="/brands/${b.id}/logo" alt="">`
    : `<span class="brand-logo-empty"></span>`;
}

function renderBrandSelector() {
  if (activeBrandId && !state.brands.some((b) => b.id === activeBrandId)) {
    activeBrandId = "";
    try { localStorage.removeItem("sp-brand"); } catch (e) { /* ignore */ }
  }
  const current = state.brands.find((b) => b.id === activeBrandId);
  const logo = document.getElementById("brand-select-logo");
  if (logo) {
    if (current && current.logo_path) {
      logo.src = `/brands/${current.id}/logo`;
      logo.hidden = false;
    } else {
      logo.hidden = true;
    }
  }
  const nameEl = document.getElementById("brand-select-name");
  if (nameEl) nameEl.textContent = current ? current.name : "—";
  const menu = document.getElementById("brand-select-menu");
  if (menu) {
    menu.innerHTML = state.brands.map((b) => `
      <button type="button" class="brand-select-item ${b.id === activeBrandId ? "active" : ""}" data-brand-id="${b.id}">
        ${brandLogoHtml(b)}
        <span>${esc(b.name)}</span>
      </button>`).join("") || `<div class="muted" style="padding:6px 8px">—</div>`;
  }
}

function brandWarningText() {
  if (!state.brands.length) return t("gen.no_brand");
  return t("gen.brand_no_account");
}

function renderBrandWarning() {
  const el = document.getElementById("gen-brand-warning");
  if (!el) return;
  const blocked = !state.brands.length || publishableNets().length === 0;
  el.hidden = !blocked;
  if (blocked) el.textContent = brandWarningText();
}

async function setActiveBrand(id) {
  activeBrandId = id || "";
  try { localStorage.setItem("sp-brand", activeBrandId); } catch (err) { /* ignore */ }
  renderBrandSelector();
  await loadAccounts();
  renderBrandWarning();
  loadBranding();
  loadMedia();
  loadPresets();
  const active = document.querySelector(".nav-item.active");
  if (active && active.dataset.tab === "calendar") loadCalendar();
  if (active && active.dataset.tab === "performance") loadPerformance();
  if (active && active.dataset.tab === "community") loadCommunity();
}

function initBrandSelector() {
  try { activeBrandId = localStorage.getItem("sp-brand") || ""; } catch (e) { activeBrandId = ""; }
  const btn = document.getElementById("brand-select-btn");
  const menu = document.getElementById("brand-select-menu");
  if (btn) btn.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    btn.setAttribute("aria-expanded", String(!menu.hidden));
  });
  if (menu) menu.addEventListener("click", (e) => {
    const item = e.target.closest("[data-brand-id]");
    if (!item) return;
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    setActiveBrand(item.dataset.brandId);
  });
  document.addEventListener("click", () => {
    if (menu) { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); }
  });
}

async function loadBrands() {
  state.brands = await api("/brands");
  renderBrands();
  renderBrandSelector();
  if (!activeBrandId && state.brands.length) setActiveBrand(state.brands[0].id);
  else renderBrandWarning();
}

function brandName(id) {
  const b = state.brands.find((x) => x.id === id);
  return b ? b.name : "—";
}

function renderBrands() {
  const el = document.getElementById("brands-list");
  if (!state.brands.length) {
    el.innerHTML = `<div class="muted">${t("brands.empty")}</div>`;
    return;
  }
  el.innerHTML = state.brands.map((b) => `
    <div class="brand-row">
      ${brandLogoHtml(b)}
      <span class="pill">${esc(b.name)}</span>
      <span class="spacer"></span>
      <button class="btn-mini" data-brand-logo="${b.id}" type="button" title="Logo">🖼</button>
      <button class="btn-mini" data-brand-rename="${b.id}" type="button" title="${t("brands.rename_title")}">✎</button>
      <button class="btn-mini" data-brand-del="${b.id}" type="button" title="${t("common.delete")}">×</button>
    </div>`).join("");
  el.querySelectorAll("[data-brand-logo]").forEach((btn) => btn.addEventListener("click", () => {
    pendingLogoBrandId = btn.dataset.brandLogo;
    document.getElementById("brand-logo-edit").click();
  }));
  el.querySelectorAll("[data-brand-rename]").forEach((btn) => btn.addEventListener("click", () => renameBrand(btn.dataset.brandRename)));
  el.querySelectorAll("[data-brand-del]").forEach((btn) => btn.addEventListener("click", () => deleteBrand(btn.dataset.brandDel)));
}

async function createBrand() {
  const input = document.getElementById("brand-name");
  const name = input.value.trim();
  if (!name) return;
  const logoFile = document.getElementById("brand-logo").files[0];
  const fd = new FormData();
  fd.append("name", name);
  if (logoFile) fd.append("logo", logoFile);
  try {
    await api("/brands", { method: "POST", body: fd });
    input.value = "";
    document.getElementById("brand-logo").value = "";
    document.getElementById("brand-logo-name").textContent = "";
    await loadBrands();
    await renderConnect();
  } catch (e) { showAlert(e.message); }
}

async function renameBrand(id) {
  const b = state.brands.find((x) => x.id === id);
  askPrompt(t("brands.rename_title"), b ? b.name : "", async (name) => {
    if (!name || !name.trim()) return;
    try {
      await api(`/brands/${id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
      await loadBrands();
      await renderConnect();
    } catch (e) { showAlert(e.message); }
  });
}

async function deleteBrand(id) {
  openBrandDeleteModal(id);
}

let brandDelTargetId = null;

function openBrandDeleteModal(id) {
  brandDelTargetId = id;
  const b = state.brands.find((x) => x.id === id);
  const name = b ? b.name : "";
  document.getElementById("brand-del-title").textContent = t("brands.delete_title");
  document.getElementById("brand-del-body").textContent = t("brands.del_body", { name });
  document.getElementById("brand-del-scope").textContent = t("brands.del_scope");
  document.getElementById("brand-del-confirm-wrap").hidden = true;
  const input = document.getElementById("brand-del-confirm-input");
  input.value = "";
  input.placeholder = name;
  document.getElementById("brand-del-confirm-hint").textContent = t("brands.purge_hint", { name });
  document.getElementById("brand-del-confirm-btn").disabled = true;
  document.getElementById("brand-del-modal").hidden = false;
  input.focus();
}

function closeBrandDeleteModal() {
  document.getElementById("brand-del-modal").hidden = true;
  brandDelTargetId = null;
}

async function softDeleteBrand() {
  const id = brandDelTargetId;
  if (!id) return;
  closeBrandDeleteModal();
  try {
    await api(`/brands/${id}`, { method: "DELETE" });
    await loadBrands();
    await renderConnect();
  } catch (e) { showAlert(e.message); }
}

async function purgeBrand() {
  const id = brandDelTargetId;
  if (!id) return;
  const name = (state.brands.find((x) => x.id === id) || {}).name || "";
  const typed = document.getElementById("brand-del-confirm-input").value.trim();
  if (typed !== name) { showAlert(t("brands.purge_mismatch")); return; }
  closeBrandDeleteModal();
  try {
    await api(`/brands/${id}/purge?confirm=${encodeURIComponent(typed)}`, { method: "DELETE" });
    await loadBrands();
    await renderConnect();
  } catch (e) { showAlert(e.message); }
}

/* ---------- LLM settings (dynamic per provider) ---------- */

const PROVIDER_DEFAULTS = {
  openrouter: "https://openrouter.ai/api/v1",
  openai_compatible: "",
  ollama: "http://localhost:11434",
  none: "",
};

let llmState = { provider: "openrouter", base_url: "", model: "", image_model: "", video_model: "", monthly_limit: null, topup_mode: "manual", has_api_key: false };

function fieldHtml(id, label, type, value, placeholder) {
  return `<label for="${id}">${label}</label>
    <input id="${id}" type="${type || "text"}" value="${esc(value || "")}" placeholder="${esc(placeholder || "")}" autocomplete="off">`;
}

function autoFieldHtml(id, label, value, placeholder) {
  return `<label for="${id}">${label}</label>
    <div class="ac">
      <input id="${id}" type="text" value="${esc(value || "")}" placeholder="${esc(placeholder || "")}" autocomplete="off">
      <div class="ac-list" id="${id}-list" hidden></div>
    </div>`;
}

async function fetchModels(modality) {
  const baseUrlEl = document.getElementById("llm-base-url");
  const keyEl = document.getElementById("llm-api-key");
  const q = modality ? `?modality=${encodeURIComponent(modality)}` : "";
  const res = await api(`/settings/llm/models${q}`, {
    method: "POST",
    body: JSON.stringify({
      provider: llmState.provider,
      base_url: baseUrlEl ? baseUrlEl.value.trim() : "",
      api_key: keyEl && keyEl.value.trim() ? keyEl.value.trim() : null,
    }),
  });
  return res.models;
}

function attachAutocomplete(inputId, modality) {
  const input = document.getElementById(inputId);
  const list = document.getElementById(inputId + "-list");
  if (!input || !list) return;
  let models = [];
  let loading = false;

  async function load() {
    // Always re-fetch: model listing is free (no credits) and we want the
    // latest models the moment they land on the provider.
    loading = true;
    try {
      models = await fetchModels(modality);
    } catch (e) { /* keep previous models on error */ }
    finally { loading = false; }
    render(input.value);
  }

  function render(q) {
    q = (q || "").trim().toLowerCase();
    const items = models
      .filter((m) => !q || m.id.toLowerCase().includes(q) || (m.name && m.name.toLowerCase().includes(q)))
      .slice(0, 60);
    let html = `<div class="ac-item" data-id="auto">${t("settings.llm_auto_hint")}</div>`;
    html += items.map((m) => {
      const extra = m.name && m.name !== m.id ? ` <span class="muted">(${esc(m.name)})</span>` : "";
      return `<div class="ac-item" data-id="${esc(m.id)}">${esc(m.id)}${extra}</div>`;
    }).join("");
    list.innerHTML = html;
    list.hidden = false;
  }

  input.addEventListener("focus", load);
  input.addEventListener("input", () => render(input.value));
  input.addEventListener("blur", () => { setTimeout(() => { list.hidden = true; }, 150); });
  list.addEventListener("mousedown", (e) => {
    e.preventDefault(); // keep focus in the input (no blur)
    const item = e.target.closest(".ac-item");
    if (item) { input.value = item.dataset.id; list.hidden = true; }
  });
}

function renderLLMFields(isAdmin) {
  const wrap = document.getElementById("llm-fields");
  const p = llmState.provider;
  let html = "";
  if (p === "openrouter") {
    html += `<div class="muted" style="margin-bottom:12px">${t("settings.llm_base_url_fixed")}</div>`;
    html += fieldHtml("llm-api-key", t("settings.llm_api_key"), "password", "", "sk-or-...");
    html += autoFieldHtml("llm-model", t("settings.llm_model_text"), llmState.model, "anthropic/claude-sonnet-4");
    html += autoFieldHtml("llm-image-model", t("settings.llm_model_image"), llmState.image_model, "google/gemini-2.5-flash-image");
    html += autoFieldHtml("llm-video-model", t("settings.llm_model_video"), llmState.video_model, "google/veo-3.1");
    html += fieldHtml("llm-limit", t("settings.llm_monthly_limit"), "number", llmState.monthly_limit ?? "", "50");
    html += `<label for="llm-topup">${t("settings.llm_topup")}</label><select id="llm-topup"><option value="manual">${t("settings.llm_topup_manual")}</option><option value="auto">${t("settings.llm_topup_auto")}</option></select>`;
  } else if (p === "openai_compatible") {
    html += fieldHtml("llm-base-url", t("settings.llm_base_url"), "text", llmState.base_url, "https://api.votre-fournisseur.com/v1");
    html += fieldHtml("llm-api-key", t("settings.llm_api_key"), "password", "", "sk-...");
    html += autoFieldHtml("llm-model", t("settings.llm_model"), llmState.model, "gpt-4o");
  } else if (p === "ollama") {
    html += fieldHtml("llm-base-url", t("settings.llm_ollama_url"), "text", llmState.base_url, PROVIDER_DEFAULTS.ollama);
    html += autoFieldHtml("llm-model", t("settings.llm_ollama_model"), llmState.model, "llama3.2");
  } else {
    html = `<div class="muted">${t("settings.llm_none_hint")}</div>`;
  }
  wrap.innerHTML = html;
  if (p === "openrouter") {
    const sel = document.getElementById("llm-topup");
    if (sel && llmState.topup_mode) sel.value = llmState.topup_mode;
  }
  if (isAdmin) {
    const textMod = p === "openrouter" ? "text" : null;
    if (document.getElementById("llm-model")) attachAutocomplete("llm-model", textMod);
    if (document.getElementById("llm-image-model")) attachAutocomplete("llm-image-model", "image");
    if (document.getElementById("llm-video-model")) attachAutocomplete("llm-video-model", "video");
  }
}

async function renderLLM() {
  const isAdmin = state.me.role === "admin";
  const msg = document.getElementById("llm-msg");
  const llm = await api("/settings/llm");
  llmState = {
    provider: llm.provider, base_url: llm.base_url, model: llm.model,
    image_model: llm.image_model, video_model: llm.video_model,
    monthly_limit: llm.monthly_limit, topup_mode: llm.topup_mode, has_api_key: llm.has_api_key,
  };
  const sel = document.getElementById("llm-provider");
  sel.value = llm.provider;
  sel.disabled = !isAdmin;
  document.getElementById("llm-save-btn").style.display = isAdmin ? "" : "none";
  document.getElementById("llm-test-btn").style.display = isAdmin ? "" : "none";
  msg.textContent = isAdmin
    ? (llm.has_api_key ? t("settings.llm_key_set") : t("settings.llm_key_missing"))
    : t("settings.llm_admin_only");
  renderLLMFields(isAdmin);
  if (!isAdmin) return;

  sel.onchange = () => {
    llmState.provider = sel.value;
    llmState.base_url = PROVIDER_DEFAULTS[sel.value];
    renderLLMFields(true);
  };

  document.getElementById("llm-save-btn").onclick = async () => {
    msg.textContent = "";
    const body = { provider: llmState.provider, topup_mode: "manual" };
    const baseUrlEl = document.getElementById("llm-base-url");
    if (llmState.provider === "openai_compatible" && (!baseUrlEl || !baseUrlEl.value.trim())) {
      msg.textContent = t("settings.llm_base_url_required");
      return;
    }
    if (baseUrlEl) body.base_url = baseUrlEl.value.trim();
    const keyEl = document.getElementById("llm-api-key");
    if (keyEl && keyEl.value.trim()) body.api_key = keyEl.value.trim();
    const modelEl = document.getElementById("llm-model");
    if (modelEl) body.model = modelEl.value.trim();
    if (llmState.provider === "openrouter") {
      body.image_model = document.getElementById("llm-image-model").value.trim();
      body.video_model = document.getElementById("llm-video-model").value.trim();
      body.monthly_limit = document.getElementById("llm-limit").value
        ? parseFloat(document.getElementById("llm-limit").value) : null;
      body.topup_mode = document.getElementById("llm-topup").value;
    }
    try {
      await api("/settings/llm", { method: "PUT", body: JSON.stringify(body) });
      if (keyEl) keyEl.value = "";
      msg.textContent = t("settings.llm_saved");
      await renderUsage();
    } catch (e) { msg.textContent = e.message; }
  };

  document.getElementById("llm-test-btn").onclick = async () => {
    msg.textContent = t("settings.llm_testing");
    const baseUrlEl = document.getElementById("llm-base-url");
    const keyEl = document.getElementById("llm-api-key");
    try {
      const res = await api("/settings/llm/test", {
        method: "POST",
        body: JSON.stringify({
          provider: llmState.provider,
          base_url: baseUrlEl ? baseUrlEl.value.trim() : "",
          api_key: keyEl && keyEl.value.trim() ? keyEl.value.trim() : null,
        }),
      });
      msg.textContent = (res.ok ? "✓ " : "✗ ") + res.detail;
    } catch (e) { msg.textContent = e.message; }
  };
}

/* ---------- usage / budget (adaptive per provider) ---------- */

async function renderUsage() {
  const s = await api("/usage/summary");
  const el = document.getElementById("usage-summary");
  const table = document.getElementById("usage-table");

  if (s.provider === "ollama") {
    el.innerHTML = `<div class="muted">${t("usage.ollama")}</div>`;
    table.innerHTML = "";
    return;
  }
  if (s.provider === "openai_compatible") {
    el.innerHTML = `<div class="muted">${t("usage.private")}</div>`;
    table.innerHTML = "";
    return;
  }
  if (s.provider === "none") {
    el.innerHTML = `<div class="muted">${t("usage.none")}</div>`;
    table.innerHTML = "";
    return;
  }

  const limit = s.monthly_limit;
  const pct = limit ? Math.min(100, (s.total_cost / limit) * 100) : 0;
  const remaining = s.remaining != null ? `$${s.remaining.toFixed(2)}` : t("common.unlimited");
  const limitStr = limit
    ? `$${limit.toFixed(2)}${t("usage.per_month")} (${s.topup_mode === "auto" ? t("usage.topup_auto") : t("usage.topup_manual")})`
    : t("usage.no_limit");
  el.innerHTML = `
    <div style="font-size:14px;margin-bottom:6px">
      <strong>${t("usage.monthly")}</strong> <span class="muted mono">(${esc(s.month)})</span> — ${t("usage.total")} <strong>$${s.total_cost.toFixed(4)}</strong>
      <span class="muted">(LLM $${s.llm_cost.toFixed(4)} · ${t("usage.media")} $${s.media_cost.toFixed(4)})</span>
    </div>
    <div class="muted" style="margin-bottom:6px">${t("usage.all_time")} <strong>$${s.total_all_time.toFixed(4)}</strong></div>
    <div class="inline-form" style="margin-top:12px">
      <input type="month" id="usage-export-month" value="${esc(s.month)}">
      <button class="btn-sm" id="usage-export-btn" type="button">${t("usage.export_month")}</button>
      <button class="btn-sm" id="usage-export-all-btn" type="button">${t("usage.export_all")}</button>
    </div>
    <div class="muted">${t("usage.budget", { limit: limitStr, remaining })}</div>
    ${limit ? `<div style="height:6px;background:var(--sp-bg);border:1px solid var(--sp-border);border-radius:4px;margin-top:8px"><div style="height:100%;width:${pct}%;background:linear-gradient(90deg,var(--sp-accent),var(--sp-accent-2));border-radius:4px"></div></div>` : ""}`;

  const records = await api("/usage?limit=10");
  table.innerHTML =
    `<tr><th>${t("usage.date")}</th><th>${t("usage.type")}</th><th>${t("usage.model")}</th><th>${t("usage.cost")}</th></tr>` +
    records.map((r) => `<tr>
      <td class="mono">${esc((r.created_at || "").slice(0, 16))}</td>
      <td><span class="badge">${esc(r.kind)}</span></td>
      <td class="mono">${esc(r.model || r.provider)}</td>
      <td>$${r.cost.toFixed(6)}</td></tr>`).join("");

  document.getElementById("usage-export-btn").addEventListener("click", () => {
    const m = document.getElementById("usage-export-month").value;
    if (m) window.location.href = `/usage/export?month=${m}`;
    else showAlert(t("usage.choose_month"));
  });
  document.getElementById("usage-export-all-btn").addEventListener("click", () => {
    window.location.href = "/usage/export";
  });
}

/* ---------- performance ---------- */

let perfPlatform = "all";
let perfChart = null;
let perfDetailChart = null;

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const perfFmt = (n) => (n == null ? "—" : n.toLocaleString("fr-FR"));
const perfPct = (r) => (r == null ? "—" : `${(r * 100).toFixed(2)}%`);

async function loadPerformance(platform = perfPlatform) {
  perfPlatform = platform;
  const params = [];
  if (platform && platform !== "all") params.push(`platform=${platform}`);
  if (activeBrandId) params.push(`brand_id=${activeBrandId}`);
  const q = params.length ? `?${params.join("&")}` : "";
  const ov = await api(`/performance/overview${q}`);
  const totals = ov.totals || {};
  document.querySelectorAll("[data-pf]").forEach((b) => b.classList.toggle("active", b.dataset.pf === platform));

  document.getElementById("perf-kpis").innerHTML = `
    <div class="kpi"><div class="kpi-val">${perfFmt(totals.impressions)}</div><div class="kpi-label">${t("perf.impressions")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(totals.reach)}</div><div class="kpi-label">${t("perf.reach")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(totals.views)}</div><div class="kpi-label">${t("perf.views")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(totals.likes)}</div><div class="kpi-label">${t("perf.likes")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(totals.comments)}</div><div class="kpi-label">${t("perf.comments")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfPct(ov.engagement_rate)}</div><div class="kpi-label">${t("perf.engagement")}</div></div>
  `;

  const table = document.getElementById("perf-table");
  const empty = document.getElementById("perf-empty");
  if (!ov.posts.length) {
    empty.hidden = false;
    table.innerHTML = "";
  } else {
    empty.hidden = true;
    table.innerHTML =
      `<tr><th>${t("perf.platform")}</th><th>${t("perf.publication")}</th><th>${t("perf.views")}</th><th>${t("perf.likes")}</th><th>${t("perf.comm_short")}</th><th>${t("perf.eng_short")}</th><th>${t("perf.link")}</th></tr>` +
      ov.posts.map((p) => {
        const m = p.metrics || {};
        const copyBtn = p.remote_url
          ? `<button class="btn-mini" data-copy="${esc(p.remote_url)}" type="button" title="${t("common.copy_url")}">${t("common.copy")}</button>`
          : "";
        const delBtn = `<button class="btn-mini btn-danger" data-del="${p.publication_id}" type="button" title="${t("common.delete")}">${t("common.delete_short")}</button>`;
        return `<tr>
          <td><span class="pill">${esc(p.platform)}</span></td>
          <td class="trunc" data-detail="${p.publication_id}" title="${t("pp.view_detail")}">${esc(p.caption)}</td>
          <td>${perfFmt(m.views)}</td>
          <td>${perfFmt(m.likes)}</td>
          <td>${perfFmt(m.comments)}</td>
          <td>${perfPct(p.engagement_rate)}</td>
          <td>${copyBtn} ${delBtn}</td>
        </tr>`;
      }).join("");

    table.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); copyUrl(b.dataset.copy, b); }));
    table.querySelectorAll("[data-detail]").forEach((b) => b.addEventListener("click", () => openPostDetail(b.dataset.detail)));
    table.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", (e) => {
      e.stopPropagation();
      const pub = ov.posts.find((x) => x.publication_id === b.dataset.del);
      if (pub) openPubDeleteModal({ publication_id: pub.publication_id, post_id: pub.post_id, platform: pub.platform, caption: pub.caption, status: "published" });
    }));
  }

  renderPerfChart(ov.trend || []);

  const ins = await api(`/performance/insights${q}`);
  renderInsights(ins);

  const bt = await api(`/performance/best-time${q}`);
  renderBestTime(bt);
}

function renderPerfChart(trend) {
  const canvas = document.getElementById("perf-chart");
  if (!window.Chart) return;
  if (perfChart) { perfChart.destroy(); perfChart = null; }
  const grid = "rgba(128,128,128,0.12)";
  perfChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: trend.map((x) => x.t),
      datasets: [
        { label: t("perf.views"), data: trend.map((x) => x.views), borderColor: cssVar("--sp-accent"), backgroundColor: "transparent", tension: 0.3, pointRadius: 0 },
        { label: t("perf.likes"), data: trend.map((x) => x.likes), borderColor: cssVar("--sp-accent-2"), backgroundColor: "transparent", tension: 0.3, pointRadius: 0 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: cssVar("--sp-muted") } } },
      scales: {
        x: { ticks: { color: cssVar("--sp-muted"), maxTicksLimit: 8 }, grid: { color: grid } },
        y: { ticks: { color: cssVar("--sp-muted") }, grid: { color: grid } },
      },
    },
  });
}

const FORMAT_LABELS = { image: "gen.media_image", video: "gen.media_video", text: "perf.format_text", auto: "perf.format_auto" };

const BT_DAYS = ["cal.mon", "cal.tue", "cal.wed", "cal.thu", "cal.fri", "cal.sat", "cal.sun"];

function btClass(c) {
  if (!c || !c.count || c.score == null) return "bt-empty";
  if (c.score >= 1.1) return "bt-good";
  if (c.score <= 0.9) return "bt-bad";
  return "bt-neutral";
}

function renderBestTime(bt) {
  const el = document.getElementById("perf-best-time");
  const grid = bt.grid || [];
  const map = {};
  grid.forEach((c) => { (map[c.day] ||= {})[c.hour] = c; });

  const slots = (bt.top_slots || []).map((s) => `${t(BT_DAYS[s.day])} ${String(s.hour).padStart(2, "0")}h`).join(" · ");
  let html = `
    <div class="muted" style="margin-bottom:8px">${t("perf.best_slots")} <strong>${slots || t("perf.no_slots")}</strong></div>
    <div class="bt-legend">
      <span><i class="bt-dot bt-good"></i>${t("perf.above_median")}</span>
      <span><i class="bt-dot bt-neutral"></i>${t("perf.average")}</span>
      <span><i class="bt-dot bt-bad"></i>${t("perf.below")}</span>
      <span><i class="bt-dot bt-empty"></i>${t("perf.untested")}</span>
    </div>
    <div class="bt-grid"><div class="bt-hlabel"></div>`;
  for (let h = 0; h < 24; h++) html += `<div class="bt-hlabel">${h}h</div>`;
  for (let d = 0; d < 7; d++) {
    html += `<div class="bt-dlabel">${t(BT_DAYS[d])}</div>`;
    for (let h = 0; h < 24; h++) {
      const c = (map[d] || {})[h];
      const title = c && c.count ? `${t(BT_DAYS[d])} ${h}h — ${t("perf.posts_count", { n: c.count })}, ${(c.avg_rate * 100).toFixed(2)}%` : `${t(BT_DAYS[d])} ${h}h — ${t("perf.untested")}`;
      html += `<div class="bt-cell ${btClass(c)}" title="${title}"></div>`;
    }
  }
  html += `</div>`;
  el.innerHTML = html;
}

function renderInsights(ins) {
  const el = document.getElementById("perf-insights");
  const lb = (ins.leaderboard || []).map((p, i) =>
    `<div class="insight-row"><span class="pill">#${i + 1}</span> <span class="trunc">${esc(p.caption)}</span> <span class="muted mono">${perfPct(p.engagement_rate)}</span></div>`).join("")
    || `<div class="muted">${t("perf.no_data")}</div>`;
  const fmts = (ins.formats || []).map((f) =>
    `<div class="insight-row"><span class="pill">${esc(t(FORMAT_LABELS[f.format] || f.format))}</span> <span class="muted">${t("perf.posts_count", { n: f.count })}</span> <span class="mono">${perfPct(f.avg_engagement_rate)}</span></div>`).join("")
    || `<div class="muted">—</div>`;
  const an = (ins.anomalies || []).map((a) => {
    const c = a.kind === "high" ? "var(--sp-success)" : "var(--sp-danger)";
    return `<div class="insight-row"><span class="pill" style="color:${c};border-color:color-mix(in srgb, ${c} 45%, transparent)">${a.kind === "high" ? t("perf.viral") : t("perf.flop")}</span> <span class="trunc">${esc(a.caption)}</span> <span class="muted mono">${perfPct(a.engagement_rate)}</span></div>`;
  }).join("") || `<div class="muted">${t("perf.no_anomalies")}</div>`;

  el.innerHTML = `
    <div class="insight-block"><div class="panel-subtitle">${t("perf.top_posts")}</div>${lb}</div>
    <div class="insight-block"><div class="panel-subtitle">${t("perf.winning_format")}</div>${fmts}</div>
    <div class="insight-block"><div class="panel-subtitle">${t("perf.anomalies")}</div>${an}</div>
  `;
}

async function openPostDetail(id) {
  const d = await api(`/performance/posts/${id}`);
  document.getElementById("pd-title").textContent = t("pp.detail_title", { platform: d.platform });
  document.getElementById("pd-meta").textContent = d.caption || "";
  const m = d.metrics || {};
  document.getElementById("pd-kpis").innerHTML = `
    <div class="kpi"><div class="kpi-val">${perfFmt(m.impressions)}</div><div class="kpi-label">${t("perf.impressions")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(m.reach)}</div><div class="kpi-label">${t("perf.reach")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(m.views)}</div><div class="kpi-label">${t("perf.views")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(m.likes)}</div><div class="kpi-label">${t("perf.likes")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(m.comments)}</div><div class="kpi-label">${t("perf.comments")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(m.shares)}</div><div class="kpi-label">${t("perf.shares")}</div></div>
    <div class="kpi"><div class="kpi-val">${perfFmt(m.saves)}</div><div class="kpi-label">${t("perf.saves")}</div></div>
  `;
  renderPerfDetailChart(d.series || []);
  document.getElementById("post-detail-modal").hidden = false;
}

function closePostDetail() {
  document.getElementById("post-detail-modal").hidden = true;
}

function renderPerfDetailChart(series) {
  const canvas = document.getElementById("pd-chart");
  if (!window.Chart) return;
  if (perfDetailChart) { perfDetailChart.destroy(); perfDetailChart = null; }
  const grid = "rgba(128,128,128,0.12)";
  perfDetailChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: series.map((x) => x.t),
      datasets: [
        { label: t("perf.views"), data: series.map((x) => x.views || 0), borderColor: cssVar("--sp-accent"), backgroundColor: "transparent", tension: 0.3, pointRadius: 0 },
        { label: t("perf.likes"), data: series.map((x) => x.likes || 0), borderColor: cssVar("--sp-accent-2"), backgroundColor: "transparent", tension: 0.3, pointRadius: 0 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: cssVar("--sp-muted") } } },
      scales: {
        x: { ticks: { color: cssVar("--sp-muted"), maxTicksLimit: 8 }, grid: { color: grid } },
        y: { ticks: { color: cssVar("--sp-muted") }, grid: { color: grid } },
      },
    },
  });
}

/* ---------- community manager ---------- */
let cmLevel = "all";
const cmSelection = new Set();

const CM_LEVELS = { green: "cm.green", orange: "cm.orange", red: "cm.red" };

async function loadCommunity(level = cmLevel) {
  cmLevel = level;
  document.querySelectorAll("[data-cf]").forEach((b) => b.classList.toggle("active", b.dataset.cf === level));
  const params = [];
  if (level && level !== "all") params.push(`level=${level}`);
  if (activeBrandId) params.push(`brand_id=${activeBrandId}`);
  const q = params.length ? `?${params.join("&")}` : "";
  const comments = await api(`/community/comments${q}`);
  renderComments(comments);
}

function updateBatchBar() {
  const bar = document.getElementById("cm-batch-bar");
  const n = cmSelection.size;
  bar.hidden = n === 0;
  document.getElementById("cm-sel-count").textContent = t("cm.selected", { n });
}

function renderComments(comments) {
  const el = document.getElementById("cm-list");
  if (!comments.length) {
    el.innerHTML = `<div class="muted">${t("cm.empty")}</div>`;
    updateBatchBar();
    return;
  }
  el.innerHTML = comments.map((c) => {
    const replied = c.status === "replied";
    const checked = cmSelection.has(c.id) ? "checked" : "";
    const checkCell = replied ? "" : `<input type="checkbox" class="cm-check" data-cm-check="${c.id}" ${checked}>`;
    let action;
    if (replied) {
      action = `<div class="muted"><b>${t("cm.response")}</b> : ${esc(c.reply_draft || "")}</div>`;
    } else if (c.reply_draft) {
      action = `<textarea class="cm-reply" data-cm-text="${c.id}" rows="2">${esc(c.reply_draft)}</textarea>
                <button class="btn-accent" data-cm-send="${c.id}" type="button">${t("common.send")}</button>`;
    } else {
      action = `<button class="btn-sm" data-cm-reply="${c.id}" type="button">${t("cm.reply")}</button>`;
    }
    return `<div class="cm-card cm-${c.level}">
      <div class="cm-head">
        ${checkCell}
        <span class="badge badge-cm-${c.level}">${t(CM_LEVELS[c.level] || c.level)}</span>
        <b>${esc(c.author_name)}</b>
        <span class="pill">${esc(c.platform)}</span>
        ${replied ? `<span class="pill" style="color:var(--sp-success)">${t("cm.replied")}</span>` : ""}
      </div>
      <div class="cm-text">${esc(c.text)}</div>
      <div class="cm-actions" data-cm-actions="${c.id}">${action}</div>
    </div>`;
  }).join("");

  el.querySelectorAll("[data-cm-check]").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) cmSelection.add(cb.dataset.cmCheck); else cmSelection.delete(cb.dataset.cmCheck);
    updateBatchBar();
  }));
  el.querySelectorAll("[data-cm-reply]").forEach((b) => b.addEventListener("click", () => cmGenerateReply(b.dataset.cmReply)));
  el.querySelectorAll("[data-cm-send]").forEach((b) => b.addEventListener("click", () => cmSendReply(b.dataset.cmSend)));
  updateBatchBar();
}

async function cmGenerateReply(id) {
  const actions = document.querySelector(`[data-cm-actions="${id}"]`);
  actions.innerHTML = `<div class="loading"><span class="spinner"></span>${t("gen.generating")}</div>`;
  try {
    await api(`/community/comments/${id}/generate-reply`, { method: "POST", timeoutMs: 60000 });
    await loadCommunity();
  } catch (e) {
    actions.innerHTML = `<div class="error-msg">${esc(e.message)}</div>`;
  }
}

async function cmSendReply(id) {
  const ta = document.querySelector(`[data-cm-text="${id}"]`);
  const text = ta ? ta.value.trim() : "";
  try {
    await api(`/community/comments/${id}/reply`, { method: "POST", body: JSON.stringify({ text }) });
    cmSelection.delete(id);
    await loadCommunity();
  } catch (e) {
    showAlert(e.message, t("common.error"));
  }
}

async function cmBatchGenerate() {
  const ids = [...cmSelection];
  if (!ids.length) return;
  const btn = document.getElementById("cm-batch-generate");
  btn.disabled = true;
  btn.textContent = t("gen.generating");
  try {
    await api(`/community/batch-generate`, { method: "POST", body: JSON.stringify({ comment_ids: ids }), timeoutMs: 180000 });
    await loadCommunity();
  } catch (e) {
    showAlert(e.message, t("common.error"));
  }
  btn.disabled = false;
  btn.textContent = t("cm.generate_replies");
}

async function cmBatchSend() {
  const ids = [...cmSelection];
  if (!ids.length) return;
  const items = ids.map((id) => {
    const ta = document.querySelector(`[data-cm-text="${id}"]`);
    return { comment_id: id, text: ta ? ta.value.trim() : "" };
  }).filter((it) => it.text);
  if (!items.length) {
    showAlert(t("cm.no_reply_ready"), t("common.info"));
    return;
  }
  try {
    await api(`/community/batch-reply`, { method: "POST", body: JSON.stringify({ items }) });
    cmSelection.clear();
    await loadCommunity();
  } catch (e) {
    showAlert(e.message, t("common.error"));
  }
}

/* ---------- editorial calendar ---------- */

let calView = { year: null, month: null };
let calEntries = [];

function calMonthKeys() {
  return ["cal.january", "cal.february", "cal.march", "cal.april", "cal.may", "cal.june",
    "cal.july", "cal.august", "cal.september", "cal.october", "cal.november", "cal.december"];
}

function calDayKeys() {
  return ["cal.mon", "cal.tue", "cal.wed", "cal.thu", "cal.fri", "cal.sat", "cal.sun"];
}

function calFmt(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function loadCalendar() {
  const now = new Date();
  if (calView.year == null) { calView.year = now.getFullYear(); calView.month = now.getMonth(); }
  const first = new Date(calView.year, calView.month, 1);
  const startDow = (first.getDay() + 6) % 7;  // Monday = 0
  const gridStart = new Date(calView.year, calView.month, 1 - startDow);
  const daysInMonth = new Date(calView.year, calView.month + 1, 0).getDate();
  const lastDow = (new Date(calView.year, calView.month, daysInMonth).getDay() + 6) % 7;
  const gridEnd = new Date(calView.year, calView.month, daysInMonth + (6 - lastDow));
  const brandQ = activeBrandId ? `&brand_id=${activeBrandId}` : "";

  calEntries = await api(`/calendar?start=${calFmt(gridStart)}&end=${calFmt(gridEnd)}${brandQ}`);
  renderCalendar(gridStart);
}

function renderCalendar(gridStart) {
  const months = calMonthKeys();
  document.getElementById("cal-month-title").textContent = `${t(months[calView.month])} ${calView.year}`;
  document.getElementById("cal-dow").innerHTML = calDayKeys().map((k) => `<span>${t(k)}</span>`).join("");

  const byDay = {};
  calEntries.forEach((e) => { (byDay[e.date] ||= []).push(e); });

  const today = new Date();
  const todayKey = calFmt(today);

  let cells = "";
  const start = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate());
  for (let i = 0; i < 42; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    const key = calFmt(d);
    const outside = d.getMonth() !== calView.month;
    const chips = (byDay[key] || []).map(calChip).join("");
    cells += `<div class="ec-cell ${outside ? "outside" : ""} ${key === todayKey ? "today" : ""}">
      <div class="ec-cell-num">${d.getDate()}</div>
      <div class="ec-chips">${chips}</div>
    </div>`;
  }
  document.getElementById("cal-grid").innerHTML = cells;
  document.querySelectorAll(".ec-chip").forEach((c) => c.addEventListener("click", () => openCalDetail(c.dataset.id)));
}

function calChip(e) {
  const thumb = e.media
    ? `<img class="ec-thumb" src="${e.media.kind === "image" ? `/media/${e.media.id}/file` : `/media/${e.media.id}/thumb`}" alt="">`
    : "";
  return `<div class="ec-chip ${e.status}" data-id="${e.publication_id}" title="${esc(e.caption)}">
    ${thumb}<span class="ec-time">${esc(e.time || "")}</span><span class="ec-platform">${esc(e.platform)}</span>
  </div>`;
}

function openCalDetail(id) {
  const e = calEntries.find((x) => x.publication_id === id);
  if (!e) return;
  const body = document.getElementById("cal-detail-body");
  const thumb = e.media
    ? `<img src="${e.media.kind === "image" ? `/media/${e.media.id}/file` : `/media/${e.media.id}/thumb`}" alt="" style="max-width:100%;border-radius:8px;margin-bottom:10px">`
    : "";
  const statusLabel = e.status === "scheduled" ? t("cal.scheduled") : t("cal.published");
  body.innerHTML = `
    ${thumb}
    <div class="muted mono" style="margin-bottom:10px">${esc(e.platform)} · ${statusLabel} · ${esc(e.date)} ${esc(e.time || "")}</div>
    <div style="white-space:pre-wrap;margin-bottom:12px">${esc(e.caption)}</div>
    ${e.remote_url ? `<div style="margin-bottom:12px"><a href="${esc(e.remote_url)}" target="_blank" rel="noopener">${esc(e.remote_url)}</a></div>` : ""}
    ${e.remote_url ? `<button class="btn-sm" data-copy="${esc(e.remote_url)}" type="button">${t("common.copy_url")}</button>` : ""}
    <div style="margin-top:14px"><button class="btn-danger btn-sm" id="cal-del-btn" type="button">${t("common.delete")}</button></div>`;
  document.getElementById("cal-detail-modal").hidden = false;
  const copyBtn = body.querySelector("[data-copy]");
  if (copyBtn) copyBtn.addEventListener("click", (ev) => copyUrl(ev.target.dataset.copy, ev.target));
  const delBtn = body.querySelector("#cal-del-btn");
  if (delBtn) delBtn.addEventListener("click", () => openPubDeleteModal(e));
}

function closeCalDetail() {
  document.getElementById("cal-detail-modal").hidden = true;
}

/* ---------- publication deletion (missclick-proof) ---------- */

let pubDelTarget = null;

function openPubDeleteModal(e) {
  pubDelTarget = e;
  const statusLabel = e.status === "scheduled" ? t("cal.scheduled") : t("cal.published");
  const when = e.date ? ` · ${esc(e.date)} ${esc(e.time || "")}` : "";
  document.getElementById("pub-del-body").textContent =
    `${esc(e.platform)} · ${statusLabel}${when}\n${e.caption || ""}`; // not-html: assigned to textContent — escaping here would show the operator &amp;
  const input = document.getElementById("pub-del-input");
  input.value = "";
  document.getElementById("pub-del-confirm").disabled = true;
  document.getElementById("cal-detail-modal").hidden = true;
  document.getElementById("pub-del-modal").hidden = false;
  input.focus();
}

function closePubDeleteModal() {
  document.getElementById("pub-del-modal").hidden = true;
  pubDelTarget = null;
}

async function confirmPubDelete() {
  const e = pubDelTarget;
  if (!e) return;
  const typed = document.getElementById("pub-del-input").value.trim().toUpperCase();
  if (typed !== confirmWord()) { showAlert(t("pub.delete_mismatch")); return; }
  closePubDeleteModal();
  try {
    const result = await api(`/posts/${e.post_id}/publications/${e.publication_id}?confirm=${encodeURIComponent(confirmWord())}`, { method: "DELETE" });
    await loadCalendar();
    if (document.getElementById("tab-performance").classList.contains("active")) await loadPerformance();
    if (result && result.platform_deleted === false) {
      showAlert(t("pub.delete_platform_failed") + (result.platform_error ? "\n" + result.platform_error : ""));
    }
  } catch (err) { showAlert(err.message); }
}

/* ---------- generation ---------- */

const genState = {
  sessionId: null, sessions: [], messages: [], proposals: [],
  media: [], branding: [], presets: [], pendingMedia: [],
  currentPost: null, currentPublications: [],
  netSelection: [],
  genNets: [],
  accounts: [],
  previewMode: "mobile",
};

function mediaMap() {
  const m = {};
  genState.media.forEach((x) => { m[x.id] = x; });
  return m;
}

function mediaThumbs(ids) {
  if (!ids || !ids.length) return "";
  const map = mediaMap();
  const html = ids.map((id) => {
    const m = map[id];
    if (!m) return "";
    const src = m.kind === "image" ? `/media/${id}/file` : `/media/${id}/thumb`;
    return `<img class="thumb" src="${src}" title="${esc(m.original_name)}">`;
  }).join("");
  return html ? `<div class="thumb-row">${html}</div>` : "";
}

function proposalCard(p) {
  const c = p.content || {};
  const validated = p.status === "validated";
  const fr = c.media_prompt_fr || c.media_prompt || "";
  const en = c.media_prompt || "";
  const variants = c.variants && typeof c.variants === "object" && Object.keys(c.variants).length ? c.variants : null;

  let body = "";
  if (variants) {
    const platforms = Object.keys(variants);
    const tabs = platforms.map((pl, i) =>
      `<button class="seg-btn ${i === 0 ? "active" : ""}" data-vtab="${p.id}" data-vpl="${pl}" type="button">${esc(pl)}</button>`).join("");
    const panes = platforms.map((pl, i) => {
      const v = variants[pl] || {};
      const tags = (v.hashtags || []).map((h) => `<span>${esc(h)}</span>`).join("");
      return `<div class="variant-pane" data-vpane="${p.id}" data-vpl="${pl}" ${i === 0 ? "" : "hidden"}>
        <div class="proposal-caption">${esc(v.caption || "")}</div>
        <div class="hashtags">${tags}</div>
        ${v.hook ? `<div class="prop-meta"><strong>${t("gen.hook")}</strong> : ${esc(v.hook)}</div>` : ""}
        ${v.cta ? `<div class="prop-meta"><strong>${t("gen.cta")}</strong> : ${esc(v.cta)}</div>` : ""}
      </div>`;
    }).join("");
    body = `<div class="variant-tabs">${tabs}</div>${panes}`;
  } else {
    const tags = (c.hashtags || []).map((h) => `<span>${esc(h)}</span>`).join("");
    body = `<div class="proposal-caption">${esc(c.caption || "")}</div>
      <div class="hashtags">${tags}</div>
      ${c.hook ? `<div class="prop-meta"><strong>${t("gen.hook")}</strong> : ${esc(c.hook)}</div>` : ""}
      ${c.cta ? `<div class="prop-meta"><strong>${t("gen.cta")}</strong> : ${esc(c.cta)}</div>` : ""}`;
  }

  return `<div class="proposal ${validated ? "validated" : ""}">
    <div class="proposal-head"><span class="proposal-num">${t("gen.proposal", { n: p.number })}${validated ? " · " + t("gen.validated") : ""}</span>${c.concept ? `<span class="muted" style="font-size:12px"> — ${esc(c.concept)}</span>` : ""}</div>
    ${body}
    <div class="media-prompt">
      <div class="mp-head"><strong>${t("gen.media_prompt")}</strong><button class="btn-mini" data-mp-toggle="${p.id}" type="button">FR ⇄ EN</button></div>
      <div data-mp-fr="${p.id}">${esc(fr)}</div>
      <div data-mp-en="${p.id}" hidden>${esc(en)}</div>
    </div>
    <div class="prop-actions">
      ${validated ? "" : `<button class="btn-sm" data-adjust="${p.id}">${t("gen.adjust")}</button>`}
      ${validated ? "" : `<button class="btn-accent" data-validate="${p.id}">${t("gen.validate")}</button>`}
      <button class="btn-sm btn-back" data-savepreset="${p.id}" title="${t("gen.save_preset_title")}">${t("gen.save_preset")}</button>
    </div>
  </div>`;
}

function switchVariant(id, platform) {
  document.querySelectorAll(`[data-vtab="${id}"]`).forEach((b) => b.classList.toggle("active", b.dataset.vpl === platform));
  document.querySelectorAll(`[data-vpane="${id}"]`).forEach((p) => { p.hidden = p.dataset.vpl !== platform; });
}

function toggleMediaPrompt(id) {
  const fr = document.querySelector(`[data-mp-fr="${id}"]`);
  const en = document.querySelector(`[data-mp-en="${id}"]`);
  if (!fr || !en) return;
  const showEn = en.hidden;
  fr.hidden = showEn;
  en.hidden = !showEn;
}

async function saveAsPreset(id) {
  const p = genState.proposals.find((x) => x.id === id);
  const c = (p && p.content) || {};
  askPrompt(t("gen.preset_name"), t("gen.preset_name_da"), async (name) => {
    if (!name) return;
    const directives = c.style_notes || c.media_prompt_fr || "";
    try {
      await api("/presets", { method: "POST", body: JSON.stringify({ name, directives }) });
      await loadPresets();
    } catch (e) { showAlert(e.message); }
  });
}

function renderChat() {
  const el = document.getElementById("chat-messages");
  let html = "";
  for (const m of genState.messages) {
    if (m.role === "user") {
      const brief = m.media_brief ? `<div class="muted" style="font-size:11px;margin:3px 0">🎨 ${esc(m.media_brief)}</div>` : "";
      html += `<div class="msg user"><div class="bubble">${esc(m.content)}</div>${brief}${mediaThumbs(m.media_ids)}</div>`;
    } else if (m.role === "assistant") {
      html += `<div class="msg assistant"><div class="bubble">${esc(m.content)}</div></div>`;
    }
  }
  for (const p of genState.proposals) {
    html += `<div class="msg assistant">${proposalCard(p)}</div>`;
  }
  el.innerHTML = html || `<div class="muted">${t("gen.start_hint")}</div>`;
  el.scrollTop = el.scrollHeight;
  el.querySelectorAll("[data-adjust]").forEach((b) => b.addEventListener("click", () => adjustProposal(b.dataset.adjust)));
  el.querySelectorAll("[data-validate]").forEach((b) => b.addEventListener("click", () => validateProposal(b.dataset.validate)));
  el.querySelectorAll("[data-mp-toggle]").forEach((b) => b.addEventListener("click", () => toggleMediaPrompt(b.dataset.mpToggle)));
  el.querySelectorAll("[data-vtab]").forEach((b) => b.addEventListener("click", () => switchVariant(b.dataset.vtab, b.dataset.vpl)));
  el.querySelectorAll("[data-savepreset]").forEach((b) => b.addEventListener("click", () => saveAsPreset(b.dataset.savepreset)));
}

function renderSessions() {
  const el = document.getElementById("session-list");
  el.innerHTML = genState.sessions.map((s) =>
    `<div class="session-item ${s.id === genState.sessionId ? "active" : ""}" data-id="${s.id}">${esc(s.title || t("gen.new_session_title"))}<button class="session-del" data-del="${s.id}" title="Supprimer">×</button></div>`
  ).join("") || `<div class="muted">${t("gen.no_sessions")}</div>`;
  el.querySelectorAll(".session-item").forEach((s) => s.addEventListener("click", () => selectSession(s.dataset.id)));
  el.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); deleteSession(b.dataset.del); }));
}

async function deleteSession(id) {
  askConfirm(t("gen.delete_session_title"), t("gen.delete_session_text"), async () => {
    try {
      await api(`/chat/sessions/${id}`, { method: "DELETE" });
      await loadSessions();
      if (genState.sessionId === id) {
        if (genState.sessions.length) await selectSession(genState.sessions[0].id);
        else await newSession();
      }
    } catch (e) { showAlert(e.message); }
  });
}

async function loadSessions() {
  genState.sessions = await api("/chat/sessions");
  renderSessions();
}

async function newSession() {
  const s = await api("/chat/sessions", { method: "POST" });
  await loadSessions();
  await selectSession(s.id);
}

async function selectSession(id) {
  genState.sessionId = id;
  renderSessions();
  genState.messages = await api(`/chat/sessions/${id}`);
  genState.proposals = await api(`/chat/sessions/${id}/proposals`);
  renderChat();
  await loadAccounts();
  await loadSessionPosts();
  renderBrandWarning();
}

async function loadSessionPosts() {
  try {
    const posts = await api("/posts");
    genState.currentPost = posts.find((p) => p.session_id === genState.sessionId) || null;
    if (genState.currentPost) {
      genState.currentPublications = await api(`/posts/${genState.currentPost.id}/publications`);
    } else {
      genState.currentPublications = [];
    }
  } catch (e) {
    genState.currentPost = null;
    genState.currentPublications = [];
  }
  renderPostPipeline();
}

function renderPendingMedia() {
  const el = document.getElementById("selected-media");
  el.innerHTML = genState.pendingMedia.map((m) => {
    const src = m.kind === "image" ? `/media/${m.id}/file` : `/media/${m.id}/thumb`;
    return `<div class="pending-item">
      <img src="${src}" alt="">
      <span class="pending-name">${esc(m.original_name)}</span>
      <button class="pending-x" data-remove="${m.id}" title="${t("common.remove")}">×</button>
    </div>`;
  }).join("");
  el.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
    genState.pendingMedia = genState.pendingMedia.filter((x) => x.id !== b.dataset.remove);
    renderPendingMedia();
    renderMediaList();
  }));
}

function readOnMediaText() {
  const mode = document.getElementById("media-text-mode")?.value || "auto";
  if (mode === "auto") return null;
  const text = document.getElementById("media-text-spec").value.trim();
  return { mode, text };
}

function syncMediaTextVisibility() {
  const textOnly = document.getElementById("media-type").value === "text";
  document.getElementById("media-text-group").hidden = textOnly;
  document.getElementById("media-brief-col").hidden = textOnly;
}

async function sendMessage() {
  const input = document.getElementById("chat-input");
  const content = input.value.trim();
  if (!content || !genState.sessionId) return;
  if (!genState.accounts.length) await loadAccounts();
  if (!state.brands.length || publishableNets().length === 0) {
    showAlert(brandWarningText());
    return;
  }
  const presetId = document.getElementById("preset-select").value || null;
  const mediaIds = genState.pendingMedia.map((m) => m.id);
  const brandingUsage = readBrandingUsage();
  const mediaType = document.getElementById("media-type").value || null;
  const onMediaText = readOnMediaText();
  const mediaBrief = document.getElementById("media-brief").value.trim() || null;
  const genNets = genState.genNets.length ? genState.genNets : publishableNets();
  input.value = "";
  document.getElementById("media-brief").value = "";
  const el = document.getElementById("chat-messages");
  el.insertAdjacentHTML("beforeend",
    `<div class="msg user"><div class="bubble">${esc(content)}</div>${mediaThumbs(mediaIds)}</div><div class="loading" id="gen-loading"><span class="spinner"></span>${t("gen.team_generating")}</div>`);
  el.scrollTop = el.scrollHeight;
  document.getElementById("send-btn").disabled = true;
  try {
    await api(`/chat/sessions/${genState.sessionId}/send`, {
      method: "POST", body: JSON.stringify({ content, media_ids: mediaIds, preset_id: presetId, branding_usage: brandingUsage, media_type: mediaType, on_media_text: onMediaText, media_brief: mediaBrief, platforms: genNets }),
      timeoutMs: 130000,
    });
    genState.pendingMedia = [];
    renderPendingMedia();
    await selectSession(genState.sessionId);
    await loadSessions();
  } catch (e) {
    document.getElementById("gen-loading")?.remove();
    el.insertAdjacentHTML("beforeend", `<div class="error-msg">${esc(e.message)}</div>`);
    el.scrollTop = el.scrollHeight;
  } finally {
    document.getElementById("send-btn").disabled = false;
  }
}

async function adjustProposal(id) {
  openFeedbackModal("adjust", id, t("gen.adjust_title"), t("gen.adjust_placeholder"));
}

/* ---------- feedback modal (ajuster proposition / média) ---------- */
let feedbackMode = null;  // "adjust" | "regenerate"
let feedbackTargetId = null;

function openFeedbackModal(mode, id, title, placeholder) {
  feedbackMode = mode;
  feedbackTargetId = id;
  document.getElementById("adjust-title").textContent = title;
  document.getElementById("adjust-text").placeholder = placeholder;
  document.getElementById("adjust-text").value = "";
  document.getElementById("adjust-modal").hidden = false;
  document.getElementById("adjust-text").focus();
}

function closeFeedbackModal() {
  document.getElementById("adjust-modal").hidden = true;
}

async function confirmFeedback() {
  const feedback = document.getElementById("adjust-text").value.trim();
  if (!feedback) { document.getElementById("adjust-text").focus(); return; }
  closeFeedbackModal();
  if (feedbackMode === "regenerate") {
    await regenerateMedia(feedbackTargetId, feedback);
  } else {
    await adjustProposalSubmit(feedbackTargetId, feedback);
  }
}

async function adjustProposalSubmit(id, feedback) {
  const el = document.getElementById("chat-messages");
  el.insertAdjacentHTML("beforeend", `<div class="loading" id="gen-loading"><span class="spinner"></span>${t("gen.adjusting")}</div>`);
  el.scrollTop = el.scrollHeight;
  try {
    const res = await api(`/chat/proposals/${id}/adjust`, { method: "POST", body: JSON.stringify({ proposal_id: id, feedback }), timeoutMs: 130000 });
    document.getElementById("gen-loading")?.remove();
    // Use the response directly (no re-fetch round-trip that could hang).
    genState.messages.push(res.assistant_message);
    genState.proposals = res.proposals;
    renderChat();
    renderPostPipeline();
  } catch (e) {
    document.getElementById("gen-loading")?.remove();
    el.insertAdjacentHTML("beforeend", `<div class="error-msg">${esc(e.message)}</div>`);
    el.scrollTop = el.scrollHeight;
  }
}

async function regenerateMedia(postId, feedback) {
  postBusy(t("gen.regenerate_media"));
  try {
    genState.currentPost = await api(`/posts/${postId}/regenerate-media`, {
      method: "POST", body: JSON.stringify({ feedback }), timeoutMs: 250000,
    });
  } catch (e) { showAlert(e.message); }
  renderPostPipeline();
}

async function validateProposal(id) {
  const el = document.getElementById("chat-messages");
  el.insertAdjacentHTML("beforeend", `<div class="loading" id="gen-loading"><span class="spinner"></span>${t("gen.validating")}</div>`);
  el.scrollTop = el.scrollHeight;
  try {
    const res = await api(`/chat/proposals/${id}/validate`, { method: "POST", body: JSON.stringify({ brand_id: activeBrandId || null }), timeoutMs: 30000 });
    genState.currentPost = res.post || null;
    genState.currentPublications = [];
    genState.netSelection = genState.genNets.slice();
    await selectSession(genState.sessionId);
    renderPostPipeline();
  } catch (e) {
    document.getElementById("gen-loading")?.remove();
    el.insertAdjacentHTML("beforeend", `<div class="error-msg">${esc(e.message)}</div>`);
    el.scrollTop = el.scrollHeight;
  }
}

/* ---------- post production pipeline ---------- */

const POST_STATUS_LABELS = {
  concept_validated: "status.concept_validated",
  media_ready: "status.media_ready",
  compliance_ok: "status.compliance_ok",
  approved: "status.approved",
  scheduled: "status.scheduled",
  publishing: "status.publishing",
  published: "status.published",
  failed: "status.failed",
};

const TIKTOK_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 1 1-2.89-2.9c.3 0 .58.05.85.13V9.4a6.34 6.34 0 0 0-.85-.06 6.34 6.34 0 1 0 6.34 6.34V8.94a8.27 8.27 0 0 0 4.77 1.52V6.69a4.85 4.85 0 0 1-1-.69z"/></svg>`;
const INSTAGRAM_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="2.5" y="2.5" width="19" height="19" rx="5"/><circle cx="12" cy="12" r="4.2"/><circle cx="17.6" cy="6.4" r="1.2" fill="currentColor" stroke="none"/></svg>`;
const LINKEDIN_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M20.45 20.45h-3.55v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.36V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.12 2.06 2.06 0 0 1 0 4.12zM7.12 20.45H3.56V9h3.56v11.45z"/></svg>`;
const X_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M18.9 1.15h3.68l-8.04 9.19L24 22.85h-7.41l-5.8-7.58-6.64 7.58H.47l8.6-9.83L0 1.15h7.6l5.24 6.93 6.06-6.93z"/></svg>`;
const FACEBOOK_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>`;
const YOUTUBE_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>`;
const THREADS_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12.186 24h-.007c-3.581-.024-6.334-1.205-8.184-3.509C2.35 18.44 1.5 15.586 1.472 12.01v-.017c.03-3.579 1.297-6.314 3.767-8.13C7.17 2.167 9.887 1.32 12.22 1.32c2.329 0 5.046.847 6.98 2.483 1.752 1.484 3.003 3.633 3.77 6.48.194.72.353 1.448.474 2.16a16.7 16.7 0 0 1-9.06-.545 12.4 12.4 0 0 1-4.638-3.062 1.4 1.4 0 1 1 2.09-1.866 9.6 9.6 0 0 0 3.25 2.186c.793.3 1.637.494 2.499.578-1.547-4.334-4.646-6.35-7.85-6.35-3.868 0-6.905 2.93-6.905 6.654 0 3.725 3.037 6.654 6.905 6.654 1.495 0 2.92-.458 4.126-1.312a1.405 1.405 0 1 1 1.61 2.303A9.72 9.72 0 0 1 12.186 24z"/></svg>`;
const PINTEREST_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12.017 0C5.396 0 .029 5.367.029 11.987c0 5.079 3.158 9.417 7.618 11.162-.105-.949-.199-2.403.041-3.439.219-.937 1.406-5.957 1.406-5.957s-.359-.72-.359-1.781c0-1.663.967-2.911 2.168-2.911 1.024 0 1.518.769 1.518 1.688 0 1.029-.653 2.567-.992 3.992-.285 1.193.6 2.165 1.775 2.165 2.128 0 3.768-2.245 3.768-5.487 0-2.861-2.063-4.869-5.008-4.869-3.41 0-5.409 2.562-5.409 5.199 0 1.033.394 2.143.889 2.741.099.12.112.225.085.345-.09.375-.293 1.199-.334 1.363-.053.225-.172.271-.401.165-1.495-.69-2.433-2.878-2.433-4.646 0-3.776 2.748-7.252 7.92-7.252 4.158 0 7.392 2.967 7.392 6.923 0 4.135-2.607 7.462-6.233 7.462-1.214 0-2.354-.629-2.758-1.379l-.749 2.848c-.269 1.045-1.004 2.352-1.498 3.146 1.123.345 2.306.535 3.55.535 6.607 0 11.985-5.365 11.985-11.987C23.97 5.39 18.592.026 11.985.026L12.017 0z"/></svg>`;
const GOOGLE_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12.48 10.92v3.28h7.84c-.24 1.84-.853 3.187-1.787 4.133-1.147 1.147-2.933 2.4-6.053 2.4-4.827 0-8.6-3.893-8.6-8.72s3.773-8.72 8.6-8.72c2.6 0 4.507 1.027 5.907 2.347l2.307-2.307C18.747 1.44 16.133 0 12.48 0 5.867 0 .307 5.387.307 12s5.56 12 12.173 12c3.573 0 6.267-1.173 8.373-3.36 2.16-2.16 2.84-5.213 2.84-7.667 0-.76-.053-1.467-.173-2.053H12.48z"/></svg>`;
const BLUESKY_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 10.8c-1.087-2.114-4.046-6.053-6.798-7.995C2.566.944 1.561 1.266.902 1.565.139 1.908 0 3.08 0 3.768c0 .69.378 5.65.624 6.479.815 2.736 3.713 3.66 6.383 3.364.136-.02.275-.039.415-.056-.138.022-.276.04-.415.056-3.912.58-7.387 2.005-2.83 7.078 5.013 5.19 6.87-1.113 7.823-4.308.953 3.195 2.05 9.271 7.733 4.308 4.267-4.308 1.172-6.498-2.74-7.078a8.741 8.741 0 0 1-.415-.056c.14.017.279.036.415.056 2.67.297 5.568-.628 6.383-3.364.246-.828.624-5.79.624-6.478 0-.69-.139-1.861-.902-2.206-.659-.298-1.664-.62-4.3 1.24C16.046 4.748 13.087 8.687 12 10.8z"/></svg>`;

const NET_SVGS = { tiktok: TIKTOK_SVG, instagram: INSTAGRAM_SVG, facebook: FACEBOOK_SVG, linkedin: LINKEDIN_SVG, x: X_SVG, youtube: YOUTUBE_SVG, threads: THREADS_SVG, pinterest: PINTEREST_SVG, google: GOOGLE_SVG, bluesky: BLUESKY_SVG };

function postBusy(msg) {
  const el = document.querySelector(".pp-actions");
  if (el) el.innerHTML = `<div class="loading"><span class="spinner"></span>${msg}</div>`;
}

async function genMedia() {
  postBusy(t("pp.generating_media"));
  try {
    genState.currentPost = await api(`/posts/${genState.currentPost.id}/generate-media`, { method: "POST", timeoutMs: 250000 });
  } catch (e) { showAlert(e.message); }
  renderPostPipeline();
}

async function scanCompliance() {
  postBusy(t("pp.scanning"));
  try {
    genState.currentPost = await api(`/posts/${genState.currentPost.id}/compliance`, { method: "POST", timeoutMs: 130000 });
  } catch (e) { showAlert(e.message); }
  await loadAccounts();
  renderPostPipeline();
}

async function createPublications() {
  const platforms = [...document.querySelectorAll(".net-card.selected")].map((b) => b.dataset.net);
  if (!platforms.length) { showAlert(t("pp.choose_network_alert")); return; }
  try {
    genState.currentPublications = await api(`/posts/${genState.currentPost.id}/publications`, {
      method: "POST", body: JSON.stringify({ platforms }),
    });
  } catch (e) { showAlert(e.message); }
  renderPostPipeline();
}

async function stepBack() {
  try {
    genState.currentPost = await api(`/posts/${genState.currentPost.id}/step-back`, { method: "POST" });
    genState.currentPublications = await api(`/posts/${genState.currentPost.id}/publications`);
  } catch (e) { showAlert(e.message); }
  await loadAccounts();
  renderPostPipeline();
}

async function approvePost() {
  try {
    genState.currentPost = await api(`/posts/${genState.currentPost.id}/approve`, { method: "POST" });
  } catch (e) { showAlert(e.message); }
  renderPostPipeline();
}

async function publishNow() {
  askConfirm(t("pp.publish_confirm_title"), t("pp.publish_confirm_text"), doPublishNow);
}

async function doPublishNow() {
  postBusy(t("pp.publishing"));
  try {
    const caption = document.getElementById("pp-caption")?.value || null;
    genState.currentPublications = await api(`/posts/${genState.currentPost.id}/publish`, {
      method: "POST", body: JSON.stringify({ confirm: true, caption }), timeoutMs: 30000,
    });
    genState.currentPost = await api(`/posts/${genState.currentPost.id}`);
  } catch (e) { showAlert(e.message); }
  renderPostPipeline();
}

async function schedulePost() {
  openCalendarModal();
}

/* ---------- calendar (scheduling) ---------- */
let calState = { year: null, month: null, day: null, hour: 9, minute: 0 };

function openCalendarModal() {
  const now = new Date();
  calState.year = now.getFullYear();
  calState.month = now.getMonth();
  calState.day = now.getDate();
  calState.hour = now.getHours();
  calState.minute = 0;
  document.getElementById("calendar-modal").hidden = false;
  renderScheduleCalendar();
}

function closeCalendarModal() { document.getElementById("calendar-modal").hidden = true; }

function renderScheduleCalendar() {
  const { year, month } = calState;
  const first = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const startDow = (first.getDay() + 6) % 7;  // Monday = 0
  const MONTHS = ["cal.january", "cal.february", "cal.march", "cal.april", "cal.may", "cal.june", "cal.july", "cal.august", "cal.september", "cal.october", "cal.november", "cal.december"];
  const today = new Date();
  const todayKey = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  let cells = "";
  for (let i = 0; i < startDow; i++) cells += `<span class="cal-cell empty"></span>`;
  for (let d = 1; d <= daysInMonth; d++) {
    const selected = d === calState.day ? "selected" : "";
    const past = new Date(year, month, d).getTime() < todayKey ? "past" : "";
    cells += `<span class="cal-cell ${selected} ${past}" data-day="${d}">${d}</span>`;
  }
  document.getElementById("cal-view").innerHTML = `
    <div class="cal-head">
      <button class="btn-sm" data-cal-prev type="button">‹</button>
      <span class="cal-title">${t(MONTHS[month])} ${year}</span>
      <button class="btn-sm" data-cal-next type="button">›</button>
    </div>
    <div class="cal-grid cal-dow">${BT_DAYS.map((k) => `<span>${t(k)[0]}</span>`).join("")}</div>
    <div class="cal-grid">${cells}</div>
    <div class="cal-time">
      <label>${t("pp.time")}</label>
      <input type="time" id="cal-time" value="${String(calState.hour).padStart(2, "0")}:${String(calState.minute).padStart(2, "0")}">
    </div>`;
  document.querySelector("[data-cal-prev]").addEventListener("click", () => { calState.month--; if (calState.month < 0) { calState.month = 11; calState.year--; } renderScheduleCalendar(); });
  document.querySelector("[data-cal-next]").addEventListener("click", () => { calState.month++; if (calState.month > 11) { calState.month = 0; calState.year++; } renderScheduleCalendar(); });
  document.querySelectorAll(".cal-cell[data-day]").forEach((c) => c.addEventListener("click", () => {
    if (c.classList.contains("past")) return;
    calState.day = parseInt(c.dataset.day, 10);
    renderScheduleCalendar();
  }));
  document.getElementById("cal-time").addEventListener("change", (e) => {
    const [h, m] = e.target.value.split(":").map(Number);
    calState.hour = h; calState.minute = m;
  });
}

async function confirmSchedule() {
  const dt = new Date(calState.year, calState.month, calState.day, calState.hour, calState.minute);
  closeCalendarModal();
  askConfirm(t("pp.schedule_confirm_title"), t("pp.schedule_confirm_text"), () => doSchedule(dt));
}

async function doSchedule(dt) {
  postBusy(t("pp.scheduling"));
  try {
    const caption = document.getElementById("pp-caption")?.value || null;
    genState.currentPublications = await api(`/posts/${genState.currentPost.id}/publish`, {
      method: "POST", body: JSON.stringify({ confirm: true, scheduled_at: dt.toISOString(), caption }), timeoutMs: 30000,
    });
    genState.currentPost = await api(`/posts/${genState.currentPost.id}`);
  } catch (e) { showAlert(e.message); }
  renderPostPipeline();
}

async function loadAccounts() {
  try { genState.accounts = await api("/connect"); } catch (e) { genState.accounts = []; }
  renderGenNetPicker();
}

function publishableNets() {
  return [...new Set(
    (genState.accounts || [])
      .filter((a) => a.is_active && (activeBrandId ? a.brand_id === activeBrandId : !a.brand_id))
      .map((a) => a.platform)
  )];
}

function renderGenNetPicker() {
  const el = document.getElementById("gen-net-picker");
  if (!el) return;
  const nets = publishableNets();
  // Default-select any newly available network; drop ones no longer available.
  nets.forEach((n) => { if (!genState.genNets.includes(n)) genState.genNets.push(n); });
  genState.genNets = genState.genNets.filter((n) => nets.includes(n));
  el.innerHTML = nets.length
    ? nets.map((net) => `<button type="button" class="net-card ${genState.genNets.includes(net) ? "selected" : ""}" data-gen-net="${net}">${NET_SVGS[net] || ""}<span>${PLATFORM_LABELS[net] || net}</span></button>`).join("")
    : `<div class="muted" style="font-size:12px">${t("pp.no_accounts")}</div>`;
  el.querySelectorAll("[data-gen-net]").forEach((b) => b.addEventListener("click", () => {
    const net = b.dataset.genNet;
    genState.genNets = genState.genNets.includes(net)
      ? genState.genNets.filter((x) => x !== net)
      : [...genState.genNets, net];
    b.classList.toggle("selected");
  }));
}

function canPublish() {
  return state.me.role === "admin" || !!state.me.can_publish;
}

function renderPostPipeline() {
  const el = document.getElementById("post-pipeline");
  const post = genState.currentPost;
  if (!post) { el.innerHTML = ""; return; }
  const c = post.content || {};
  const mv = post.media_variants;
  const mediaHtml = (mv && typeof mv === "object" && Object.keys(mv).length)
    ? Object.entries(mv).map(([platform, id]) =>
        `<div class="pv-media"><span class="pill">${esc(platform)}</span><img class="thumb-lg" src="/media/${id}/file" alt="" style="cursor:zoom-in" onclick="openLightbox('/media/${id}/file')"></div>`).join("")
    : (post.media_ids || []).map((id) =>
        `<img class="thumb-lg" src="/media/${id}/file" alt="" style="cursor:zoom-in" onclick="openLightbox('/media/${id}/file')">`).join("");
  let compl = "";
  if (post.compliance) {
    const v = post.compliance.verdict || "risk";
    const vLabel = t("pp.verdict_" + v) || v;
    const risks = (post.compliance.risks || []).join(" · ");
    compl = `<div class="compl compl-${v}">${t("pp.compliance", { v: vLabel })}${risks ? " — " + esc(risks) : ""}</div>`;
  }
  const pubs = genState.currentPublications || [];
  const links = pubs.filter((p) => p.remote_url).map((p) =>
    `<div class="pub-link"><span class="pill">${esc(p.platform)}</span> <a href="${esc(p.remote_url)}" target="_blank" rel="noopener">${esc(p.remote_url)}</a> <button class="btn-mini" data-copy="${esc(p.remote_url)}" type="button">${t("common.copy")}</button></div>`).join("");

  const isText = post.media_type === "text";
  let scheduledWhen = "";
  let actions = "";
  if (post.status === "concept_validated") {
    actions = isText
      ? `<button class="btn-primary" id="pp-comp">${t("pp.scan_compliance")}</button>`
      : `<button class="btn-primary" id="pp-gen">${t("pp.generate_media")}</button>`;
  } else if (post.status === "media_ready") {
    actions = `<button class="btn-primary" id="pp-comp">${t("pp.scan_compliance")}</button>${isText ? "" : `
      <button class="btn-sm" id="pp-regen">${t("pp.adjust_media")}</button>`}`;
  } else if (post.status === "compliance_ok") {
    if (pubs.length) {
      actions = `<button class="btn-primary" id="pp-approve">${t("pp.approve")}</button>${isText ? "" : `
        <button class="btn-sm" id="pp-regen">${t("pp.adjust_media")}</button>`}`;
    } else {
      const nets = publishableNets();
      const picker = nets.length
        ? nets.map((net) => `<button type="button" class="net-card ${genState.netSelection.includes(net) ? "selected" : ""}" data-net="${net}">${NET_SVGS[net] || ""}<span>${PLATFORM_LABELS[net] || net}</span></button>`).join("")
        : `<div class="muted">${t("pp.no_accounts")}</div>`;
      actions = `<div class="net-picker">${picker}</div>
      <button class="btn-primary" id="pp-pubs" ${nets.length ? "" : "disabled"}>${t("pp.choose_networks")}</button>`;
    }
  } else if (post.status === "approved") {
    actions = canPublish()
      ? `<button class="btn-primary" id="pp-now">${t("pp.publish_now")}</button>
      <button class="btn-sm" id="pp-sched">${t("pp.schedule")}</button>`
      : `<div class="muted">${t("pp.publish_forbidden")}</div>`;
  } else if (post.status === "scheduled") {
    const sched = pubs.map((p) => p.scheduled_at).filter(Boolean).sort()[0];
    scheduledWhen = sched
      ? " · " + new Date(sched.endsWith("Z") ? sched : sched + "Z").toLocaleString(currentLang === "fr" ? "fr-FR" : "en-GB", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
      : "";
    actions = "";
  } else if (post.status === "published") {
    actions = "";
  } else if (post.status === "failed") {
    const err = pubs.map((p) => p.last_error).filter(Boolean)[0];
    actions = `<div class="muted" style="color:var(--sp-danger)">${t("pp.failed")}${err ? ` — ${esc(err)}` : ""}.</div>`;
  }

  const previews = pubs.map((p) => networkPreview(p, c)).join("");
  const captionEditable = post.status === "approved" || (post.status === "compliance_ok" && pubs.length);

  const canBack = ["media_ready", "compliance_ok", "approved"].includes(post.status);
  if (canBack) actions = `<button class="btn-sm btn-back" id="pp-back" type="button">${t("pp.back")}</button>` + actions;

  el.innerHTML = `<div class="card post-pipeline">
    <div class="card-title">${t("pp.in_progress")} <span class="badge badge-${post.status}">${t(POST_STATUS_LABELS[post.status] || post.status) + scheduledWhen}</span></div>
    ${compl}
    ${captionEditable
      ? `<div class="caption-edit"><label class="muted" style="font-size:12px">${t("pp.caption_label")}</label><textarea id="pp-caption" rows="6">${esc(c.caption || "")}</textarea></div>`
      : `<div class="post-caption">${esc(c.caption || "")}</div>`}
    ${mediaHtml ? `<div class="thumb-row">${mediaHtml}</div>` : ""}
    ${previews ? `<div class="pp-previews-wrap">
      <div class="pp-previews-head">
        <span class="panel-subtitle" style="margin:0">${t("pp.preview")}</span>
        <div class="seg">
          <button type="button" class="seg-btn ${genState.previewMode === "mobile" ? "active" : ""}" data-pvmode="mobile">${t("pp.mobile")}</button>
          <button type="button" class="seg-btn ${genState.previewMode === "desktop" ? "active" : ""}" data-pvmode="desktop">${t("pp.desktop")}</button>
        </div>
      </div>
      <div class="pp-previews ${genState.previewMode === "desktop" ? "desktop" : "mobile"}">${previews}</div>
    </div>` : ""}
    ${links ? `<div class="pp-links"><div class="muted" style="margin-bottom:4px">${t("pp.quick_links")}</div>${links}</div>` : ""}
    <div class="pp-actions">${actions}</div>
  </div>`;

  el.querySelector("#pp-gen")?.addEventListener("click", genMedia);
  el.querySelector("#pp-comp")?.addEventListener("click", scanCompliance);
  el.querySelector("#pp-regen")?.addEventListener("click", () =>
    openFeedbackModal("regenerate", post.id, t("gen.adjust_media_title"), t("gen.adjust_media_placeholder")));
  el.querySelector("#pp-pubs")?.addEventListener("click", createPublications);
  el.querySelector("#pp-approve")?.addEventListener("click", approvePost);
  el.querySelector("#pp-now")?.addEventListener("click", publishNow);
  el.querySelector("#pp-sched")?.addEventListener("click", openCalendarModal);
  el.querySelector("#pp-back")?.addEventListener("click", stepBack);
  el.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copyUrl(b.dataset.copy, b)));
  el.querySelectorAll("[data-pvmode]").forEach((b) => b.addEventListener("click", () => {
    genState.previewMode = b.dataset.pvmode;
    renderPostPipeline();
  }));
  el.querySelectorAll(".net-card").forEach((b) => b.addEventListener("click", () => {
    const net = b.dataset.net;
    genState.netSelection = genState.netSelection.includes(net)
      ? genState.netSelection.filter((x) => x !== net)
      : [...genState.netSelection, net];
    b.classList.toggle("selected");
  }));
  if (["scheduled", "publishing"].includes(post.status)) startPipelinePolling();
  else stopPipelinePolling();
}

/* ---------- pipeline auto-refresh (scheduled → published without reload) ---------- */
let pipelinePollTimer = null;
function startPipelinePolling() {
  if (pipelinePollTimer) return;
  pipelinePollTimer = setInterval(pollPipeline, 10000);
}
function stopPipelinePolling() {
  if (pipelinePollTimer) { clearInterval(pipelinePollTimer); pipelinePollTimer = null; }
}
async function pollPipeline() {
  const post = genState.currentPost;
  if (!post) return;
  try {
    genState.currentPost = await api(`/posts/${post.id}`);
    genState.currentPublications = await api(`/posts/${genState.currentPost.id}/publications`);
    renderPostPipeline();
    if (!["scheduled", "publishing"].includes(genState.currentPost.status)) stopPipelinePolling();
  } catch (e) { /* transient — retry next tick */ }
}

function networkPreview(p, c) {
  const media = (genState.currentPost.media_ids || []).map((id) => `<img src="/media/${id}/file" alt="" style="cursor:zoom-in" onclick="openLightbox('/media/${id}/file')">`).join("");
  if (p.platform === "instagram") {
    return `<div class="net-preview ig">
      <div class="ig-top"><span class="ig-avatar"></span><span class="ig-name">votre_compte</span><span class="ig-dots">⋯</span></div>
      <div class="ig-media">${media}</div>
      <div class="ig-icons">♡ &nbsp; 💬 &nbsp; ↗</div>
      <div class="ig-caption"><b>votre_compte</b> ${esc(c.caption || "")}</div>
      <div class="ig-tags">${(c.hashtags || []).map((h) => esc(h)).join(" ")}</div>
    </div>`;
  }
  if (p.platform === "facebook") {
    return `<div class="net-preview fb">
      <div class="fb-top"><span class="fb-avatar"></span><div><div class="fb-name">votre_page</div><div class="fb-meta">Public</div></div></div>
      <div class="fb-text">${esc(c.caption || "")}</div>
      ${media ? `<div class="fb-media">${media}</div>` : ""}
      <div class="fb-actions"><span>👍</span><span>💬</span><span>↗</span></div>
    </div>`;
  }
  if (p.platform === "linkedin") {
    return `<div class="net-preview li">
      <div class="li-top"><span class="li-avatar"></span><div><div class="li-name">votre_compte</div><div class="li-meta">Public</div></div></div>
      <div class="li-text">${esc(c.caption || "")}</div>
      ${media ? `<div class="li-media">${media}</div>` : ""}
      <div class="li-actions"><span>👍</span><span>💬</span><span>↻</span><span>↗</span></div>
    </div>`;
  }
  if (p.platform === "x") {
    return `<div class="net-preview x">
      <div class="x-top"><span class="x-avatar"></span><div class="x-id"><div class="x-name">votre_compte</div><div class="x-handle">@votre_compte</div></div></div>
      <div class="x-text">${esc(c.caption || "")}</div>
      ${media ? `<div class="x-media">${media}</div>` : ""}
      <div class="x-actions"><span>💬</span><span>↻</span><span>❤</span><span>↗</span></div>
    </div>`;
  }
  if (p.platform === "youtube") {
    return `<div class="net-preview yt">
      ${media ? `<div class="yt-thumb">${media}<span class="yt-play">▶</span></div>` : ""}
      <div class="yt-title">${esc(c.caption || "").slice(0, 80)}</div>
      <div class="yt-meta">votre_chaîne · 1,2 k vues</div>
    </div>`;
  }
  if (p.platform === "threads") {
    return `<div class="net-preview th">
      <div class="th-top"><span class="th-avatar"></span><div class="th-name">votre_compte</div></div>
      <div class="th-text">${esc(c.caption || "")}</div>
      ${media ? `<div class="th-media">${media}</div>` : ""}
      <div class="th-actions"><span>♡</span><span>💬</span><span>↻</span><span>↗</span></div>
    </div>`;
  }
  if (p.platform === "pinterest") {
    return `<div class="net-preview pi">
      ${media ? `<div class="pi-media">${media}</div>` : ""}
      <div class="pi-title">${esc(c.caption || "").slice(0, 60)}</div>
    </div>`;
  }
  if (p.platform === "google") {
    return `<div class="net-preview gb">
      <div class="gb-top"><span class="gb-avatar"></span><div><div class="gb-name">votre_entreprise</div><div class="gb-meta">Google Business</div></div></div>
      <div class="gb-text">${esc(c.caption || "")}</div>
      ${media ? `<div class="gb-media">${media}</div>` : ""}
      <div class="gb-cta"><span>Call</span><span>Directions</span><span>Website</span></div>
    </div>`;
  }
  if (p.platform === "bluesky") {
    return `<div class="net-preview bs">
      <div class="bs-top"><span class="bs-avatar"></span><div><div class="bs-name">votre_compte</div><div class="bs-handle">@votre_compte.bsky.social</div></div></div>
      <div class="bs-text">${esc(c.caption || "")}</div>
      ${media ? `<div class="bs-media">${media}</div>` : ""}
      <div class="bs-actions"><span>💬</span><span>↻</span><span>♡</span></div>
    </div>`;
  }
  return `<div class="net-preview tt">
    <div class="tt-media">${media}</div>
    <div class="tt-rail"><span>♡</span><span>💬</span><span>↗</span></div>
    <div class="tt-caption"><b>@votre_compte</b> ${esc(c.caption || "")}</div>
    <div class="tt-tags">${(c.hashtags || []).map((h) => esc(h)).join(" ")}</div>
  </div>`;
}

function renderMediaList() {
  const el = document.getElementById("media-list");
  const shown = genState.media.slice(0, 4);
  el.innerHTML = shown.map((m) => {
    const src = m.kind === "image" ? `/media/${m.id}/file` : `/media/${m.id}/thumb`;
    const attached = genState.pendingMedia.some((x) => x.id === m.id);
    return `<div class="media-cell ${attached ? "attached" : ""}" title="${esc(m.original_name)}" data-attach="${m.id}">
      <img src="${src}" alt="">
    </div>`;
  }).join("") || `<div class="muted" style="grid-column:1/-1">${t("media.no_media")}</div>`;
  if (genState.media.length > 4) {
    el.insertAdjacentHTML("beforeend",
      `<button class="btn-sm" id="media-more" style="grid-column:1/-1;width:100%;margin-top:4px" type="button">${t("media.view_more", { n: genState.media.length - 4 })} →</button>`);
    el.querySelector("#media-more").addEventListener("click", openMediaModal);
  }
  el.querySelectorAll("[data-attach]").forEach((n) => n.addEventListener("click", () => toggleAttach(n.dataset.attach)));
}

function toggleAttach(id) {
  const i = genState.pendingMedia.findIndex((x) => x.id === id);
  if (i >= 0) genState.pendingMedia.splice(i, 1);
  else {
    const m = genState.media.find((x) => x.id === id);
    if (m) genState.pendingMedia.push(m);
  }
  renderPendingMedia();
  renderMediaList();
}

/* ---------- media modal (browse + purge) ---------- */

function openMediaModal() {
  document.getElementById("media-modal").hidden = false;
  renderMediaModal();
}

function closeMediaModal() {
  document.getElementById("media-modal").hidden = true;
}

function renderMediaModal() {
  const q = document.getElementById("media-search").value.toLowerCase();
  const el = document.getElementById("media-modal-grid");
  const items = genState.media.filter((m) => !q || m.original_name.toLowerCase().includes(q));
  el.innerHTML = items.map((m) => {
    const src = m.kind === "image" ? `/media/${m.id}/file` : `/media/${m.id}/thumb`;
    const attached = genState.pendingMedia.some((x) => x.id === m.id);
    return `<div class="media-modal-cell ${attached ? "attached" : ""}">
      <img src="${src}" alt="">
      <div class="name">${esc(m.original_name)}</div>
      <div class="mm-actions">
        <button data-mm-attach="${m.id}">${attached ? t("common.remove") : t("common.attach")}</button>
        <button data-mm-del="${m.id}" style="color:var(--sp-danger)">${t("common.delete")}</button>
      </div>
    </div>`;
  }).join("") || `<div class="muted" style="grid-column:1/-1">${t("media.no_media")}</div>`;
  el.querySelectorAll("[data-mm-attach]").forEach((b) => b.addEventListener("click", () => { toggleAttach(b.dataset.mmAttach); renderMediaModal(); }));
  el.querySelectorAll("[data-mm-del]").forEach((b) => b.addEventListener("click", () => deleteMedia(b.dataset.mmDel)));
}

async function deleteMedia(id) {
  askConfirm(t("media.delete_title"), t("media.delete_text"), async () => {
    try {
      await api(`/media/${id}`, { method: "DELETE" });
      genState.pendingMedia = genState.pendingMedia.filter((x) => x.id !== id);
      await loadMedia();
      renderPendingMedia();
      renderMediaModal();
    } catch (e) { showAlert(e.message); }
  });
}

/* ---------- branding kit modal ---------- */

function openBrandKitModal() {
  document.getElementById("brandkit-modal").hidden = false;
}

function closeBrandKitModal() {
  document.getElementById("brandkit-modal").hidden = true;
}

async function loadMedia() {
  const q = activeBrandId ? `?brand_id=${activeBrandId}` : "";
  genState.media = await api(`/media${q}`);
  renderMediaList();
}

async function uploadFiles(input) {
  const files = input.files;
  if (!files.length) return;
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f);
    if (activeBrandId) fd.append("brand_id", activeBrandId);
    try { await fetch("/media/upload", { method: "POST", body: fd }); } catch (e) { /* ignore */ }
  }
  input.value = "";
  await loadMedia();
}

const BRAND_KINDS = {
  logo: "brand.logo", color: "brand.color", font: "brand.font", visual: "brand.visual",
  guideline: "brand.guideline", condition: "brand.condition", other: "brand.other",
};

function renderBrandingConsignes() {
  const el = document.getElementById("branding-consignes");
  const byKind = {};
  genState.branding.forEach((a) => { (byKind[a.kind] ||= []).push(a); });

  let html = `<div class="panel-subtitle">${t("brand.consignes")}</div>`;
  if (!genState.branding.length) {
    html += `<div class="muted" style="font-size:12px">${t("brand.add_hint")}</div>`;
  } else {
    if (byKind.logo) {
      html += `<label class="chk"><input type="checkbox" id="bu-logo"> ${t("brand.integrate_logo")}</label>
        <select id="bu-placement">
          <option value="watermark">${t("brand.placement_watermark")}</option>
          <option value="decor">${t("brand.placement_decor")}</option>
          <option value="subtle">${t("brand.placement_subtle")}</option>
        </select>`;
    }
    if (byKind.color) html += `<label class="chk"><input type="checkbox" id="bu-color"> ${t("brand.use_colors")}</label>`;
    if (byKind.font) html += `<label class="chk"><input type="checkbox" id="bu-font"> ${t("brand.use_font")}</label>`;
    if (byKind.visual) html += `<label class="chk"><input type="checkbox" id="bu-visual"> ${t("brand.use_visuals")}</label>`;
    if (byKind.guideline) html += `<label class="chk"><input type="checkbox" id="bu-guideline"> ${t("brand.use_guidelines")}</label>`;
    (byKind.condition || []).forEach((c) => {
      const opts = (c.notes || "").split("|").map((s) => s.trim()).filter(Boolean);
      if (opts.length > 1) {
        html += `<label class="chk"><input type="checkbox" id="bu-cond-${c.id}"> ${esc(c.filename)}</label>
          <select id="bu-cond-val-${c.id}">${opts.map((o) => `<option>${esc(o)}</option>`).join("")}</select>`;
      } else {
        html += `<label class="chk"><input type="checkbox" id="bu-cond-${c.id}"> ${esc(c.filename)}</label>`;
      }
    });
  }
  el.innerHTML = html;
}

function readBrandingUsage() {
  const usage = { color: false, font: false, visual: false, guideline: false, custom: [] };
  const logoEl = document.getElementById("bu-logo");
  if (logoEl && logoEl.checked) {
    const p = document.getElementById("bu-placement");
    usage.logo = { use: true, placement: p ? p.value : "watermark" };
  }
  for (const key of ["color", "font", "visual", "guideline"]) {
    const el = document.getElementById("bu-" + key);
    usage[key] = !!(el && el.checked);
  }
  genState.branding.filter((a) => a.kind === "condition").forEach((a) => {
    const ck = document.getElementById(`bu-cond-${a.id}`);
    if (ck && ck.checked) {
      const valEl = document.getElementById(`bu-cond-val-${a.id}`);
      usage.custom.push({ label: a.filename, value: valEl ? valEl.value : "" });
    }
  });
  return usage;
}

function renderBrandList() {
  const el = document.getElementById("brand-list");
  el.innerHTML = genState.branding.map((a) =>
    `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;gap:6px">
      <span class="pill">${esc(t(BRAND_KINDS[a.kind] || a.kind))}</span>
      <span style="flex:1;font-size:13px">${esc(a.filename)}${a.notes ? ` <span class="muted">— ${esc(a.notes)}</span>` : ""}</span>
      <button class="btn-sm btn-danger" data-brand-del="${a.id}">×</button>
    </div>`
  ).join("") || `<div class="muted">${t("brand.empty")}</div>`;
  el.querySelectorAll("[data-brand-del]").forEach((b) => b.addEventListener("click", () => deleteBranding(b.dataset.brandDel)));
}

async function loadBranding() {
  const q = activeBrandId ? `?brand_id=${activeBrandId}` : "";
  genState.branding = await api(`/branding${q}`);
  renderBrandList();
  renderBrandingConsignes();
}

let pendingBrandMedia = null;  // {id, original_name}

function renderBrandFile() {
  document.getElementById("brand-file-name").textContent = pendingBrandMedia
    ? t("brand.file_label", { name: pendingBrandMedia.original_name }) : t("brand.no_file");
}

async function onBrandFile(e) {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  try {
    const res = await fetch("/media/upload", { method: "POST", body: fd });
    if (res.ok) { pendingBrandMedia = await res.json(); renderBrandFile(); }
  } catch (err) { showAlert(t("media.upload_failed")); }
  e.target.value = "";
}

const BRAND_KIND_HINTS = {
  logo: "brand.hint_logo",
  color: "brand.hint_color",
  font: "brand.hint_font",
  visual: "brand.hint_visual",
  guideline: "brand.hint_guideline",
  condition: "brand.hint_condition",
  other: "brand.hint_other",
};

function updateBrandKindHint() {
  const kind = document.getElementById("brand-kind").value;
  document.getElementById("brand-kind-hint").textContent = t(BRAND_KIND_HINTS[kind] || "");
  const notes = document.getElementById("brand-notes");
  notes.placeholder = kind === "condition"
    ? t("brand.notes_condition")
    : (kind === "color" ? t("brand.notes_color") : t("brand.notes"));
}

async function addBrandAsset() {
  const kind = document.getElementById("brand-kind").value;
  const filename = document.getElementById("brand-filename").value.trim();
  const notes = document.getElementById("brand-notes").value.trim();
  if (!filename) { showAlert(t("common.required_name")); return; }
  try {
    await api("/branding", { method: "POST", body: JSON.stringify({
      kind, filename, notes, media_id: pendingBrandMedia ? pendingBrandMedia.id : null, brand_id: activeBrandId || null,
    })});
    document.getElementById("brand-filename").value = "";
    document.getElementById("brand-notes").value = "";
    pendingBrandMedia = null;
    renderBrandFile();
    await loadBranding();
  } catch (e) { showAlert(e.message); }
}

async function deleteBranding(id) {
  await api(`/branding/${id}`, { method: "DELETE" });
  await loadBranding();
}

function renderPresets() {
  const sel = document.getElementById("preset-select");
  sel.innerHTML = `<option value="">${t("gen.no_preset")}</option>` +
    genState.presets.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  updatePresetHint();
}

function updatePresetHint() {
  const id = document.getElementById("preset-select").value;
  const p = genState.presets.find((x) => x.id === id);
  document.getElementById("preset-hint").innerHTML = p
    ? `<span style="color:var(--sp-accent)">${esc(p.name)}</span> — ${esc(p.directives)}`
    : "";
}

async function loadPresets() {
  const q = activeBrandId ? `?brand_id=${activeBrandId}` : "";
  genState.presets = await api(`/presets${q}`);
  renderPresets();
}

function toggleSessions() {
  const aside = document.getElementById("gen-sessions");
  aside.classList.toggle("collapsed");
  const btn = document.getElementById("sessions-toggle");
  const collapsed = aside.classList.contains("collapsed");
  btn.textContent = collapsed ? "»" : "«";
  btn.title = collapsed ? t("gen.expand") : t("gen.collapse");
}

async function initGeneration() {
  document.getElementById("new-session-btn").addEventListener("click", newSession);
  document.getElementById("send-btn").addEventListener("click", sendMessage);
  document.getElementById("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendMessage(); }
  });
  document.getElementById("preset-select").addEventListener("change", updatePresetHint);
  document.getElementById("media-type").addEventListener("change", syncMediaTextVisibility);
  document.getElementById("media-text-mode").addEventListener("change", () => {
    document.getElementById("media-text-spec").hidden = document.getElementById("media-text-mode").value !== "yes";
  });
  syncMediaTextVisibility();
  document.getElementById("media-upload-btn").addEventListener("click", () => document.getElementById("media-upload-input").click());
  document.getElementById("media-upload-input").addEventListener("change", (e) => uploadFiles(e.target));
  document.getElementById("brand-upload-btn").addEventListener("click", () => document.getElementById("brand-file-input").click());
  document.getElementById("brand-file-input").addEventListener("change", onBrandFile);
  document.getElementById("brand-add-btn").addEventListener("click", addBrandAsset);
  document.getElementById("sessions-toggle").addEventListener("click", toggleSessions);
  document.getElementById("branding-kit-btn").addEventListener("click", openBrandKitModal);
  document.getElementById("brandkit-close").addEventListener("click", closeBrandKitModal);
  document.getElementById("media-browse-btn").addEventListener("click", openMediaModal);
  document.getElementById("media-modal-close").addEventListener("click", closeMediaModal);
  document.getElementById("media-search").addEventListener("input", () => renderMediaModal());
  document.getElementById("brand-kind").addEventListener("change", updateBrandKindHint);
  updateBrandKindHint();
  document.getElementById("adjust-close").addEventListener("click", closeFeedbackModal);
  document.getElementById("adjust-cancel").addEventListener("click", closeFeedbackModal);
  document.getElementById("adjust-confirm").addEventListener("click", confirmFeedback);
  document.getElementById("cal-close").addEventListener("click", closeCalendarModal);
  document.getElementById("cal-cancel").addEventListener("click", closeCalendarModal);
  document.getElementById("cal-confirm").addEventListener("click", confirmSchedule);
  document.getElementById("cal-prev").addEventListener("click", () => { calView.month--; if (calView.month < 0) { calView.month = 11; calView.year--; } loadCalendar(); });
  document.getElementById("cal-next").addEventListener("click", () => { calView.month++; if (calView.month > 11) { calView.month = 0; calView.year++; } loadCalendar(); });
  document.getElementById("cal-today").addEventListener("click", () => { const n = new Date(); calView.year = n.getFullYear(); calView.month = n.getMonth(); loadCalendar(); });
  document.getElementById("cal-detail-close").addEventListener("click", closeCalDetail);
  document.getElementById("confirm-close").addEventListener("click", closeConfirmModal);
  document.getElementById("confirm-cancel").addEventListener("click", closeConfirmModal);
  document.getElementById("confirm-ok").addEventListener("click", okConfirm);
  document.getElementById("prompt-close").addEventListener("click", closePromptModal);
  document.getElementById("prompt-cancel").addEventListener("click", closePromptModal);
  document.getElementById("prompt-ok").addEventListener("click", okPrompt);
  document.getElementById("prompt-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); okPrompt(); }
  });
  document.getElementById("brand-del-close").addEventListener("click", closeBrandDeleteModal);
  document.getElementById("brand-del-cancel").addEventListener("click", closeBrandDeleteModal);
  document.getElementById("brand-del-soft").addEventListener("click", softDeleteBrand);
  document.getElementById("brand-del-hard").addEventListener("click", () => {
    document.getElementById("brand-del-confirm-wrap").hidden = false;
    document.getElementById("brand-del-confirm-input").focus();
  });
  document.getElementById("brand-del-confirm-input").addEventListener("input", (e) => {
    const name = (state.brands.find((x) => x.id === brandDelTargetId) || {}).name || "";
    document.getElementById("brand-del-confirm-btn").disabled = e.target.value.trim() !== name;
  });
  document.getElementById("brand-del-confirm-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); purgeBrand(); }
  });
  document.getElementById("brand-del-confirm-btn").addEventListener("click", purgeBrand);
  document.getElementById("pub-del-close").addEventListener("click", closePubDeleteModal);
  document.getElementById("pub-del-cancel").addEventListener("click", closePubDeleteModal);
  document.getElementById("pub-del-input").addEventListener("input", (e) => {
    document.getElementById("pub-del-confirm").disabled = e.target.value.trim().toUpperCase() !== confirmWord();
  });
  document.getElementById("pub-del-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); confirmPubDelete(); }
  });
  document.getElementById("pub-del-confirm").addEventListener("click", confirmPubDelete);
  document.getElementById("lightbox").addEventListener("click", closeLightbox);
  document.getElementById("pd-close").addEventListener("click", closePostDetail);
  document.querySelectorAll("[data-pf]").forEach((b) => b.addEventListener("click", () => loadPerformance(b.dataset.pf)));
  document.getElementById("perf-export-btn").addEventListener("click", () => {
    const params = [];
    if (perfPlatform && perfPlatform !== "all") params.push(`platform=${perfPlatform}`);
    if (activeBrandId) params.push(`brand_id=${activeBrandId}`);
    window.location.href = `/performance/report${params.length ? "?" + params.join("&") : ""}`;
  });
  document.getElementById("brand-create-btn").addEventListener("click", createBrand);
  document.getElementById("brand-logo").addEventListener("change", (e) => {
    document.getElementById("brand-logo-name").textContent = e.target.files[0] ? e.target.files[0].name : "";
  });
  document.getElementById("brand-logo-edit").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file || !pendingLogoBrandId) return;
    const fd = new FormData();
    fd.append("logo", file);
    try {
      await api(`/brands/${pendingLogoBrandId}/logo`, { method: "PATCH", body: fd });
      await loadBrands();
    } catch (err) { showAlert(err.message); }
    e.target.value = "";
    pendingLogoBrandId = "";
  });
  document.getElementById("accounts-table").addEventListener("change", async (e) => {
    const sel = e.target.closest("[data-brand-account]");
    if (!sel) return;
    const brandId = sel.value || null;
    try {
      await api(`/connect/${sel.dataset.brandAccount}`, { method: "PATCH", body: JSON.stringify({ brand_id: brandId }) });
      await loadAccounts();
      renderBrandWarning();
    } catch (err) { showAlert(err.message); }
  });
  document.querySelectorAll("[data-cf]").forEach((b) => b.addEventListener("click", () => loadCommunity(b.dataset.cf)));
  document.getElementById("cm-poll-btn").addEventListener("click", async () => { await api("/community/poll", { method: "POST" }); await loadCommunity(); });
  document.getElementById("cm-select-all").addEventListener("change", (e) => {
    document.querySelectorAll("[data-cm-check]").forEach((cb) => {
      cb.checked = e.target.checked;
      if (e.target.checked) cmSelection.add(cb.dataset.cmCheck); else cmSelection.delete(cb.dataset.cmCheck);
    });
    updateBatchBar();
  });
  document.getElementById("cm-batch-generate").addEventListener("click", cmBatchGenerate);
  document.getElementById("cm-batch-send").addEventListener("click", cmBatchSend);

  await loadPresets();
  await loadMedia();
  await loadBranding();
  await loadSessions();
  if (genState.sessions.length) await selectSession(genState.sessions[0].id);
  else await newSession();
}

/* ---------- language / i18n ---------- */

function applyLangSelection() {
  document.querySelectorAll("[data-lang]").forEach((b) =>
    b.classList.toggle("active", b.dataset.lang === currentLang));
}

async function changeLang(lang) {
  if (lang === currentLang) return;
  setLang(lang);            // client-side + static applyI18n
  applyLangSelection();
  try {
    state.me = await api("/auth/me", { method: "PATCH", body: JSON.stringify({ language: lang }) });
  } catch (e) { /* non-blocking — keep the local choice */ }
  rerenderAll();
}

function initLanguage(fallback) {
  const lang = initLang(fallback);  // localStorage choice wins, else account default
  applyLangSelection();
  if (lang !== fallback) {
    // pre-login language choice stored locally — sync it to the account
    api("/auth/me", { method: "PATCH", body: JSON.stringify({ language: lang }) }).catch(() => {});
  }
  document.querySelectorAll("[data-lang]").forEach((b) =>
    b.addEventListener("click", () => changeLang(b.dataset.lang)));
}

function rerenderAll() {
  render2FA();
  renderThemes();
  loadBrands();
  renderConnect();
  renderLLM();
  renderUsage();
  if (state.me.role === "admin") renderUsers();
  renderSessions();
  renderChat();
  renderPostPipeline();
  renderMediaList();
  renderPendingMedia();
  renderBrandList();
  renderBrandingConsignes();
  renderPresets();
  const active = document.querySelector(".nav-item.active");
  if (active) {
    if (active.dataset.tab === "performance") loadPerformance();
    else if (active.dataset.tab === "community") loadCommunity();
  }
}

/* ---------- init ---------- */

async function init() {
  initLang(null);  // apply cached/default language immediately (avoid flash)
  let me;
  try {
    me = await api("/auth/me");
  } catch (e) {
    if (e.status === 401) { window.location.href = "/login"; return; }
    throw e;
  }
  state.me = me;
  initLanguage(me.language);
  document.getElementById("user-name").textContent = me.name;
  document.getElementById("user-role").textContent = me.role;

  const theme = await api("/theme");
  applyTheme(theme);

  initTabs();
  initAccount();
  initBrandSelector();
  await renderThemes();
  await loadBrands();
  await renderConnect();
  await renderLLM();
  await renderUsage();
  await initUsers();
  await initGeneration();

  document.getElementById("logout-btn").addEventListener("click", async () => {
    await api("/auth/logout", { method: "POST" });
    window.location.href = "/login";
  });
}

init().catch((e) => {
  document.body.innerHTML = `<div class="empty-state"><div class="big">${t("common.error")}</div>${esc(e.message)}</div>`;
});
