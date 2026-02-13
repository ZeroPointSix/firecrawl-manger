import { computed, ref } from "vue";

import { getFcamErrorMessage, http, setAdminToken } from "@/api/http";

const STORAGE_KEY = "fcam_ui2_admin_token_v1";

const initialToken = (() => {
  try {
    return localStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
})();

export const adminToken = ref<string>(initialToken);
export const connectionStatus = ref<"disconnected" | "unknown" | "ok" | "unauthorized" | "error">(
  initialToken ? "unknown" : "disconnected"
);
export const lastConnectionError = ref<string>("");

setAdminToken(initialToken);

export const isConnected = computed(() => connectionStatus.value === "ok");

export async function verifyAdminToken() {
  if (!adminToken.value) {
    connectionStatus.value = "disconnected";
    lastConnectionError.value = "";
    return false;
  }

  try {
    await http.get("/admin/stats");
    connectionStatus.value = "ok";
    lastConnectionError.value = "";
    return true;
  } catch (err: unknown) {
    const status = (err as any)?.response?.status as number | undefined;
    connectionStatus.value = status === 401 ? "unauthorized" : "error";
    lastConnectionError.value = getFcamErrorMessage(err);
    return false;
  }
}

export async function connectAdminToken(token: string, opts: { persist: boolean }) {
  adminToken.value = token.trim();
  setAdminToken(adminToken.value);

  if (opts.persist) {
    try {
      localStorage.setItem(STORAGE_KEY, adminToken.value);
    } catch {
      // ignore
    }
  } else {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }

  connectionStatus.value = adminToken.value ? "unknown" : "disconnected";
  lastConnectionError.value = "";
  await verifyAdminToken();
}

export function disconnectAdminToken() {
  adminToken.value = "";
  setAdminToken("");
  connectionStatus.value = "disconnected";
  lastConnectionError.value = "";
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
