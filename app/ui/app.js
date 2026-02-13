(() => {
  "use strict";

  const ADMIN_TOKEN_STORAGE_KEY = "fcam_ui_admin_token_v1";
  const UI_SETTINGS_KEY = "fcam_ui_settings_v1";
  const DEFAULT_KEY_TEST_URL = "https://www.google.com";
  const CLIENT_LOG_MAX = 200;

  const state = {
    adminToken: "",
    adminTokenSource: "memory",
    adminTokenExpiresAt: null,
    currentView: "dashboard",
    keys: [],
    keysFiltered: [],
    selectedKeyId: null,
    selectedKeyIds: new Set(),
    clients: [],
    clientsFiltered: [],
    selectedClientId: null,
    logs: [],
    logsCursor: null,
    logsHasMore: false,
    selectedLogId: null,
    auditLogs: [],
    auditCursor: null,
    auditHasMore: false,
    selectedAuditId: null,
  };

  const clientLogLines = [];

  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const hasValue = (v) => v !== undefined && v !== null && !(typeof v === "string" && v.trim() === "");

  function nowMs() {
    return Date.now();
  }

  function storageGet(store, key) {
    try {
      return store.getItem(key);
    } catch {
      return null;
    }
  }

  function storageSet(store, key, value) {
    try {
      store.setItem(key, value);
      return true;
    } catch {
      return false;
    }
  }

  function storageRemove(store, key) {
    try {
      store.removeItem(key);
    } catch {
      // ignore
    }
  }

  function safeJsonParse(raw) {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function renderClientLog() {
    const el = qs("#clientLog");
    if (!el) return;
    el.textContent = clientLogLines.length ? clientLogLines.join("\n") : "（空）";
  }

  function logClient(level, message, data) {
    const ts = new Date().toISOString();
    let line = `[${ts}] ${String(level || "info").toUpperCase()} ${message}`;
    if (data !== undefined) {
      try {
        line += ` ${JSON.stringify(data)}`;
      } catch {
        // ignore
      }
    }
    clientLogLines.push(line);
    while (clientLogLines.length > CLIENT_LOG_MAX) clientLogLines.shift();
    renderClientLog();
  }

  function setText(id, text) {
    const el = qs(`#${id}`);
    if (!el) return;
    el.textContent = text == null ? "" : String(text);
  }

  function setHidden(id, hidden) {
    const el = qs(`#${id}`);
    if (!el) return;
    el.hidden = Boolean(hidden);
  }

  function formatJson(obj) {
    return JSON.stringify(obj, null, 2);
  }

  function toast(kind, title, body) {
    const container = qs("#toasts");
    if (!container) return;

    logClient(kind || "info", title, body ? { body } : undefined);

    const el = document.createElement("div");
    el.className = `toast ${kind || ""}`.trim();

    const h = document.createElement("div");
    h.className = "toast-title";
    h.textContent = title;

    const p = document.createElement("div");
    p.className = "toast-body";
    p.textContent = body || "";

    const actions = document.createElement("div");
    actions.className = "toast-actions";

    const btn = document.createElement("button");
    btn.className = "btn";
    btn.type = "button";
    btn.textContent = "关闭";
    btn.addEventListener("click", () => el.remove());

    actions.appendChild(btn);
    el.appendChild(h);
    if (body) el.appendChild(p);
    el.appendChild(actions);
    container.appendChild(el);

    window.setTimeout(() => {
      if (el.isConnected) el.remove();
    }, 6000);
  }

  function updateConnectionUi(status) {
    const dot = qs("#connDot");
    const text = qs("#connText");
    if (!dot || !text) return;

    const statusToClass = { ok: "ok", warn: "warn", err: "err" };
    dot.className = `status-dot ${statusToClass[status] || ""}`.trim();

    if (!state.adminToken) {
      text.textContent = "未连接";
      return;
    }

    const suffix = state.adminTokenSource === "local" ? "（本机）" : state.adminTokenSource === "session" ? "（同页）" : "";
    text.textContent =
      status === "ok" ? `已连接${suffix}` : status === "err" ? `未授权${suffix}` : `Token 已设置${suffix}`;
  }

  function requireToken() {
    if (
      state.adminToken &&
      state.adminTokenSource !== "memory" &&
      typeof state.adminTokenExpiresAt === "number" &&
      nowMs() > state.adminTokenExpiresAt
    ) {
      const source = state.adminTokenSource;
      state.adminToken = "";
      state.adminTokenSource = "memory";
      state.adminTokenExpiresAt = null;
      clearAdminTokenStorage();
      updateConnectionUi("warn");
      logClient("warning", "Admin Token 已过期并被清理", { source });
      throw new Error("Admin Token 已过期，请重新连接");
    }
    if (!state.adminToken) {
      throw new Error("请先在右上角「连接」里设置 Admin Token（FCAM_ADMIN_TOKEN）");
    }
  }

  async function api(method, path, body) {
    requireToken();
    const headers = { Accept: "application/json", Authorization: `Bearer ${state.adminToken}` };
    const opts = { method, headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }

    const t0 = typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : nowMs();
    const resp = await fetch(path, opts);
    const t1 = typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : nowMs();
    const ct = resp.headers.get("content-type") || "";
    const isJson = ct.includes("application/json");
    const payload = isJson ? await resp.json() : await resp.text();

    if (!resp.ok) {
      const err = isJson ? payload : { error: { code: "HTTP_ERROR", message: String(payload) } };
      const code = err?.error?.code || "HTTP_ERROR";
      const message = err?.error?.message || "请求失败";
      const details = err?.error?.details || null;
      const e = new Error(`${code}: ${message}`);
      e.code = code;
      e.details = details;
      logClient("error", `API ${method} ${path} -> ${resp.status}`, {
        code,
        message,
        ms: Math.round(t1 - t0),
        details,
      });
      throw e;
    }

    logClient("info", `API ${method} ${path} -> ${resp.status}`, { ms: Math.round(t1 - t0) });
    return payload;
  }

  async function apiWithBearer(method, path, bearerToken, body) {
    if (!bearerToken) throw new Error("缺少 Bearer Token");

    const headers = { Accept: "application/json", Authorization: `Bearer ${bearerToken}` };
    const opts = { method, headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }

    const t0 = typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : nowMs();
    const resp = await fetch(path, opts);
    const t1 = typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : nowMs();
    const ct = resp.headers.get("content-type") || "";
    const isJson = ct.includes("application/json");
    const payload = isJson ? await resp.json() : await resp.text();

    if (!resp.ok) {
      const err = isJson ? payload : { error: { code: "HTTP_ERROR", message: String(payload) } };
      const code = err?.error?.code || "HTTP_ERROR";
      const message = err?.error?.message || "请求失败";
      const details = err?.error?.details || null;
      const e = new Error(`${code}: ${message}`);
      e.code = code;
      e.details = details;
      logClient("error", `API ${method} ${path} -> ${resp.status}`, {
        code,
        message,
        ms: Math.round(t1 - t0),
        details,
      });
      throw e;
    }

    logClient("info", `API ${method} ${path} -> ${resp.status}`, { ms: Math.round(t1 - t0) });
    return payload;
  }

  function loadUiSettings() {
    const raw = storageGet(window.localStorage, UI_SETTINGS_KEY);
    const parsed = raw ? safeJsonParse(raw) : null;
    if (!parsed || typeof parsed !== "object") return {};
    return parsed;
  }

  function saveUiSettings(partial) {
    const prev = loadUiSettings();
    const next = { ...prev, ...(partial || {}) };
    storageSet(window.localStorage, UI_SETTINGS_KEY, JSON.stringify(next));
    return next;
  }

  function clearAdminTokenStorage() {
    storageRemove(window.sessionStorage, ADMIN_TOKEN_STORAGE_KEY);
    storageRemove(window.localStorage, ADMIN_TOKEN_STORAGE_KEY);
  }

  function loadAdminTokenFromStorage() {
    const sessionRaw = storageGet(window.sessionStorage, ADMIN_TOKEN_STORAGE_KEY);
    if (sessionRaw) {
      const parsed = safeJsonParse(sessionRaw);
      const token = parsed?.token;
      const expiresAt = parsed?.expiresAt;
      if (typeof token === "string" && token.trim()) {
        if (typeof expiresAt === "number" && nowMs() > expiresAt) {
          storageRemove(window.sessionStorage, ADMIN_TOKEN_STORAGE_KEY);
          logClient("warning", "已清理过期 Admin Token（sessionStorage）");
        } else {
          return { token: token.trim(), source: "session", expiresAt: typeof expiresAt === "number" ? expiresAt : null };
        }
      }
    }

    const localRaw = storageGet(window.localStorage, ADMIN_TOKEN_STORAGE_KEY);
    if (localRaw) {
      const parsed = safeJsonParse(localRaw);
      const token = parsed?.token;
      const expiresAt = parsed?.expiresAt;
      if (typeof token === "string" && token.trim()) {
        if (typeof expiresAt === "number" && nowMs() > expiresAt) {
          storageRemove(window.localStorage, ADMIN_TOKEN_STORAGE_KEY);
          logClient("warning", "已清理过期 Admin Token（localStorage）");
        } else {
          return { token: token.trim(), source: "local", expiresAt: typeof expiresAt === "number" ? expiresAt : null };
        }
      }
    }

    return null;
  }

  function persistAdminToken(token, { mode, hours }) {
    clearAdminTokenStorage();

    if (!token) return { source: "memory", expiresAt: null };
    if (mode === "memory") return { source: "memory", expiresAt: null };

    const ttlHours = Number(hours);
    if (!Number.isFinite(ttlHours) || ttlHours <= 0) {
      throw new Error("过期时间（小时）必须为正数");
    }

    const expiresAt = nowMs() + Math.floor(ttlHours * 60 * 60 * 1000);
    const record = { token, savedAt: nowMs(), expiresAt };

    const store = mode === "local" ? window.localStorage : window.sessionStorage;
    const ok = storageSet(store, ADMIN_TOKEN_STORAGE_KEY, JSON.stringify(record));
    if (!ok) throw new Error("浏览器存储失败（可能被禁用或空间不足）");
    return { source: mode, expiresAt };
  }

  function buildQuery(params) {
    const usp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (!hasValue(v)) continue;
      usp.set(k, String(v));
    }
    const raw = usp.toString();
    return raw ? `?${raw}` : "";
  }

  function badgeClassFromStatus(value) {
    const v = String(value || "").toLowerCase();
    if (v === "active") return "ok";
    if (v === "cooling" || v === "quota_exceeded") return "warn";
    if (v === "failed" || v === "disabled") return "err";
    return "";
  }

  function setActiveView(view) {
    state.currentView = view;
    qsa(".view[data-view]").forEach((sec) => {
      const v = sec.getAttribute("data-view");
      sec.hidden = v !== view;
    });
    qsa(".nav-item[data-view]").forEach((btn) => {
      const v = btn.getAttribute("data-view");
      if (v === view) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    });

    const titles = {
      dashboard: "概览",
      keys: "API Keys",
      clients: "Clients",
      logs: "请求日志",
      audit: "审计日志",
      help: "帮助",
    };
    setText("viewTitle", titles[view] || view);
  }

  async function testConnection({ silent = false } = {}) {
    try {
      setText("connOrigin", window.location.origin);
      const stats = await api("GET", "/admin/stats");
      updateConnectionUi("ok");
      if (!silent) {
        setHidden("connTestOutput", false);
        qs("#connTestOutput").textContent = formatJson(stats);
      }
      const dashboardOutput = qs("#dashboardOutput");
      if (dashboardOutput) dashboardOutput.textContent = formatJson(stats);
      if (!silent) toast("success", "连接成功", "已通过 /admin/stats 验证 Admin Token");
      return true;
    } catch (e) {
      updateConnectionUi("err");
      if (!silent) {
        setHidden("connTestOutput", false);
        qs("#connTestOutput").textContent = `${e.message || e}`;
        toast("error", "连接失败", e.message || String(e));
      }
      return false;
    }
  }

  function openDialog(id) {
    const dlg = qs(`#${id}`);
    if (!dlg) return;
    if (typeof dlg.showModal === "function") {
      dlg.showModal();
    } else {
      dlg.hidden = false;
    }
  }

  function closeDialog(id) {
    const dlg = qs(`#${id}`);
    if (!dlg) return;
    if (typeof dlg.close === "function") {
      dlg.close();
    } else {
      dlg.hidden = true;
    }
  }

  function parseNum(raw, { allowEmpty = false } = {}) {
    const s = String(raw ?? "").trim();
    if (!s) {
      if (allowEmpty) return undefined;
      throw new Error("数字字段不能为空");
    }
    const n = Number(s);
    if (!Number.isFinite(n)) throw new Error(`数字字段非法: ${s}`);
    return n;
  }

  function parseOptionalDatetimeLocal(value) {
    const raw = String(value || "").trim();
    if (!raw) return undefined;
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) throw new Error("日期时间格式非法");
    return d.toISOString();
  }

  function clearElementChildren(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function updateKeysBulkUi() {
    const btn = qs("#keysPurgeSelected");
    if (btn) btn.disabled = state.selectedKeyIds.size === 0;

    const master = qs("#keysSelectAll");
    if (!master) return;
    const visibleIds = state.keysFiltered.map((k) => String(k.id));
    if (!visibleIds.length) {
      master.checked = false;
      master.indeterminate = false;
      return;
    }

    let selectedVisible = 0;
    for (const id of visibleIds) if (state.selectedKeyIds.has(id)) selectedVisible += 1;

    if (selectedVisible === 0) {
      master.checked = false;
      master.indeterminate = false;
      return;
    }
    if (selectedVisible === visibleIds.length) {
      master.checked = true;
      master.indeterminate = false;
      return;
    }

    master.checked = false;
    master.indeterminate = true;
  }

  function renderKeysTable() {
    const tbody = qs("#keysTbody");
    if (!tbody) return;
    clearElementChildren(tbody);

    for (const item of state.keysFiltered) {
      const tr = document.createElement("tr");
      tr.dataset.id = String(item.id);
      tr.dataset.selected = String(item.id) === String(state.selectedKeyId);
      tr.setAttribute("data-selected", tr.dataset.selected);

      const tdCheck = document.createElement("td");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = state.selectedKeyIds.has(String(item.id));
      cb.addEventListener("click", (ev) => ev.stopPropagation());
      cb.addEventListener("change", () => {
        const id = String(item.id);
        if (cb.checked) state.selectedKeyIds.add(id);
        else state.selectedKeyIds.delete(id);
        updateKeysBulkUi();
      });
      tdCheck.appendChild(cb);
      tr.appendChild(tdCheck);

      const cols = [
        item.id,
        item.name || "",
        item.account_username || "",
        item.api_key_masked || "",
        item.status || "",
        item.is_active,
        `${item.daily_usage ?? 0}/${item.daily_quota ?? 0}`,
        `${item.current_concurrent ?? 0}/${item.max_concurrent ?? 0}`,
        item.rate_limit_per_min ?? "",
        item.cooldown_until || "",
        item.account_verified_at || "",
        item.last_used_at || "",
      ];
      for (const c of cols) {
        const td = document.createElement("td");
        td.textContent = c == null ? "" : String(c);
        tr.appendChild(td);
      }

      tr.addEventListener("click", () => selectKey(item.id));
      tbody.appendChild(tr);
    }

    setText("keysCount", String(state.keysFiltered.length));
    updateKeysBulkUi();
  }

  function applyKeysFilter() {
    const q = String(qs("#keysSearch")?.value || "").trim().toLowerCase();
    if (!q) {
      state.keysFiltered = state.keys.slice();
      renderKeysTable();
      return;
    }
    state.keysFiltered = state.keys.filter((k) => {
      const hay = [
        String(k.id),
        k.name || "",
        k.account_username || "",
        k.api_key_masked || "",
        k.status || "",
        String(k.is_active),
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
    renderKeysTable();
  }

  async function loadKeys({ silent = false } = {}) {
    try {
      const body = await api("GET", "/admin/keys");
      state.keys = Array.isArray(body.items) ? body.items : [];
      state.selectedKeyIds = new Set();
      applyKeysFilter();
      if (!silent) toast("success", "Keys 已刷新", `共 ${state.keys.length} 条`);
    } catch (e) {
      toast("error", "刷新 Keys 失败", e.message || String(e));
    }
  }

  function selectKey(id) {
    state.selectedKeyId = id;
    renderKeysTable();

    const item = state.keys.find((k) => String(k.id) === String(id));
    if (!item) return;

    setHidden("keyEmpty", true);
    setHidden("keyEditForm", false);
    setHidden("keyOpOutput", true);

    qs("#keyEditId").value = String(item.id);
    qs("#keyEditMasked").value = item.api_key_masked || "";
    const accountEl = qs("#keyEditAccount");
    if (accountEl) accountEl.value = item.account_username || "";
    const verifiedEl = qs("#keyEditVerified");
    if (verifiedEl) verifiedEl.value = item.account_verified_at || "";
    qs("#keyEditName").value = item.name || "";
    qs("#keyEditPlan").value = item.plan_type || "";
    qs("#keyEditQuota").value = item.daily_quota ?? 0;
    qs("#keyEditConc").value = item.max_concurrent ?? 0;
    qs("#keyEditRpm").value = item.rate_limit_per_min ?? 0;
    qs("#keyEditActive").value = item.is_active ? "true" : "false";

    const badge = qs("#keyStatusBadge");
    if (badge) {
      const cls = badgeClassFromStatus(item.status);
      badge.className = `badge ${cls}`.trim();
      badge.textContent = "";
      const dot = document.createElement("span");
      dot.className = "dot";
      badge.appendChild(dot);
      badge.appendChild(
        document.createTextNode(`${item.status || "unknown"}${item.is_active ? "" : " (inactive)"}`)
      );
    }
  }

  async function saveKey() {
    try {
      const id = parseNum(qs("#keyEditId").value);
      const payload = {
        name: String(qs("#keyEditName").value || "").trim() || null,
        plan_type: String(qs("#keyEditPlan").value || "").trim() || null,
        daily_quota: parseNum(qs("#keyEditQuota").value),
        max_concurrent: parseNum(qs("#keyEditConc").value),
        rate_limit_per_min: parseNum(qs("#keyEditRpm").value),
        is_active: qs("#keyEditActive").value === "true",
      };

      const rotateApiKeyEl = qs("#keyEditRotateApiKey");
      const rotateApiKey = String(rotateApiKeyEl?.value || "").trim();
      if (rotateApiKey) payload.api_key = rotateApiKey;

      const body = await api("PUT", `/admin/keys/${id}`, payload);
      setHidden("keyOpOutput", false);
      qs("#keyOpOutput").textContent = formatJson(body);
      toast("success", "Key 已更新", `key_id=${id}`);
      if (rotateApiKeyEl) rotateApiKeyEl.value = "";
      await loadKeys({ silent: true });
      selectKey(id);
    } catch (e) {
      toast("error", "更新 Key 失败", e.message || String(e));
    }
  }

  async function toggleKeyActive() {
    try {
      const id = parseNum(qs("#keyEditId").value);
      const item = state.keys.find((k) => String(k.id) === String(id));
      if (!item) throw new Error("未找到 Key");

      const nextActive = !Boolean(item.is_active);
      const body = await api("PUT", `/admin/keys/${id}`, { is_active: nextActive });
      setHidden("keyOpOutput", false);
      qs("#keyOpOutput").textContent = formatJson(body);
      toast("success", nextActive ? "Key 已启用" : "Key 已禁用", `key_id=${id}`);
      await loadKeys({ silent: true });
      selectKey(id);
    } catch (e) {
      toast("error", "切换启用状态失败", e.message || String(e));
    }
  }

  async function createKey() {
    try {
      const payload = {
        api_key: String(qs("#keyCreateApiKey").value || "").trim(),
        name: String(qs("#keyCreateName").value || "").trim() || null,
        plan_type: String(qs("#keyCreatePlan").value || "free").trim() || "free",
        daily_quota: parseNum(qs("#keyCreateQuota").value),
        max_concurrent: parseNum(qs("#keyCreateConc").value),
        rate_limit_per_min: parseNum(qs("#keyCreateRpm").value),
        is_active: qs("#keyCreateActive").value === "true",
      };

      const body = await api("POST", "/admin/keys", payload);
      setHidden("keyCreateOutput", false);
      qs("#keyCreateOutput").textContent = formatJson(body);
      toast("success", "Key 创建成功", `key_id=${body.id}`);
      qs("#keyCreateApiKey").value = "";
      await loadKeys({ silent: true });
      selectKey(body.id);
      closeDialog("dlgKeyCreate");
    } catch (e) {
      toast("error", "创建 Key 失败", e.message || String(e));
    }
  }

  async function importKeysText() {
    try {
      const text = String(qs("#keyImportText")?.value || "").trim();
      if (!text) throw new Error("导入内容不能为空");

      const body = await api("POST", "/admin/keys/import-text", { text });
      setHidden("keyImportOutput", false);
      qs("#keyImportOutput").textContent = formatJson(body);
      toast(
        "success",
        "导入完成",
        `created=${body.created} updated=${body.updated} skipped=${body.skipped} failed=${body.failed}`
      );
      await loadKeys({ silent: true });
      closeDialog("dlgKeyImport");
    } catch (e) {
      toast("error", "导入失败", e.message || String(e));
    }
  }

  async function testKey() {
    try {
      const id = parseNum(qs("#keyEditId").value);
      const testUrl = String(qs("#keyTestUrl")?.value || "").trim() || DEFAULT_KEY_TEST_URL;
      const body = await api("POST", `/admin/keys/${id}/test`, { mode: "scrape", test_url: testUrl });
      setHidden("keyOpOutput", false);
      qs("#keyOpOutput").textContent = formatJson(body);
      toast("success", "Key 测试完成", `key_id=${id} ok=${body.ok}`);
      await loadKeys({ silent: true });
      selectKey(id);
    } catch (e) {
      toast("error", "测试 Key 失败", e.message || String(e));
    }
  }

  async function softDeleteKey() {
    try {
      const id = parseNum(qs("#keyEditId").value);
      if (!window.confirm(`确认禁用 key_id=${id} ?`)) return;
      await api("DELETE", `/admin/keys/${id}`);
      toast("success", "Key 已禁用", `key_id=${id}`);
      await loadKeys({ silent: true });
      state.selectedKeyId = null;
      setHidden("keyEmpty", false);
      setHidden("keyEditForm", true);
    } catch (e) {
      toast("error", "禁用 Key 失败", e.message || String(e));
    }
  }

  async function purgeKey() {
    try {
      const id = parseNum(qs("#keyEditId").value);
      if (!window.confirm(`确认从数据库永久删除 key_id=${id} ? 此操作不可恢复。`)) return;
      await api("DELETE", `/admin/keys/${id}/purge`);
      toast("success", "Key 已删除", `key_id=${id}`);
      state.selectedKeyIds.delete(String(id));
      await loadKeys({ silent: true });
      state.selectedKeyId = null;
      setHidden("keyEmpty", false);
      setHidden("keyEditForm", true);
    } catch (e) {
      toast("error", "删除 Key 失败", e.message || String(e));
    }
  }

  async function purgeSelectedKeys() {
    const ids = Array.from(state.selectedKeyIds);
    if (!ids.length) return;

    if (!window.confirm(`确认从数据库永久删除选中的 ${ids.length} 条 Key ? 此操作不可恢复。`)) return;

    let deleted = 0;
    const failed = [];
    for (const sid of ids) {
      const id = Number(sid);
      if (!Number.isFinite(id)) continue;
      try {
        await api("DELETE", `/admin/keys/${id}/purge`);
        deleted += 1;
      } catch (e) {
        failed.push({ id, code: e.code || "ERROR", message: e.message || String(e) });
      }
    }

    if (failed.length) {
      logClient("error", "批量删除 Key 部分失败", { deleted, failed });
      toast("warning", "批量删除完成", `deleted=${deleted} failed=${failed.length}`);
    } else {
      toast("success", "批量删除完成", `deleted=${deleted}`);
    }

    state.selectedKeyIds = new Set();
    await loadKeys({ silent: true });
    state.selectedKeyId = null;
    setHidden("keyEmpty", false);
    setHidden("keyEditForm", true);
  }

  async function resetKeysQuota() {
    try {
      if (!window.confirm("确认重置所有 Key 的今日配额计数？")) return;
      const body = await api("POST", "/admin/keys/reset-quota");
      toast("success", "配额已重置", `affected_keys=${body.affected_keys}`);
      await loadKeys({ silent: true });
    } catch (e) {
      toast("error", "重置配额失败", e.message || String(e));
    }
  }

  function renderClientsTable() {
    const tbody = qs("#clientsTbody");
    if (!tbody) return;
    clearElementChildren(tbody);

    for (const item of state.clientsFiltered) {
      const tr = document.createElement("tr");
      tr.dataset.id = String(item.id);
      tr.dataset.selected = String(item.id) === String(state.selectedClientId);
      tr.setAttribute("data-selected", tr.dataset.selected);

      const cols = [
        item.id,
        item.name || "",
        item.is_active,
        `${item.daily_usage ?? 0}/${item.daily_quota ?? ""}`,
        item.rate_limit_per_min ?? "",
        item.max_concurrent ?? "",
        item.created_at || "",
        item.last_used_at || "",
      ];
      for (const c of cols) {
        const td = document.createElement("td");
        td.textContent = c == null ? "" : String(c);
        tr.appendChild(td);
      }

      tr.addEventListener("click", () => selectClient(item.id));
      tbody.appendChild(tr);
    }

    setText("clientsCount", String(state.clientsFiltered.length));
  }

  function applyClientsFilter() {
    const q = String(qs("#clientsSearch")?.value || "").trim().toLowerCase();
    if (!q) {
      state.clientsFiltered = state.clients.slice();
      renderClientsTable();
      return;
    }
    state.clientsFiltered = state.clients.filter((c) => {
      const hay = [String(c.id), c.name || "", String(c.is_active)].join(" ").toLowerCase();
      return hay.includes(q);
    });
    renderClientsTable();
  }

  async function loadClients() {
    try {
      const body = await api("GET", "/admin/clients");
      state.clients = Array.isArray(body.items) ? body.items : [];
      applyClientsFilter();
      toast("success", "Clients 已刷新", `共 ${state.clients.length} 条`);
    } catch (e) {
      toast("error", "刷新 Clients 失败", e.message || String(e));
    }
  }

  function selectClient(id) {
    state.selectedClientId = id;
    renderClientsTable();

    const item = state.clients.find((c) => String(c.id) === String(id));
    if (!item) return;

    setHidden("clientEmpty", true);
    setHidden("clientEditForm", false);
    setHidden("clientOpOutput", true);

    qs("#clientEditId").value = String(item.id);
    qs("#clientEditName").value = item.name || "";
    qs("#clientEditQuota").value = item.daily_quota ?? "";
    qs("#clientEditQuotaClear").checked = false;
    qs("#clientEditRpm").value = item.rate_limit_per_min ?? 60;
    qs("#clientEditConc").value = item.max_concurrent ?? 10;
    qs("#clientEditActive").value = item.is_active ? "true" : "false";
  }

  async function saveClient() {
    try {
      const id = parseNum(qs("#clientEditId").value);

      const payload = {
        rate_limit_per_min: parseNum(qs("#clientEditRpm").value),
        max_concurrent: parseNum(qs("#clientEditConc").value),
        is_active: qs("#clientEditActive").value === "true",
      };

      const clearQuota = Boolean(qs("#clientEditQuotaClear").checked);
      if (clearQuota) {
        payload.daily_quota = null;
      } else {
        const raw = String(qs("#clientEditQuota").value || "").trim();
        if (raw) payload.daily_quota = parseNum(raw);
      }

      const body = await api("PUT", `/admin/clients/${id}`, payload);
      setHidden("clientOpOutput", false);
      qs("#clientOpOutput").textContent = formatJson(body);
      toast("success", "Client 已更新", `client_id=${id}`);
      await loadClients();
      selectClient(id);
    } catch (e) {
      toast("error", "更新 Client 失败", e.message || String(e));
    }
  }

  async function createClient() {
    try {
      const name = String(qs("#clientCreateName").value || "").trim();
      if (!name) throw new Error("client.name 必填");

      const quotaRaw = String(qs("#clientCreateQuota").value || "").trim();
      const payload = {
        name,
        daily_quota: quotaRaw ? parseNum(quotaRaw) : null,
        rate_limit_per_min: parseNum(qs("#clientCreateRpm").value),
        max_concurrent: parseNum(qs("#clientCreateConc").value),
        is_active: qs("#clientCreateActive").value === "true",
      };

      const body = await api("POST", "/admin/clients", payload);
      setHidden("clientCreateOutput", false);
      qs("#clientCreateOutput").textContent = formatJson(body);
      toast("success", "Client 创建成功", "token 仅返回一次，请立即复制保存");

      const token = body?.token;
      const dpTokenEl = qs("#dpClientToken");
      if (dpTokenEl && typeof token === "string" && token.trim()) {
        dpTokenEl.value = token.trim();
        toast("info", "已填入数据面自检", "Client Token 已自动写入「数据面自检（/api/scrape）」表单");
      }

      qs("#clientCreateName").value = "";
      qs("#clientCreateQuota").value = "";
      await loadClients();
      if (body?.client?.id) selectClient(body.client.id);
    } catch (e) {
      toast("error", "创建 Client 失败", e.message || String(e));
    }
  }

  async function rotateClientToken() {
    try {
      const id = parseNum(qs("#clientEditId").value);
      if (!window.confirm(`确认 rotate client_id=${id} ? 新 token 仅返回一次。`)) return;
      const body = await api("POST", `/admin/clients/${id}/rotate`);
      setHidden("clientOpOutput", false);
      qs("#clientOpOutput").textContent = formatJson(body);
      toast("success", "Rotate 成功", "新 token 已返回，请立即复制保存");

      const token = body?.token;
      const dpTokenEl = qs("#dpClientToken");
      if (dpTokenEl && typeof token === "string" && token.trim()) {
        dpTokenEl.value = token.trim();
        toast("info", "已填入数据面自检", "Client Token 已自动写入「数据面自检（/api/scrape）」表单");
      }
    } catch (e) {
      toast("error", "Rotate 失败", e.message || String(e));
    }
  }

  async function softDeleteClient() {
    try {
      const id = parseNum(qs("#clientEditId").value);
      if (!window.confirm(`确认禁用 client_id=${id} ?`)) return;
      await api("DELETE", `/admin/clients/${id}`);
      toast("success", "Client 已禁用", `client_id=${id}`);
      await loadClients();
      state.selectedClientId = null;
      setHidden("clientEmpty", false);
      setHidden("clientEditForm", true);
    } catch (e) {
      toast("error", "禁用 Client 失败", e.message || String(e));
    }
  }

  async function purgeClient() {
    try {
      const id = parseNum(qs("#clientEditId").value);
      if (!window.confirm(`确认从数据库永久删除 client_id=${id} ? 此操作不可恢复。`)) return;
      await api("DELETE", `/admin/clients/${id}/purge`);
      toast("success", "Client 已删除", `client_id=${id}`);
      await loadClients();
      state.selectedClientId = null;
      setHidden("clientEmpty", false);
      setHidden("clientEditForm", true);
    } catch (e) {
      toast("error", "删除 Client 失败", e.message || String(e));
    }
  }

  async function loadDashboard() {
    try {
      const body = await api("GET", "/admin/stats");
      setHidden("dashboardOutput", false);
      qs("#dashboardOutput").textContent = formatJson(body);
    } catch (e) {
      setHidden("dashboardOutput", false);
      qs("#dashboardOutput").textContent = `${e.message || e}`;
    }
  }

  async function runDataPlaneScrapeTest() {
    const out = qs("#dpTestOutput");
    if (out) {
      out.textContent = "";
      setHidden("dpTestOutput", true);
    }

    try {
      const token = String(qs("#dpClientToken")?.value || "").trim();
      if (!token) throw new Error("请先输入 Client Token");

      const testUrl = String(qs("#dpTestUrl")?.value || "").trim() || DEFAULT_KEY_TEST_URL;
      const body = await apiWithBearer("POST", "/api/scrape", token, { url: testUrl });
      setHidden("dpTestOutput", false);
      qs("#dpTestOutput").textContent = formatJson(body);
      toast("success", "数据面自检完成", "已调用 /api/scrape（端到端验证）");
    } catch (e) {
      setHidden("dpTestOutput", false);
      qs("#dpTestOutput").textContent = `${e.message || e}`;
      if (e && e.code === "CLIENT_UNAUTHORIZED") {
        toast(
          "error",
          "数据面自检失败",
          "CLIENT_UNAUTHORIZED：请粘贴 /admin/clients 创建/rotate 时返回的 token（通常以 fcam_client_ 开头），不要填 Admin Token/Firecrawl Key"
        );
      } else {
        toast("error", "数据面自检失败", e.message || String(e));
      }
    }
  }

  function renderLogsTable() {
    const tbody = qs("#logsTbody");
    if (!tbody) return;
    clearElementChildren(tbody);

    const q = String(qs("#logsSearch")?.value || "").trim().toLowerCase();
    const items = !q
      ? state.logs
      : state.logs.filter((x) => {
          const hay = [
            String(x.id),
            x.created_at || "",
            x.request_id || "",
            x.endpoint || "",
            String(x.status_code ?? ""),
            String(x.client_id ?? ""),
            String(x.api_key_id ?? ""),
            String(x.idempotency_key ?? ""),
            String(x.error_message || ""),
          ]
            .join(" ")
            .toLowerCase();
          return hay.includes(q);
        });

    for (const item of items) {
      const tr = document.createElement("tr");
      tr.dataset.id = String(item.id);
      tr.dataset.selected = String(item.id) === String(state.selectedLogId);
      tr.setAttribute("data-selected", tr.dataset.selected);

      const cols = [
        item.id,
        item.created_at || "",
        item.request_id || "",
        item.client_id ?? "",
        item.api_key_id ?? "",
        item.endpoint || "",
        item.status_code ?? "",
        item.response_time_ms ?? "",
        item.retry_count ?? "",
        item.success ?? "",
        item.error_message || "",
      ];
      for (const c of cols) {
        const td = document.createElement("td");
        td.textContent = c == null ? "" : String(c);
        tr.appendChild(td);
      }

      tr.addEventListener("click", () => selectLog(item.id));
      tbody.appendChild(tr);
    }

    qs("#logsMore").disabled = !state.logsHasMore;
  }

  function selectLog(id) {
    state.selectedLogId = id;
    renderLogsTable();
    const item = state.logs.find((x) => String(x.id) === String(id));
    if (!item) return;
    setHidden("logEmpty", true);
    setHidden("logDetail", false);
    qs("#logDetail").textContent = formatJson(item);
  }

  function readLogsFilters() {
    const limit = parseNum(qs("#logsLimit").value);
    const params = {
      limit,
      from: parseOptionalDatetimeLocal(qs("#logsFrom").value),
      to: parseOptionalDatetimeLocal(qs("#logsTo").value),
      client_id: String(qs("#logsClientId").value || "").trim() || undefined,
      api_key_id: String(qs("#logsApiKeyId").value || "").trim() || undefined,
      endpoint: String(qs("#logsEndpoint").value || "").trim() || undefined,
      status_code: String(qs("#logsStatusCode").value || "").trim() || undefined,
      success: qs("#logsSuccess").value || undefined,
      request_id: String(qs("#logsRequestId").value || "").trim() || undefined,
      idempotency_key: String(qs("#logsIdempotencyKey").value || "").trim() || undefined,
    };
    if (params.success === "any") params.success = undefined;
    return params;
  }

  async function loadLogs({ append = false } = {}) {
    try {
      const params = readLogsFilters();
      if (append && state.logsCursor) params.cursor = state.logsCursor;
      const body = await api("GET", `/admin/logs${buildQuery(params)}`);
      const items = Array.isArray(body.items) ? body.items : [];
      if (append) state.logs = state.logs.concat(items);
      else state.logs = items;
      state.logsCursor = body.next_cursor || null;
      state.logsHasMore = Boolean(body.has_more);
      state.selectedLogId = null;
      setHidden("logEmpty", false);
      setHidden("logDetail", true);
      renderLogsTable();
      toast("success", append ? "日志已追加" : "日志已加载", `items=${items.length}`);
    } catch (e) {
      toast("error", "加载日志失败", e.message || String(e));
    }
  }

  function renderAuditTable() {
    const tbody = qs("#auditTbody");
    if (!tbody) return;
    clearElementChildren(tbody);

    const q = String(qs("#auditSearch")?.value || "").trim().toLowerCase();
    const items = !q
      ? state.auditLogs
      : state.auditLogs.filter((x) => {
          const hay = [
            String(x.id),
            x.created_at || "",
            x.action || "",
            x.resource_type || "",
            String(x.resource_id ?? ""),
            x.actor_type || "",
            String(x.actor_id ?? ""),
            x.ip || "",
            String(x.user_agent || ""),
          ]
            .join(" ")
            .toLowerCase();
          return hay.includes(q);
        });

    for (const item of items) {
      const tr = document.createElement("tr");
      tr.dataset.id = String(item.id);
      tr.dataset.selected = String(item.id) === String(state.selectedAuditId);
      tr.setAttribute("data-selected", tr.dataset.selected);

      const cols = [
        item.id,
        item.created_at || "",
        item.action || "",
        item.resource_type || "",
        item.resource_id || "",
        `${item.actor_type || ""}:${item.actor_id || ""}`,
        item.ip || "",
      ];
      for (const c of cols) {
        const td = document.createElement("td");
        td.textContent = c == null ? "" : String(c);
        tr.appendChild(td);
      }

      tr.addEventListener("click", () => selectAudit(item.id));
      tbody.appendChild(tr);
    }

    qs("#auditMore").disabled = !state.auditHasMore;
  }

  function selectAudit(id) {
    state.selectedAuditId = id;
    renderAuditTable();
    const item = state.auditLogs.find((x) => String(x.id) === String(id));
    if (!item) return;
    setHidden("auditEmpty", true);
    setHidden("auditDetail", false);
    qs("#auditDetail").textContent = formatJson(item);
  }

  function readAuditFilters() {
    const limit = parseNum(qs("#auditLimit").value);
    const params = {
      limit,
      from: parseOptionalDatetimeLocal(qs("#auditFrom").value),
      to: parseOptionalDatetimeLocal(qs("#auditTo").value),
      actor_type: String(qs("#auditActorType").value || "").trim() || undefined,
      action: String(qs("#auditAction").value || "").trim() || undefined,
      resource_type: String(qs("#auditResourceType").value || "").trim() || undefined,
      resource_id: String(qs("#auditResourceId").value || "").trim() || undefined,
    };
    return params;
  }

  async function loadAudit({ append = false } = {}) {
    try {
      const params = readAuditFilters();
      if (append && state.auditCursor) params.cursor = state.auditCursor;
      const body = await api("GET", `/admin/audit-logs${buildQuery(params)}`);
      const items = Array.isArray(body.items) ? body.items : [];
      if (append) state.auditLogs = state.auditLogs.concat(items);
      else state.auditLogs = items;
      state.auditCursor = body.next_cursor || null;
      state.auditHasMore = Boolean(body.has_more);
      state.selectedAuditId = null;
      setHidden("auditEmpty", false);
      setHidden("auditDetail", true);
      renderAuditTable();
      toast("success", append ? "审计日志已追加" : "审计日志已加载", `items=${items.length}`);
    } catch (e) {
      toast("error", "加载审计日志失败", e.message || String(e));
    }
  }

  function wire() {
    setText("appOrigin", window.location.origin);
    updateConnectionUi("warn");

    const uiSettings = loadUiSettings();
    const rememberModeEl = qs("#connRememberMode");
    const rememberHoursEl = qs("#connRememberHours");
    if (rememberModeEl) rememberModeEl.value = uiSettings.connRememberMode || "session";
    if (rememberHoursEl) rememberHoursEl.value = String(uiSettings.connRememberHours || 8);

    const applyRememberUiState = () => {
      if (!rememberModeEl || !rememberHoursEl) return;
      const mode = rememberModeEl.value || "session";
      rememberHoursEl.disabled = mode === "memory";
    };
    applyRememberUiState();

    rememberModeEl?.addEventListener("change", () => {
      applyRememberUiState();
      saveUiSettings({ connRememberMode: rememberModeEl.value });
    });
    rememberHoursEl?.addEventListener("change", () => {
      const v = Number(rememberHoursEl.value);
      if (Number.isFinite(v) && v > 0) saveUiSettings({ connRememberHours: v });
    });

    const keyTestUrlEl = qs("#keyTestUrl");
    if (keyTestUrlEl) {
      keyTestUrlEl.value = uiSettings.keyTestUrl || DEFAULT_KEY_TEST_URL;
      keyTestUrlEl.addEventListener("change", () => saveUiSettings({ keyTestUrl: String(keyTestUrlEl.value || "").trim() }));
    }

    const dpTestUrlEl = qs("#dpTestUrl");
    if (dpTestUrlEl) {
      dpTestUrlEl.value = uiSettings.dpTestUrl || uiSettings.keyTestUrl || DEFAULT_KEY_TEST_URL;
      dpTestUrlEl.addEventListener("change", () => saveUiSettings({ dpTestUrl: String(dpTestUrlEl.value || "").trim() }));
    }

    const persisted = loadAdminTokenFromStorage();
    if (persisted?.token) {
      state.adminToken = persisted.token;
      state.adminTokenSource = persisted.source || "memory";
      state.adminTokenExpiresAt = persisted.expiresAt;
      const tokenEl = qs("#connToken");
      if (tokenEl) tokenEl.value = state.adminToken;
      updateConnectionUi("warn");
      logClient("info", `已加载 Admin Token（${state.adminTokenSource}）`, {
        expires_at: state.adminTokenExpiresAt ? new Date(state.adminTokenExpiresAt).toISOString() : null,
      });
    }

    qsa(".nav-item[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = btn.getAttribute("data-view");
        if (!v) return;
        setActiveView(v);
        if (v === "dashboard") loadDashboard();
      });
    });

    qs("#btnOpenConn").addEventListener("click", () => openDialog("dlgConn"));
    qs("#btnDocs").addEventListener("click", () => window.open("/docs", "_blank", "noopener,noreferrer"));
    qs("#btnQuickRefresh").addEventListener("click", () => {
      const v = state.currentView;
      if (v === "dashboard") loadDashboard();
      if (v === "keys") loadKeys();
      if (v === "clients") loadClients();
      if (v === "logs") loadLogs({ append: false });
      if (v === "audit") loadAudit({ append: false });
    });

    qs("#connSave").addEventListener("click", async () => {
      state.adminToken = String(qs("#connToken").value || "").trim();
      state.adminTokenSource = "memory";
      state.adminTokenExpiresAt = null;
      updateConnectionUi("warn");
      if (!state.adminToken) {
        clearAdminTokenStorage();
        toast("warning", "Token 已清空", "请重新输入 Admin Token");
        return;
      }

      const mode = String(qs("#connRememberMode")?.value || "session");
      const hours = Number(qs("#connRememberHours")?.value || 0);
      saveUiSettings({ connRememberMode: mode, connRememberHours: hours });

      try {
        const meta = persistAdminToken(state.adminToken, { mode, hours });
        state.adminTokenSource = meta.source || "memory";
        state.adminTokenExpiresAt = meta.expiresAt || null;
        updateConnectionUi("warn");
        logClient("info", "Admin Token 已保存", {
          mode: state.adminTokenSource,
          expires_at: state.adminTokenExpiresAt ? new Date(state.adminTokenExpiresAt).toISOString() : null,
        });
      } catch (e) {
        toast("error", "保存失败", e.message || String(e));
        return;
      }

      await testConnection({ silent: false });
    });

    qs("#connClear").addEventListener("click", () => {
      state.adminToken = "";
      state.adminTokenSource = "memory";
      state.adminTokenExpiresAt = null;
      qs("#connToken").value = "";
      clearAdminTokenStorage();
      updateConnectionUi("warn");
      setHidden("connTestOutput", true);
      toast("warning", "已清空 Token", "已同时清理浏览器持久化存储");
    });

    qs("#connTest").addEventListener("click", () => testConnection({ silent: false }));
    qs("#connClose").addEventListener("click", () => closeDialog("dlgConn"));

    qs("#clientLogClear")?.addEventListener("click", () => {
      clientLogLines.length = 0;
      renderClientLog();
      toast("success", "已清空前端日志", "");
    });

    qs("#clientLogCopy")?.addEventListener("click", async () => {
      try {
        const text = clientLogLines.join("\n");
        if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement("textarea");
          ta.value = text;
          ta.setAttribute("readonly", "true");
          ta.style.position = "fixed";
          ta.style.left = "-9999px";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        toast("success", "已复制前端日志", `lines=${clientLogLines.length}`);
      } catch (e) {
        toast("error", "复制失败", e.message || String(e));
      }
    });

    qs("#keysRefresh").addEventListener("click", () => loadKeys());
    qs("#keysResetQuota").addEventListener("click", () => resetKeysQuota());
    qs("#keysSearch").addEventListener("input", () => applyKeysFilter());
    qs("#keysSelectAll")?.addEventListener("change", () => {
      const master = qs("#keysSelectAll");
      const checked = Boolean(master?.checked);
      for (const k of state.keysFiltered) {
        const id = String(k.id);
        if (checked) state.selectedKeyIds.add(id);
        else state.selectedKeyIds.delete(id);
      }
      renderKeysTable();
    });
    qs("#keysOpenCreate")?.addEventListener("click", () => {
      setHidden("keyCreateOutput", true);
      openDialog("dlgKeyCreate");
    });
    qs("#keysOpenImport")?.addEventListener("click", () => {
      setHidden("keyImportOutput", true);
      openDialog("dlgKeyImport");
    });
    qs("#keysPurgeSelected")?.addEventListener("click", () => purgeSelectedKeys());
    qs("#dlgKeyCreateClose")?.addEventListener("click", () => closeDialog("dlgKeyCreate"));
    qs("#dlgKeyImportClose")?.addEventListener("click", () => closeDialog("dlgKeyImport"));
    qs("#keySave").addEventListener("click", () => saveKey());
    qs("#keyToggleActive")?.addEventListener("click", () => toggleKeyActive());
    qs("#keyTest").addEventListener("click", () => testKey());
    qs("#keySoftDelete").addEventListener("click", () => softDeleteKey());
    qs("#keyPurge")?.addEventListener("click", () => purgeKey());
    qs("#keyCreate").addEventListener("click", () => createKey());
    const importBtn = qs("#keyImportRun");
    if (importBtn) importBtn.addEventListener("click", () => importKeysText());

    qs("#clientsRefresh").addEventListener("click", () => loadClients());
    qs("#clientsSearch").addEventListener("input", () => applyClientsFilter());
    qs("#clientsOpenCreate")?.addEventListener("click", () => {
      setHidden("clientCreateOutput", true);
      openDialog("dlgClientCreate");
    });
    qs("#dlgClientCreateClose")?.addEventListener("click", () => closeDialog("dlgClientCreate"));
    qs("#clientSave").addEventListener("click", () => saveClient());
    qs("#clientRotate").addEventListener("click", () => rotateClientToken());
    qs("#clientSoftDelete").addEventListener("click", () => softDeleteClient());
    qs("#clientPurge")?.addEventListener("click", () => purgeClient());
    qs("#clientCreate").addEventListener("click", () => createClient());

    qs("#logsLoad").addEventListener("click", () => loadLogs({ append: false }));
    qs("#logsMore").addEventListener("click", () => loadLogs({ append: true }));
    qs("#logsSearch")?.addEventListener("input", () => renderLogsTable());

    qs("#auditLoad").addEventListener("click", () => loadAudit({ append: false }));
    qs("#auditMore").addEventListener("click", () => loadAudit({ append: true }));
    qs("#auditSearch")?.addEventListener("input", () => renderAuditTable());

    qs("#dpTestRun")?.addEventListener("click", () => runDataPlaneScrapeTest());

    setActiveView("dashboard");
    if (state.adminToken) testConnection({ silent: true });
  }

  document.addEventListener("DOMContentLoaded", () => {
    wire();
  });
})();
