<script setup lang="ts">
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NCheckboxGroup,
  NDataTable,
  NDivider,
  NDropdown,
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NList,
  NListItem,
  NModal,
  NPopover,
  NProgress,
  NSelect,
  NSpace,
  NTag,
  useDialog,
  useMessage,
} from "naive-ui";
import { computed, h, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { getClientCredits, getKeyCredits, refreshAllCredits, type ClientCreditsInfo, type CreditInfo } from "@/api/credits";
import { batchUpdateClients, createClient, fetchClients, rotateClientToken, updateClient, type ClientItem } from "@/api/clients";
import { fetchEncryptionStatus, type EncryptionStatus } from "@/api/dashboard";
import { batchKeys, createKey, fetchKeys, importKeysText, purgeKey, testKey, updateKey, type KeyItem } from "@/api/keys";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken, verifyAdminToken } from "@/state/adminAuth";
import CreditDisplay from "@/components/CreditDisplay.vue";
import CreditTrendChart from "@/components/CreditTrendChart.vue";
import { formatDate, formatRelativeTime, formatTimestamp } from "@/utils/time";

const message = useMessage();
const dialog = useDialog();

const loadingClients = ref(false);
const loadingKeys = ref(false);
const loadingEncryption = ref(false);

const clients = ref<ClientItem[]>([]);
const clientSearch = ref("");
const selectedClientId = ref<number | null>(null);
const checkedClientIds = ref<number[]>([]);
const batchOperating = ref(false);

const keys = ref<KeyItem[]>([]);
const checkedKeyRowKeys = ref<number[]>([]);
const keyPage = ref(1);
const keyPageSize = ref(20);
const keyTotalItems = ref(0);
const keyNameSearch = ref("");
const keyProviderFilter = ref<string | null>(null);
const encryption = ref<EncryptionStatus | null>(null);

const loadingClientCredits = ref(false);
const clientCredits = ref<ClientCreditsInfo | null>(null);
const refreshingCredits = ref(false);

const showCreditsDrawer = ref(false);
const creditsDrawerKey = ref<KeyItem | null>(null);
const creditsDrawerInfo = ref<CreditInfo | null>(null);
const loadingCreditsDrawer = ref(false);

const selectedClient = computed(() => clients.value.find((c) => c.id === selectedClientId.value) || null);
const clientUsagePercentage = computed(() => clientCredits.value?.usage_percentage ?? 0);
const clientUsageStatus = computed(() => {
  const info = clientCredits.value;
  if (!info || !info.total_credits) return "default";
  const remainingPct = (info.total_remaining_credits / info.total_credits) * 100;
  if (remainingPct < 20) return "error";
  if (remainingPct < 50) return "warning";
  return "success";
});

const filteredClients = computed(() => {
  const q = clientSearch.value.trim().toLowerCase();
  if (!q) return clients.value;
  return clients.value.filter((c) => `${c.id} ${c.name}`.toLowerCase().includes(q));
});

const splitWidthStorageKey = "fcam_ui2_clients_pane_width_v1";
const leftWidth = ref(260);
const isResizing = ref(false);
let cleanupResize: (() => void) | null = null;

function clampInt(v: number, min: number, max: number) {
  return Math.min(max, Math.max(min, Math.round(v)));
}

function onResizePointerDown(e: PointerEvent) {
  e.preventDefault();
  if (isResizing.value) return;

  const startX = e.clientX;
  const startWidth = leftWidth.value;
  const minWidth = 120;
  const maxWidth = 520;

  isResizing.value = true;
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";

  const onMove = (ev: PointerEvent) => {
    const next = clampInt(startWidth + (ev.clientX - startX), minWidth, maxWidth);
    leftWidth.value = next;
  };

  const onUp = () => {
    isResizing.value = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    cleanupResize = null;
    try {
      localStorage.setItem(splitWidthStorageKey, String(leftWidth.value));
    } catch {
      // ignore
    }
  };

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp, { once: true });
  cleanupResize = () => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };
}

async function loadClients() {
  if (!adminToken.value) return;
  loadingClients.value = true;
  try {
    clients.value = await fetchClients();
    if (!clients.value.length) {
      selectedClientId.value = null;
      return;
    }

    const currentId = selectedClientId.value;
    const exists = currentId !== null && clients.value.some((c) => c.id === currentId);
    if (!exists) selectedClientId.value = clients.value[0].id;
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    loadingClients.value = false;
  }
}

async function loadKeys() {
  if (!adminToken.value) return;
  if (!selectedClientId.value) {
    keys.value = [];
    keyTotalItems.value = 0;
    return;
  }
  loadingKeys.value = true;
  try {
    const res = await fetchKeys({
      clientId: selectedClientId.value,
      page: keyPage.value,
      pageSize: keyPageSize.value,
      q: keyNameSearch.value.trim() || undefined,
      provider: keyProviderFilter.value || undefined,
    });
    keys.value = res.items;
    keyTotalItems.value = res.pagination?.total_items ?? res.items.length;
    checkedKeyRowKeys.value = [];
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    loadingKeys.value = false;
  }
}

async function loadClientCredits() {
  if (!adminToken.value) return;
  if (!selectedClientId.value) {
    clientCredits.value = null;
    return;
  }
  loadingClientCredits.value = true;
  try {
    clientCredits.value = await getClientCredits(selectedClientId.value);
  } catch (err: unknown) {
    clientCredits.value = null;
    message.warning(getFcamErrorMessage(err));
  } finally {
    loadingClientCredits.value = false;
  }
}

async function loadEncryptionStatus() {
  if (!adminToken.value) return;
  loadingEncryption.value = true;
  try {
    encryption.value = await fetchEncryptionStatus();
  } catch (err: unknown) {
    message.warning(getFcamErrorMessage(err));
  } finally {
    loadingEncryption.value = false;
  }
}

onMounted(async () => {
  try {
    const stored = localStorage.getItem(splitWidthStorageKey);
    if (stored) {
      const parsed = Number(stored);
      if (Number.isFinite(parsed)) leftWidth.value = clampInt(parsed, 120, 520);
    }
  } catch {
    // ignore
  }

  if (adminToken.value) await verifyAdminToken();
  await loadClients();
  await loadEncryptionStatus();
  await loadKeys();
  await loadClientCredits();
});

onBeforeUnmount(() => {
  cleanupResize?.();
  if (keyReloadTimer) clearTimeout(keyReloadTimer);
});

watch(adminToken, async (token) => {
  if (!token) {
    clients.value = [];
    keys.value = [];
    checkedKeyRowKeys.value = [];
    keyPage.value = 1;
    keyPageSize.value = 20;
    keyTotalItems.value = 0;
    keyNameSearch.value = "";
    keyProviderFilter.value = null;
    selectedClientId.value = null;
    encryption.value = null;
    clientCredits.value = null;
    return;
  }

  await verifyAdminToken();
  await loadClients();
  await loadEncryptionStatus();
  await loadKeys();
  await loadClientCredits();
});

watch(selectedClientId, async () => {
  keyPage.value = 1;
  await loadKeys();
  await loadClientCredits();
});

let keyReloadTimer: ReturnType<typeof setTimeout> | undefined;
watch(keyNameSearch, () => {
  if (keyReloadTimer) clearTimeout(keyReloadTimer);
  keyReloadTimer = setTimeout(() => {
    keyPage.value = 1;
    void loadKeys();
  }, 350);
});

watch(keyProviderFilter, () => {
  keyPage.value = 1;
  void loadKeys();
});

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    message.success("已复制");
  } catch {
    message.warning("复制失败（请手动复制）");
  }
}

const showClientTokenModal = ref(false);
const clientTokenOnce = ref<string>("");
const clientTokenLabel = ref<string>("");

function openClientTokenModal(client: ClientItem, token: string) {
  clientTokenLabel.value = `${client.name} (#${client.id})`;
  clientTokenOnce.value = token;
  showClientTokenModal.value = true;
}

watch(showClientTokenModal, (v) => {
  if (v) return;
  clientTokenLabel.value = "";
  clientTokenOnce.value = "";
});

// ---- Create Client modal ----
const showCreateClient = ref(false);
const createClientForm = reactive({
  name: "",
  daily_quota: null as number | null,
  rate_limit_per_min: 60,
  max_concurrent: 10,
  is_active: true,
});
const creatingClient = ref(false);

watch(showCreateClient, (v) => {
  if (v) return;
  createClientForm.name = "";
  createClientForm.daily_quota = null;
  createClientForm.rate_limit_per_min = 60;
  createClientForm.max_concurrent = 10;
  createClientForm.is_active = true;
});

async function submitCreateClient() {
  creatingClient.value = true;
  try {
    const res = await createClient({
      name: createClientForm.name,
      daily_quota: createClientForm.daily_quota,
      rate_limit_per_min: createClientForm.rate_limit_per_min,
      max_concurrent: createClientForm.max_concurrent,
      is_active: createClientForm.is_active,
    });
    message.success("Client 已创建（Token 仅显示一次）");
    showCreateClient.value = false;
    await loadClients();
    selectedClientId.value = res.client.id;
    openClientTokenModal(res.client, res.token);
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    creatingClient.value = false;
  }
}

// ---- Rotate Client token ----
const rotating = ref(false);
async function onRotateToken() {
  const client = selectedClient.value;
  if (!client) return;
  if (rotating.value) return;

  dialog.warning({
    title: "确认轮换 Token",
    content: `将轮换 ${client.name} (#${client.id}) 的 Token。旧 Token 将立即失效。`,
    positiveText: "继续",
    negativeText: "取消",
    onPositiveClick: () => {
      const confirmInput = ref("");
      dialog.create({
        title: "输入 Client 名称确认",
        content: () =>
          h("div", null, [
            h("p", null, [
              "这是危险操作，请输入 ",
              h("strong", { style: { color: "#d03050" } }, client.name),
              " 以确认轮换。",
            ]),
            h(NInput, {
              value: confirmInput.value,
              "onUpdate:value": (v) => {
                confirmInput.value = v;
              },
              placeholder: client.name,
            }),
          ]),
        positiveText: "确认轮换",
        negativeText: "取消",
        onPositiveClick: async () => {
          if (confirmInput.value !== client.name) {
            message.error("Client 名称不匹配");
            return false;
          }

          rotating.value = true;
          try {
            const res = await rotateClientToken(client.id);
            openClientTokenModal(client, res.token);
            message.success("Token 已轮换（仅显示一次）");
          } catch (err: unknown) {
            message.error(getFcamErrorMessage(err), { duration: 5000 });
            return false;
          } finally {
            rotating.value = false;
          }
        },
      });
    },
  });
}

// ---- Disable Client (soft-delete) ----
const disablingClient = ref(false);
async function onDisableClient() {
  const client = selectedClient.value;
  if (!client) return;
  if (disablingClient.value) return;

  dialog.warning({
    title: "确认禁用 Client",
    content: `将禁用 ${client.name} (#${client.id})，该 Client 会从列表与统计中隐藏。`,
    positiveText: "继续",
    negativeText: "取消",
    onPositiveClick: () => {
      const confirmInput = ref("");
      dialog.create({
        title: "输入 Client 名称确认",
        content: () =>
          h("div", null, [
            h("p", null, [
              "这是危险操作，请输入 ",
              h("strong", { style: { color: "#d03050" } }, client.name),
              " 以确认禁用。",
            ]),
            h(NInput, {
              value: confirmInput.value,
              "onUpdate:value": (v) => {
                confirmInput.value = v;
              },
              placeholder: client.name,
            }),
          ]),
        positiveText: "确认禁用",
        negativeText: "取消",
        onPositiveClick: async () => {
          if (confirmInput.value !== client.name) {
            message.error("Client 名称不匹配");
            return false;
          }

          disablingClient.value = true;
          try {
            await updateClient(client.id, { is_active: false });
            message.success("Client 已禁用（已从列表隐藏）");
            await loadClients();
          } catch (err: unknown) {
            message.error(getFcamErrorMessage(err), { duration: 5000 });
          } finally {
            disablingClient.value = false;
          }
        },
      });
    },
  });
}

// ---- Batch Client Operations ----
async function handleBatchEnable() {
  if (!checkedClientIds.value.length || batchOperating.value) return;

  batchOperating.value = true;
  try {
    const result = await batchUpdateClients({
      client_ids: checkedClientIds.value,
      action: 'enable'
    });

    if (result.failed_count === 0) {
      message.success(`已启用 ${result.success_count} 个 Client`);
    } else {
      message.warning(`成功 ${result.success_count} 个，失败 ${result.failed_count} 个`);
      if (result.failed_items.length > 0) {
        const failedDetails = result.failed_items
          .map(item => `Client ${item.client_id}: ${item.error}`)
          .join('\n');
        dialog.warning({
          title: '部分操作失败',
          content: `成功 ${result.success_count} 个，失败 ${result.failed_count} 个\n\n失败详情：\n${failedDetails}`,
          positiveText: '确定'
        });
        checkedClientIds.value = result.failed_items.map(item => item.client_id);
      }
    }

    await loadClients();
    if (result.failed_count === 0) {
      checkedClientIds.value = [];
    }
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    batchOperating.value = false;
  }
}

async function handleBatchDisable() {
  if (!checkedClientIds.value.length || batchOperating.value) return;

  dialog.warning({
    title: '确认批量禁用',
    content: `确认禁用 ${checkedClientIds.value.length} 个 Client？禁用后这些 Client 将无法访问 API。`,
    positiveText: '确认禁用',
    negativeText: '取消',
    onPositiveClick: async () => {
      batchOperating.value = true;
      try {
        const result = await batchUpdateClients({
          client_ids: checkedClientIds.value,
          action: 'disable'
        });

        if (result.failed_count === 0) {
          message.success(`已禁用 ${result.success_count} 个 Client`);
        } else {
          message.warning(`成功 ${result.success_count} 个，失败 ${result.failed_count} 个`);
          if (result.failed_items.length > 0) {
            const failedDetails = result.failed_items
              .map(item => `Client ${item.client_id}: ${item.error}`)
              .join('\n');
            dialog.warning({
              title: '部分操作失败',
              content: `成功 ${result.success_count} 个，失败 ${result.failed_count} 个\n\n失败详情：\n${failedDetails}`,
              positiveText: '确定'
            });
            checkedClientIds.value = result.failed_items.map(item => item.client_id);
          }
        }

        await loadClients();
        if (result.failed_count === 0) {
          checkedClientIds.value = [];
        }
      } catch (err: unknown) {
        message.error(getFcamErrorMessage(err), { duration: 5000 });
      } finally {
        batchOperating.value = false;
      }
    }
  });
}

async function handleBatchDelete() {
  if (!checkedClientIds.value.length || batchOperating.value) return;

  dialog.error({
    title: '确认批量删除',
    content: `确认删除 ${checkedClientIds.value.length} 个 Client？此操作不可恢复。`,
    positiveText: '确认删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      batchOperating.value = true;
      try {
        const result = await batchUpdateClients({
          client_ids: checkedClientIds.value,
          action: 'delete'
        });

        if (result.failed_count === 0) {
          message.success(`已删除 ${result.success_count} 个 Client`);
        } else {
          message.warning(`成功 ${result.success_count} 个，失败 ${result.failed_count} 个`);
          if (result.failed_items.length > 0) {
            const failedDetails = result.failed_items
              .map(item => `Client ${item.client_id}: ${item.error}`)
              .join('\n');
            dialog.warning({
              title: '部分操作失败',
              content: `成功 ${result.success_count} 个，失败 ${result.failed_count} 个\n\n失败详情：\n${failedDetails}`,
              positiveText: '确定'
            });
            checkedClientIds.value = result.failed_items.map(item => item.client_id);
          }
        }

        await loadClients();
        if (result.failed_count === 0) {
          checkedClientIds.value = [];
        }
      } catch (err: unknown) {
        message.error(getFcamErrorMessage(err), { duration: 5000 });
      } finally {
        batchOperating.value = false;
      }
    }
  });
}

// ---- Batch Client Selection ----
const allClientsSelected = computed(() => {
  if (filteredClients.value.length === 0) return false;
  return filteredClients.value.every(c => checkedClientIds.value.includes(c.id));
});

const someClientsSelected = computed(() => {
  if (checkedClientIds.value.length === 0) return false;
  return !allClientsSelected.value;
});

function handleSelectAll(checked: boolean) {
  if (checked) {
    checkedClientIds.value = filteredClients.value.map(c => c.id);
  } else {
    checkedClientIds.value = [];
  }
}

function selectAllClients() {
  checkedClientIds.value = filteredClients.value.map(c => c.id);
}

function deselectAllClients() {
  checkedClientIds.value = [];
}

// ---- Create Key modal ----
const showCreateKey = ref(false);
const creatingKey = ref(false);
const createKeyForm = reactive({
  api_key: "",
  name: "",
  plan_type: "free",
  daily_quota: 5,
  max_concurrent: 2,
  rate_limit_per_min: 10,
  is_active: true,
  provider: "firecrawl",
});

async function submitCreateKey() {
  if (!selectedClientId.value) return;
  creatingKey.value = true;
  try {
    await createKey({
      client_id: selectedClientId.value,
      api_key: createKeyForm.api_key,
      name: createKeyForm.name || null,
      plan_type: createKeyForm.plan_type,
      daily_quota: createKeyForm.daily_quota,
      max_concurrent: createKeyForm.max_concurrent,
      rate_limit_per_min: createKeyForm.rate_limit_per_min,
      is_active: createKeyForm.is_active,
      provider: createKeyForm.provider,
    });
    message.success("Key 已创建");
    showCreateKey.value = false;
    createKeyForm.api_key = "";
    await loadKeys();
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    creatingKey.value = false;
  }
}

// ---- Import Keys modal ----
const showImportKeys = ref(false);
const importing = ref(false);
const importForm = reactive({
  text: "",
  plan_type: "free",
  daily_quota: 5,
  max_concurrent: 2,
  rate_limit_per_min: 10,
  is_active: true,
  provider: "firecrawl",
});
const importResult = ref<string>("");
const importFailures = ref<Array<{ line_no: number; raw: string; message: string }>>([]);

async function submitImportKeys() {
  if (!selectedClientId.value) return;
  importing.value = true;
  importResult.value = "";
  importFailures.value = [];
  try {
    const res = await importKeysText({
      client_id: selectedClientId.value,
      text: importForm.text,
      plan_type: importForm.plan_type,
      daily_quota: importForm.daily_quota,
      max_concurrent: importForm.max_concurrent,
      rate_limit_per_min: importForm.rate_limit_per_min,
      is_active: importForm.is_active,
      provider: importForm.provider,
    });
    importResult.value = `created=${res.created}, updated=${res.updated}, skipped=${res.skipped}, failed=${res.failed}`;
    importFailures.value = res.failures || [];
    message.success("导入完成");
    await loadKeys();
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    importing.value = false;
  }
}

watch(showCreateKey, (v) => {
  if (v) return;
  createKeyForm.api_key = "";
});

watch(showImportKeys, (v) => {
  if (v) return;
  importForm.text = "";
  importResult.value = "";
  importFailures.value = [];
});

const statusTagType = (status: string) => {
  if (status === "active") return "success";
  if (status === "cooling") return "warning";
  if (status === "quota_exceeded") return "warning";
  if (status === "failed" || status === "decrypt_failed") return "error";
  return "default";
};

const activeOptions = [
  { label: "启用", value: "true" },
  { label: "禁用", value: "false" },
];

const mutating = ref(false);
const purgingSelected = ref(false);

async function onToggleKeyActive(row: KeyItem) {
  if (mutating.value) return;
  mutating.value = true;
  try {
    await updateKey(row.id, { is_active: !row.is_active });
    message.success(row.is_active ? "已禁用" : "已启用");
    await loadKeys();
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    mutating.value = false;
  }
}

async function onPurgeKey(row: KeyItem) {
  if (mutating.value) return;

  dialog.warning({
    title: "确认删除 Key",
    content: `将从数据库永久删除 key_id=${row.id}（${row.api_key_masked}）。此操作不可恢复。`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      mutating.value = true;
      try {
        await purgeKey(row.id);
        message.success("Key 已删除");
        await loadKeys();
      } catch (err: unknown) {
        message.error(getFcamErrorMessage(err), { duration: 5000 });
      } finally {
        mutating.value = false;
      }
    },
  });
}

async function onPurgeSelected() {
  const ids = checkedKeyRowKeys.value.slice();
  if (!ids.length) return;
  if (purgingSelected.value) return;

  dialog.warning({
    title: "确认批量删除",
    content: `将从数据库永久删除选中的 ${ids.length} 条 Key。此操作不可恢复。`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      purgingSelected.value = true;
      try {
        let deleted = 0;
        const failed: Array<{ id: number; message: string }> = [];
        for (const id of ids) {
          try {
            await purgeKey(id);
            deleted += 1;
          } catch (err: unknown) {
            failed.push({ id, message: getFcamErrorMessage(err) });
          }
        }

        if (failed.length) {
          message.warning(`批量删除完成：deleted=${deleted} failed=${failed.length}`);
          console.warn("purge_selected_keys_partial_failure", { deleted, failed });
        } else {
          message.success(`批量删除完成：deleted=${deleted}`);
        }
        await loadKeys();
      } finally {
        purgingSelected.value = false;
      }
    },
  });
}

// ---- Batch edit Keys modal ----
const showBatchEditKeys = ref(false);
const batchApplying = ref(false);
const batchResult = ref<Awaited<ReturnType<typeof batchKeys>> | null>(null);
const batchForm = reactive({
  enable_is_active: false,
  is_active: "true",
  enable_plan_type: false,
  plan_type: "free",
  enable_daily_quota: false,
  daily_quota: 5,
  enable_max_concurrent: false,
  max_concurrent: 2,
  enable_rate_limit_per_min: false,
  rate_limit_per_min: 10,
  reset_cooldown: false,
  soft_delete: false,
  run_test: false,
  test_url: "https://example.com",
});

function openBatchEditKeys() {
  if (!checkedKeyRowKeys.value.length) return;
  batchResult.value = null;
  showBatchEditKeys.value = true;
}

watch(showBatchEditKeys, (v) => {
  if (v) return;
  batchApplying.value = false;
  batchResult.value = null;
  batchForm.enable_is_active = false;
  batchForm.is_active = "true";
  batchForm.enable_plan_type = false;
  batchForm.plan_type = "free";
  batchForm.enable_daily_quota = false;
  batchForm.daily_quota = 5;
  batchForm.enable_max_concurrent = false;
  batchForm.max_concurrent = 2;
  batchForm.enable_rate_limit_per_min = false;
  batchForm.rate_limit_per_min = 10;
  batchForm.reset_cooldown = false;
  batchForm.soft_delete = false;
  batchForm.run_test = false;
  batchForm.test_url = "https://example.com";
});

watch(
  () => batchForm.soft_delete,
  (v) => {
    if (!v) return;
    batchForm.enable_is_active = false;
    batchForm.is_active = "false";
  }
);

const batchTestSummary = computed(() => {
  const res = batchResult.value;
  if (!res) return null;
  let ok = 0;
  let failed = 0;
  let skipped = 0;
  for (const r of res.results || []) {
    if (!r.ok) continue;
    if (!("test" in r) || r.test == null) {
      skipped += 1;
      continue;
    }
    if (r.test.ok) ok += 1;
    else failed += 1;
  }
  return { ok, failed, skipped };
});

const batchResultRows = computed(() => {
  const res = batchResult.value;
  if (!res) return [];
  return (res.results || []).map((r) => ({
    id: r.id,
    ok: r.ok,
    api_key_masked: r.key?.api_key_masked || "-",
    status: r.key?.status || "-",
    cooldown_until: r.key?.cooldown_until || null,
    upstream_status_code: r.test?.upstream_status_code ?? null,
    latency_ms: r.test?.latency_ms ?? null,
    test_ok: r.test?.ok ?? null,
    error: r.ok ? null : `${r.error?.code || "ERROR"}: ${r.error?.message || "-"}`,
  }));
});

const batchResultColumns = [
  { title: "id", key: "id", width: 80 },
  {
    title: "Key",
    key: "api_key_masked",
    width: 140,
    render: (row: any) => h("span", { class: "mono" }, row.api_key_masked),
  },
  {
    title: "ok",
    key: "ok",
    width: 70,
    render: (row: any) =>
      h(
        NTag,
        { size: "small", type: row.ok ? ("success" as any) : ("error" as any) },
        { default: () => (row.ok ? "OK" : "ERR") }
      ),
  },
  {
    title: "status",
    key: "status",
    width: 120,
    render: (row: any) =>
      h(NTag, { size: "small", type: statusTagType(row.status) as any }, { default: () => row.status }),
  },
  {
    title: "cooldown",
    key: "cooldown_until",
    width: 170,
    render: (row: any) => row.cooldown_until || "-",
  },
  {
    title: "upstream",
    key: "upstream_status_code",
    width: 100,
    render: (row: any) => (row.upstream_status_code == null ? "-" : String(row.upstream_status_code)),
  },
  {
    title: "latency",
    key: "latency_ms",
    width: 90,
    render: (row: any) => (row.latency_ms == null ? "-" : `${row.latency_ms}ms`),
  },
  {
    title: "error",
    key: "error",
    render: (row: any) => row.error || "-",
  },
];

async function submitBatchEditKeys() {
  const ids = checkedKeyRowKeys.value.slice();
  if (!ids.length) return;
  if (batchApplying.value) return;

  const patch: Record<string, any> = {};
  if (!batchForm.soft_delete && batchForm.enable_is_active) patch.is_active = batchForm.is_active === "true";
  if (batchForm.enable_plan_type) patch.plan_type = batchForm.plan_type;
  if (batchForm.enable_daily_quota) patch.daily_quota = batchForm.daily_quota;
  if (batchForm.enable_max_concurrent) patch.max_concurrent = batchForm.max_concurrent;
  if (batchForm.enable_rate_limit_per_min) patch.rate_limit_per_min = batchForm.rate_limit_per_min;

  const hasPatch = Object.keys(patch).length > 0;
  const hasAnyOp = hasPatch || batchForm.reset_cooldown || batchForm.soft_delete || batchForm.run_test;
  if (!hasAnyOp) {
    message.warning("请选择至少一项批量操作");
    return;
  }

  // 混合 Provider 批量测试前端预检
  if (batchForm.run_test) {
    const selectedKeys = keys.value.filter((k) => ids.includes(k.id));
    const providers = new Set(selectedKeys.map((k) => k.provider));
    if (providers.size > 1) {
      message.warning("不支持混合 Provider 的批量测试，请按 Provider 分开执行");
      return;
    }
  }

  batchApplying.value = true;
  batchResult.value = null;
  try {
    const payload: any = {
      ids,
      reset_cooldown: batchForm.reset_cooldown,
      soft_delete: batchForm.soft_delete,
    };
    if (hasPatch) payload.patch = patch;
    if (batchForm.run_test) {
      payload.test = { mode: "scrape", test_url: batchForm.test_url.trim() || "https://example.com" };
    }

    const res = await batchKeys(payload);
    batchResult.value = res;
    if (res.failed) message.warning(`批量操作完成：succeeded=${res.succeeded} failed=${res.failed}`, { duration: 6000 });
    else message.success(`批量操作完成：succeeded=${res.succeeded}`);
    await loadKeys();
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    batchApplying.value = false;
  }
}

// ---- Edit Key modal ----
const showEditKey = ref(false);
const savingKey = ref(false);
const editForm = reactive({
  keyId: null as number | null,
  api_key_masked: "",
  name: "" as string,
  plan_type: "free",
  daily_quota: 5,
  max_concurrent: 2,
  rate_limit_per_min: 10,
  is_active: true,
  test_url: "https://example.com",
});
const editTestResult = ref<Awaited<ReturnType<typeof testKey>> | null>(null);

function openEditKey(row: KeyItem) {
  editForm.keyId = row.id;
  editForm.api_key_masked = row.api_key_masked;
  editForm.name = row.name || "";
  editForm.plan_type = row.plan_type || "free";
  editForm.daily_quota = row.daily_quota ?? 5;
  editForm.max_concurrent = row.max_concurrent ?? 2;
  editForm.rate_limit_per_min = row.rate_limit_per_min ?? 10;
  editForm.is_active = Boolean(row.is_active);
  editForm.test_url = "https://example.com";
  editTestResult.value = null;
  showEditKey.value = true;
}

watch(showEditKey, (v) => {
  if (v) return;
  editForm.keyId = null;
  editForm.api_key_masked = "";
  editForm.name = "";
  editForm.plan_type = "free";
  editForm.daily_quota = 5;
  editForm.max_concurrent = 2;
  editForm.rate_limit_per_min = 10;
  editForm.is_active = true;
  editForm.test_url = "https://example.com";
  editTestResult.value = null;
});

async function submitEditKey(opts: { testAfterSave: boolean }) {
  if (!editForm.keyId) return;
  savingKey.value = true;
  try {
    await updateKey(editForm.keyId, {
      name: editForm.name.trim() ? editForm.name.trim() : null,
      plan_type: editForm.plan_type,
      daily_quota: editForm.daily_quota,
      max_concurrent: editForm.max_concurrent,
      rate_limit_per_min: editForm.rate_limit_per_min,
      is_active: editForm.is_active,
    });

    if (!opts.testAfterSave) {
      message.success("Key 已更新");
      showEditKey.value = false;
      await loadKeys();
      return;
    }

    const testUrl = editForm.test_url.trim() || "https://example.com";
    const res = await testKey(editForm.keyId, { mode: "scrape", test_url: testUrl });
    editTestResult.value = res;
    if (res.ok) message.success(`保存并测试成功：upstream_status=${res.upstream_status_code ?? "-"}`);
    else message.warning(`保存完成，测试失败：upstream_status=${res.upstream_status_code ?? "-"}`);
    await loadKeys();
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    savingKey.value = false;
  }
}

// ---- Rotate Key modal ----
const showRotateKey = ref(false);
const rotatingKey = ref(false);
const rotateForm = reactive({
  keyId: null as number | null,
  api_key: "",
});

function openRotateKey(row: KeyItem) {
  rotateForm.keyId = row.id;
  rotateForm.api_key = "";
  showRotateKey.value = true;
}

async function submitRotateKey() {
  if (!rotateForm.keyId) return;
  const apiKey = rotateForm.api_key.trim();
  if (!apiKey) {
    message.warning("api_key 不能为空");
    return;
  }
  rotatingKey.value = true;
  try {
    await updateKey(rotateForm.keyId, { api_key: apiKey });
    message.success("Key 已轮换");
    showRotateKey.value = false;
    rotateForm.api_key = "";
    await loadKeys();
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    rotatingKey.value = false;
  }
}

// ---- Test Key modal ----
const showTestKey = ref(false);
const testingKey = ref(false);
const testForm = reactive({
  keyId: null as number | null,
  test_url: "https://example.com",
});
const testResult = ref<Awaited<ReturnType<typeof testKey>> | null>(null);

watch(showRotateKey, (v) => {
  if (v) return;
  rotateForm.keyId = null;
  rotateForm.api_key = "";
});

watch(showTestKey, (v) => {
  if (v) return;
  testForm.keyId = null;
  testResult.value = null;
});

function openTestKey(row: KeyItem) {
  testForm.keyId = row.id;
  testForm.test_url = "https://example.com";
  testResult.value = null;
  showTestKey.value = true;
}

async function submitTestKey() {
  if (!testForm.keyId) return;
  testingKey.value = true;
  try {
    const testUrl = testForm.test_url.trim() || "https://example.com";
    const res = await testKey(testForm.keyId, { mode: "scrape", test_url: testUrl });
    testResult.value = res;
    if (res.ok) {
      message.success(`测试成功：upstream_status=${res.upstream_status_code ?? "-"}`);
    } else if (res.upstream_status_code == null) {
      message.warning("测试失败：上游不可达/超时（检查 FCAM_FIRECRAWL__BASE_URL / 网络 / timeout）", { duration: 6000 });
    } else {
      message.warning(`测试失败：upstream_status=${res.upstream_status_code}`);
    }
    await loadKeys();
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    testingKey.value = false;
  }
}

async function refreshCreditsForCurrentKeys() {
  if (!selectedClientId.value) return;
  refreshingCredits.value = true;
  try {
    const firecrawlKeys = keys.value.filter((k) => k.provider === "firecrawl");
    const keyIds = checkedKeyRowKeys.value.length
      ? checkedKeyRowKeys.value.filter((id) => firecrawlKeys.some((k) => k.id === id))
      : firecrawlKeys.map((k) => k.id);

    if (!keyIds.length) {
      message.warning("当前无 Firecrawl Key 可刷新额度（Exa Key 不支持额度查询）");
      return;
    }

    const res = await refreshAllCredits({ key_ids: keyIds });
    if (res.failed) {
      message.warning(`额度刷新完成：成功 ${res.success}，失败 ${res.failed}`, { duration: 5000 });
    } else {
      message.success(`额度刷新完成：成功 ${res.success}`);
    }
    await loadKeys();
    await loadClientCredits();
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    refreshingCredits.value = false;
  }
}

async function loadCreditsDrawer(keyId: number) {
  loadingCreditsDrawer.value = true;
  try {
    creditsDrawerInfo.value = await getKeyCredits(keyId);
  } catch (err: unknown) {
    creditsDrawerInfo.value = null;
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    loadingCreditsDrawer.value = false;
  }
}

function openCreditsDrawer(row: KeyItem) {
  creditsDrawerKey.value = row;
  creditsDrawerInfo.value = null;
  showCreditsDrawer.value = true;
  void loadCreditsDrawer(row.id);
}

type KeyColumnConfig = {
  key: string;
  title: string;
  width?: number;
  fixed?: "left" | "right";
  defaultVisible?: boolean;
  alwaysVisible?: boolean;
  required?: boolean;
  render?: (row: KeyItem) => any;
};

const keyColumnsStorageKey = "fcam_ui2_keys_visible_columns_v1";
const visibleKeyColumns = ref<string[]>([]);

const allKeyColumnConfigs: KeyColumnConfig[] = [
  {
    key: "api_key_masked",
    title: "Key",
    width: 140,
    required: true,
    defaultVisible: true,
    render: (row: KeyItem) => h("span", { class: "mono" }, row.api_key_masked),
  },
  { key: "name", title: "Name", width: 160, defaultVisible: true, render: (row: KeyItem) => row.name || "-" },
  { key: "plan_type", title: "Plan", width: 90, defaultVisible: true },
  {
    key: "provider",
    title: "Provider",
    width: 100,
    defaultVisible: true,
    render: (row: KeyItem) =>
      h(NTag, { size: "small", type: row.provider === "exa" ? ("warning" as any) : ("info" as any) }, { default: () => row.provider }),
  },
  {
    key: "status",
    title: "Status",
    width: 120,
    required: true,
    defaultVisible: true,
    render: (row: KeyItem) =>
      h(NTag, { size: "small", type: statusTagType(row.status) as any }, { default: () => row.status }),
  },
  {
    key: "is_active",
    title: "启用",
    width: 80,
    defaultVisible: true,
    render: (row: KeyItem) =>
      h(
        NTag,
        { size: "small", type: row.is_active ? ("success" as any) : ("default" as any) },
        { default: () => (row.is_active ? "启用" : "禁用") }
      ),
  },
  {
    key: "quota",
    title: "Quota",
    width: 110,
    defaultVisible: true,
    render: (row: KeyItem) => `${row.daily_usage}/${row.daily_quota}`,
  },
  {
    key: "credits",
    title: "额度",
    width: 320,
    defaultVisible: true,
    render: (row: KeyItem) =>
      h(CreditDisplay, {
        keyId: row.id,
        remainingCredits: row.cached_remaining_credits,
        planCredits: row.cached_plan_credits,
        totalCredits: row.cached_total_credits,
        lastUpdateAt: row.last_credit_check_at,
        isEstimated: row.cached_is_estimated ?? false,
        onRefresh: () => {
          void loadKeys();
          void loadClientCredits();
          if (creditsDrawerKey.value?.id === row.id) void loadCreditsDrawer(row.id);
        },
      }),
  },
  {
    key: "billing_period",
    title: "账期",
    width: 210,
    defaultVisible: true,
    render: (row: KeyItem) => {
      if (!row.billing_period_start || !row.billing_period_end) return "-";
      return `${formatDate(row.billing_period_start)} ~ ${formatDate(row.billing_period_end)}`;
    },
  },
  {
    key: "next_refresh_at",
    title: "下次刷新",
    width: 170,
    defaultVisible: true,
    render: (row: KeyItem) =>
      h(
        "span",
        { title: formatTimestamp(row.next_refresh_at) },
        formatRelativeTime(row.next_refresh_at)
      ),
  },
  {
    key: "cooldown_until",
    title: "Cooldown",
    width: 170,
    defaultVisible: false,
    render: (row: KeyItem) => row.cooldown_until || "-",
  },
  { key: "rate_limit_per_min", title: "RPM", width: 90, defaultVisible: false },
  { key: "max_concurrent", title: "并发", width: 90, defaultVisible: false },
  {
    key: "last_used_at",
    title: "Last Used",
    width: 170,
    defaultVisible: true,
    render: (row: KeyItem) => row.last_used_at || "-",
  },
  {
    key: "created_at",
    title: "Created",
    width: 170,
    defaultVisible: false,
    render: (row: KeyItem) => row.created_at || "-",
  },
  {
    key: "actions",
    title: "操作",
    width: 72,
    alwaysVisible: true,
    required: true,
    render: (row: KeyItem) =>
      h(
        NDropdown,
        {
          trigger: "click",
          options: [
            { label: "测试", key: "test" },
            ...(row.provider === "firecrawl" ? [{ label: "额度详情", key: "credits" }] : []),
            { label: "编辑", key: "edit" },
            { label: row.is_active ? "禁用" : "启用", key: "toggle" },
            { label: "轮换", key: "rotate" },
            { type: "divider", key: "d1" } as any,
            { label: "删除", key: "purge", props: { style: { color: "#d03050" } } },
          ] as any,
          onSelect: (action: string) => {
            if (action === "test") openTestKey(row);
            if (action === "credits") openCreditsDrawer(row);
            if (action === "edit") openEditKey(row);
            if (action === "toggle") void onToggleKeyActive(row);
            if (action === "rotate") openRotateKey(row);
            if (action === "purge") void onPurgeKey(row);
          },
        },
        {
          default: () =>
            h(
              NButton,
              { size: "tiny", tertiary: true, disabled: mutating.value || purgingSelected.value },
              { default: () => "⋯" }
            ),
        }
      ),
  },
];

function loadKeyColumnPreferences() {
  try {
    const saved = localStorage.getItem(keyColumnsStorageKey);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed)) {
        visibleKeyColumns.value = parsed.filter((k) => typeof k === "string");
      }
    }
  } catch {
    // ignore
  }

  const required = allKeyColumnConfigs.filter((c) => c.required).map((c) => c.key);
  if (!visibleKeyColumns.value.length) {
    visibleKeyColumns.value = allKeyColumnConfigs
      .filter((c) => !c.alwaysVisible && (c.defaultVisible || c.required))
      .map((c) => c.key);
  }
  for (const key of required) {
    if (!visibleKeyColumns.value.includes(key)) visibleKeyColumns.value.push(key);
  }
}

function saveKeyColumnPreferences() {
  try {
    localStorage.setItem(keyColumnsStorageKey, JSON.stringify(visibleKeyColumns.value));
  } catch {
    // ignore
  }
}

function selectAllKeyColumns() {
  visibleKeyColumns.value = allKeyColumnConfigs.filter((c) => !c.alwaysVisible).map((c) => c.key);
}

function deselectAllKeyColumns() {
  visibleKeyColumns.value = allKeyColumnConfigs.filter((c) => c.required).map((c) => c.key);
}

loadKeyColumnPreferences();
watch(visibleKeyColumns, saveKeyColumnPreferences, { deep: true });

const keyColumns = computed(() => {
  const visible = new Set(visibleKeyColumns.value);
  const cols = allKeyColumnConfigs
    .filter((c) => c.alwaysVisible || c.required || visible.has(c.key))
    .map((c) => ({
      title: c.title,
      key: c.key,
      width: c.width,
      fixed: c.fixed,
      render: c.render,
    }));
  return [{ type: "selection" as const, fixed: "left" as const }, ...cols];
});

const keyPagination = computed(() => ({
  page: keyPage.value,
  pageSize: keyPageSize.value,
  itemCount: keyTotalItems.value,
  showSizePicker: true,
  pageSizes: [20, 50, 100],
  onUpdatePage: (p: number) => {
    keyPage.value = p;
    void loadKeys();
  },
  onUpdatePageSize: (s: number) => {
    keyPageSize.value = s;
    keyPage.value = 1;
    void loadKeys();
  },
}));

const planOptions = [
  { label: "free", value: "free" },
  { label: "hobby", value: "hobby" },
  { label: "standard", value: "standard" },
  { label: "growth", value: "growth" },
];

function rowKey(row: KeyItem) {
  return row.id;
}
</script>

<template>
  <n-space vertical size="large">
    <n-alert v-if="!adminToken" type="warning" title="未连接 Admin Token">
      右上角点击「连接」后再进行管理操作。
    </n-alert>

    <template v-else>
      <n-alert
        v-if="encryption && encryption.master_key_configured && encryption.has_decrypt_failures"
        type="error"
        title="检测到不可解密的 Key"
      >
        {{ encryption.suggestion || "请检查 FCAM_MASTER_KEY 是否与加密时一致。" }}
      </n-alert>

      <div class="split-panel">
        <div class="split-left" :style="{ width: `${leftWidth}px` }">
          <div class="split-left-header">
            <n-space vertical>
              <n-space align="center" justify="space-between">
                <div style="font-weight: 800">Clients（{{ clients.length }}）</div>
                <n-button size="tiny" :loading="loadingClients" @click="loadClients">刷新</n-button>
              </n-space>
              <n-input v-model:value="clientSearch" placeholder="搜索 client..." size="small" />

              <!-- 批量操作按钮区域 -->
              <div v-if="checkedClientIds.length > 0" style="padding: 8px; background: #f5f5f5; border-radius: 4px">
                <n-space vertical size="small">
                  <div style="font-size: 12px; color: #666">已选择 {{ checkedClientIds.length }} 个 Client</div>
                  <n-space size="small">
                    <n-button
                      size="tiny"
                      type="success"
                      :loading="batchOperating"
                      @click="handleBatchEnable"
                    >
                      批量启用
                    </n-button>
                    <n-button
                      size="tiny"
                      type="warning"
                      :loading="batchOperating"
                      @click="handleBatchDisable"
                    >
                      批量禁用
                    </n-button>
                    <n-button
                      size="tiny"
                      type="error"
                      :loading="batchOperating"
                      @click="handleBatchDelete"
                    >
                      批量删除
                    </n-button>
                  </n-space>
                </n-space>
              </div>

              <!-- 未选择时显示全选按钮 -->
              <n-space align="center">
                <n-checkbox
                  :checked="allClientsSelected"
                  :indeterminate="someClientsSelected"
                  @update:checked="handleSelectAll"
                />
                <n-button type="primary" size="small" @click="showCreateClient = true">创建 Client</n-button>
              </n-space>
            </n-space>
          </div>

          <div class="split-left-body">
            <n-list clickable hoverable>
              <n-list-item
                v-for="c in filteredClients"
                :key="c.id"
                :class="{ 'client-item-active': c.id === selectedClientId }"
              >
                <div style="display: flex; align-items: center; gap: 8px; width: 100%">
                  <n-checkbox
                    :checked="checkedClientIds.includes(c.id)"
                    @update:checked="(checked) => {
                      if (checked) {
                        checkedClientIds.push(c.id);
                      } else {
                        const idx = checkedClientIds.indexOf(c.id);
                        if (idx > -1) checkedClientIds.splice(idx, 1);
                      }
                    }"
                    @click.stop
                  />
                  <div
                    style="display: flex; align-items: center; justify-content: space-between; flex: 1; cursor: pointer"
                    @click="selectedClientId = c.id"
                  >
                    <div class="client-meta">
                      <div class="client-name" style="font-weight: 700">{{ c.name }}</div>
                      <div class="muted" style="font-size: 12px">#{{ c.id }}</div>
                    </div>
                    <n-tag size="small" :type="c.is_active ? 'success' : 'default'">{{
                      c.is_active ? "启用" : "禁用"
                    }}</n-tag>
                  </div>
                </div>
              </n-list-item>
            </n-list>
          </div>
        </div>

        <div class="split-handle" :class="{ dragging: isResizing }" @pointerdown="onResizePointerDown" />

        <div class="split-right">
          <div style="padding: 14px">
          <n-card v-if="!selectedClient" title="选择一个 Client" size="small">
            左侧选择 Client 后，在右侧管理该 Client 的 Keys（隔离池）。
          </n-card>

          <template v-else>
            <n-card size="small">
              <template #header>
                <n-space align="center" justify="space-between">
                  <div>
                    <div style="font-weight: 800; font-size: 16px">{{ selectedClient.name }}</div>
                    <div class="muted" style="font-size: 12px">
                      #{{ selectedClient.id }} ·
                      {{ selectedClient.is_active ? "启用" : "禁用" }} ·
                      RPM={{ selectedClient.rate_limit_per_min }} ·
                      并发={{ selectedClient.max_concurrent }}
                    </div>
                  </div>
                  <n-space>
                    <n-button size="small" :loading="rotating" @click="onRotateToken">轮换 Token</n-button>
                    <n-button size="small" type="error" :loading="disablingClient" @click="onDisableClient">
                      禁用 Client
                    </n-button>
                    <n-button size="small" @click="loadKeys" :loading="loadingKeys">刷新 Keys</n-button>
                  </n-space>
                </n-space>
              </template>

              <n-space vertical size="small" style="margin-bottom: 8px">
                <n-space align="center" justify="space-between">
                  <div class="muted" style="font-size: 12px">Client 额度汇总</div>
                  <n-button size="tiny" :loading="loadingClientCredits" @click="loadClientCredits">刷新汇总</n-button>
                </n-space>
                <n-progress type="line" :percentage="clientUsagePercentage" :status="clientUsageStatus as any" :height="10" />
                <div class="muted" style="font-size: 12px">
                  <template v-if="clientCredits">
                    剩余 {{ clientCredits.total_remaining_credits.toLocaleString() }} / {{ (clientCredits.total_credits || 0).toLocaleString() }}
                    （已用 {{ clientUsagePercentage.toFixed(2) }}%）
                  </template>
                  <template v-else>暂无额度数据（可先刷新额度）。</template>
                </div>
              </n-space>

              <n-divider />

              <n-space wrap>
                <n-button type="primary" size="small" @click="showCreateKey = true">添加 Key</n-button>
                <n-button size="small" @click="showImportKeys = true">文本导入</n-button>
                <n-button
                  size="small"
                  :disabled="!checkedKeyRowKeys.length"
                  :loading="batchApplying"
                  @click="openBatchEditKeys"
                >
                  批量编辑（{{ checkedKeyRowKeys.length }}）
                </n-button>
                <n-button
                  type="error"
                  size="small"
                  :disabled="!checkedKeyRowKeys.length"
                  :loading="purgingSelected"
                  @click="onPurgeSelected"
                >
                  永久删除所选（{{ checkedKeyRowKeys.length }}）
                </n-button>
                <n-button
                  size="small"
                  :loading="refreshingCredits"
                  :disabled="!keys.length || !keys.some(k => k.provider === 'firecrawl')"
                  @click="refreshCreditsForCurrentKeys"
                >
                  刷新额度（{{ checkedKeyRowKeys.length ? `所选 ${checkedKeyRowKeys.length}` : "当前页" }}）
                </n-button>
                <n-input
                  v-model:value="keyNameSearch"
                  size="small"
                  clearable
                  placeholder="按 name 搜索..."
                  style="width: 220px"
                />
                <n-select
                  v-model:value="keyProviderFilter"
                  size="small"
                  clearable
                  placeholder="Provider"
                  :options="[
                    { label: 'Firecrawl', value: 'firecrawl' },
                    { label: 'Exa', value: 'exa' },
                  ]"
                  style="width: 130px"
                />
                <n-popover trigger="click" placement="bottom-end">
                  <template #trigger>
                    <n-button size="small" ghost>列</n-button>
                  </template>
                  <div style="min-width: 240px">
                    <n-space vertical size="small">
                      <n-space>
                        <n-button size="tiny" @click="selectAllKeyColumns">全选</n-button>
                        <n-button size="tiny" @click="deselectAllKeyColumns">仅必选</n-button>
                      </n-space>
                      <n-checkbox-group v-model:value="visibleKeyColumns">
                        <n-space vertical size="small">
                          <n-checkbox
                            v-for="col in allKeyColumnConfigs.filter((c) => !c.alwaysVisible)"
                            :key="col.key"
                            :value="col.key"
                            :label="col.title"
                            :disabled="col.required"
                          />
                        </n-space>
                      </n-checkbox-group>
                    </n-space>
                  </div>
                </n-popover>
              </n-space>
            </n-card>

            <n-card style="margin-top: 12px" :title="`Keys（${keyTotalItems}）`" size="small">
              <n-data-table
                :columns="keyColumns as any"
                :data="keys"
                :loading="loadingKeys"
                :row-key="rowKey"
                v-model:checked-row-keys="checkedKeyRowKeys"
                size="small"
                :pagination="keyPagination as any"
                remote
                :scroll-x="1200"
                striped
              />
            </n-card>
          </template>
          </div>
        </div>
      </div>
    </template>

    <!-- Create Client modal -->
    <n-modal v-model:show="showCreateClient" preset="card" style="max-width: 560px">
      <n-card title="创建 Client" :bordered="false">
        <n-space vertical>
          <n-form label-placement="top" :model="createClientForm">
            <n-form-item label="name">
              <n-input v-model:value="createClientForm.name" placeholder="service-a" />
            </n-form-item>
            <n-form-item label="daily_quota（可选）">
              <n-input-number v-model:value="createClientForm.daily_quota" clearable style="width: 100%" />
            </n-form-item>
            <n-form-item label="rate_limit_per_min">
              <n-input-number v-model:value="createClientForm.rate_limit_per_min" style="width: 100%" />
            </n-form-item>
            <n-form-item label="max_concurrent">
              <n-input-number v-model:value="createClientForm.max_concurrent" style="width: 100%" />
            </n-form-item>
            <n-form-item>
              <n-checkbox v-model:checked="createClientForm.is_active">启用</n-checkbox>
            </n-form-item>
          </n-form>

          <n-space justify="end">
            <n-button :disabled="creatingClient" @click="showCreateClient = false">关闭</n-button>
            <n-button type="primary" :loading="creatingClient" @click="submitCreateClient">创建 Client</n-button>
          </n-space>
        </n-space>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showClientTokenModal" preset="card" style="max-width: 560px">
      <n-card title="Client Token（仅显示一次）" :bordered="false">
        <n-space vertical>
          <div class="muted" style="font-size: 12px">Client：{{ clientTokenLabel }}</div>
          <n-alert v-if="clientTokenOnce" type="success" title="请立即复制并妥善保存">
            <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
              <span class="mono">{{ clientTokenOnce }}</span>
              <n-button size="tiny" @click="copyText(clientTokenOnce)">复制</n-button>
            </div>
          </n-alert>
          <n-space justify="end">
            <n-button @click="showClientTokenModal = false">关闭</n-button>
          </n-space>
        </n-space>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showCreateKey" preset="card" style="max-width: 560px">
      <n-card title="添加 Key（绑定到当前 Client）" :bordered="false">
        <n-space vertical>
          <n-form label-placement="top" :model="createKeyForm">
            <n-form-item label="api_key">
              <n-input v-model:value="createKeyForm.api_key" placeholder="fc-... / exa-..." type="password" />
            </n-form-item>
            <n-form-item label="Provider">
              <n-select v-model:value="createKeyForm.provider" :options="[{ label: 'firecrawl', value: 'firecrawl' }, { label: 'exa', value: 'exa' }]" />
            </n-form-item>
            <n-form-item label="name（可选）">
              <n-input v-model:value="createKeyForm.name" placeholder="k1" />
            </n-form-item>
            <n-form-item label="plan_type">
              <n-select v-model:value="createKeyForm.plan_type" :options="planOptions" />
            </n-form-item>
            <n-form-item label="daily_quota">
              <n-input-number v-model:value="createKeyForm.daily_quota" style="width: 100%" />
            </n-form-item>
            <n-form-item label="max_concurrent">
              <n-input-number v-model:value="createKeyForm.max_concurrent" style="width: 100%" />
            </n-form-item>
            <n-form-item label="rate_limit_per_min">
              <n-input-number v-model:value="createKeyForm.rate_limit_per_min" style="width: 100%" />
            </n-form-item>
            <n-form-item>
              <n-checkbox v-model:checked="createKeyForm.is_active">启用</n-checkbox>
            </n-form-item>
          </n-form>
          <n-space justify="end">
            <n-button :disabled="creatingKey" @click="showCreateKey = false">取消</n-button>
            <n-button type="primary" :loading="creatingKey" @click="submitCreateKey">创建</n-button>
          </n-space>
        </n-space>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showImportKeys" preset="card" style="max-width: 680px">
      <n-card title="文本导入 Keys（绑定到当前 Client）" :bordered="false">
        <n-space vertical>
          <n-form label-placement="top" :model="importForm">
            <n-form-item label="text（每行一条：user|pass|api_key|verified_at 或直接 api_key）">
              <n-input v-model:value="importForm.text" type="textarea" :autosize="{ minRows: 6, maxRows: 14 }" />
            </n-form-item>
            <n-space>
              <n-form-item label="Provider" style="min-width: 140px">
                <n-select v-model:value="importForm.provider" :options="[{ label: 'firecrawl', value: 'firecrawl' }, { label: 'exa', value: 'exa' }]" />
              </n-form-item>
              <n-form-item label="plan_type" style="min-width: 140px">
                <n-select v-model:value="importForm.plan_type" :options="planOptions" />
              </n-form-item>
              <n-form-item label="daily_quota" style="min-width: 140px">
                <n-input-number v-model:value="importForm.daily_quota" />
              </n-form-item>
              <n-form-item label="max_concurrent" style="min-width: 140px">
                <n-input-number v-model:value="importForm.max_concurrent" />
              </n-form-item>
              <n-form-item label="rate_limit_per_min" style="min-width: 160px">
                <n-input-number v-model:value="importForm.rate_limit_per_min" />
              </n-form-item>
              <n-form-item label="启用">
                <n-checkbox v-model:checked="importForm.is_active" />
              </n-form-item>
            </n-space>
          </n-form>

          <n-alert v-if="importResult" type="info" title="导入结果">
            {{ importResult }}
          </n-alert>

          <n-alert v-if="importFailures.length" type="warning" title="失败明细（最多显示前 20 条）">
              <div
                v-for="(f, idx) in importFailures.slice(0, 20)"
                :key="`${f.line_no}-${idx}`"
                style="margin-top: 6px"
              >
                <div class="mono">line={{ f.line_no }}</div>
                <div class="muted">{{ f.message }}</div>
              </div>
              <div class="muted" style="margin-top: 8px; font-size: 12px">
                出于安全原因，不展示原始导入文本（可能包含明文账号/密码/api_key）。
              </div>
            </n-alert>

          <n-space justify="end">
            <n-button :disabled="importing" @click="showImportKeys = false">关闭</n-button>
            <n-button type="primary" :loading="importing" @click="submitImportKeys">执行导入</n-button>
          </n-space>
        </n-space>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showBatchEditKeys" preset="card" style="max-width: 720px">
      <n-card title="批量编辑 Keys" :bordered="false">
        <n-space vertical>
          <n-alert type="info" title="已选择 Keys">
            已选择 <span class="mono">{{ checkedKeyRowKeys.length }}</span> 条 Key（尽力而为，允许部分失败）。
          </n-alert>

          <n-form label-placement="top" :model="batchForm">
            <n-form-item label="批量启用/禁用">
              <n-space align="center" wrap>
                <n-checkbox v-model:checked="batchForm.enable_is_active" :disabled="batchForm.soft_delete">
                  修改
                </n-checkbox>
                <n-select
                  v-model:value="batchForm.is_active"
                  size="small"
                  style="min-width: 140px"
                  :disabled="!batchForm.enable_is_active || batchForm.soft_delete"
                  :options="activeOptions as any"
                />
                <span v-if="batchForm.soft_delete" class="muted" style="font-size: 12px">
                  已选择“软删除”，此处不可用
                </span>
              </n-space>
            </n-form-item>

            <n-form-item label="批量修改配置">
              <n-space wrap>
                <n-space align="center">
                  <n-checkbox v-model:checked="batchForm.enable_plan_type">plan_type</n-checkbox>
                  <n-select
                    v-model:value="batchForm.plan_type"
                    size="small"
                    style="min-width: 140px"
                    :disabled="!batchForm.enable_plan_type"
                    :options="planOptions"
                  />
                </n-space>

                <n-space align="center">
                  <n-checkbox v-model:checked="batchForm.enable_daily_quota">daily_quota</n-checkbox>
                  <n-input-number v-model:value="batchForm.daily_quota" :disabled="!batchForm.enable_daily_quota" />
                </n-space>

                <n-space align="center">
                  <n-checkbox v-model:checked="batchForm.enable_max_concurrent">max_concurrent</n-checkbox>
                  <n-input-number
                    v-model:value="batchForm.max_concurrent"
                    :disabled="!batchForm.enable_max_concurrent"
                  />
                </n-space>

                <n-space align="center">
                  <n-checkbox v-model:checked="batchForm.enable_rate_limit_per_min">rate_limit_per_min</n-checkbox>
                  <n-input-number
                    v-model:value="batchForm.rate_limit_per_min"
                    :disabled="!batchForm.enable_rate_limit_per_min"
                  />
                </n-space>
              </n-space>
            </n-form-item>

            <n-form-item label="批量附加操作">
              <n-space wrap>
                <n-checkbox v-model:checked="batchForm.reset_cooldown">清除冷却（cooldown_until）</n-checkbox>
                <n-checkbox v-model:checked="batchForm.soft_delete">软删除（禁用并标记 disabled）</n-checkbox>
                <n-checkbox v-model:checked="batchForm.run_test">批量测试</n-checkbox>
              </n-space>
            </n-form-item>

            <n-form-item v-if="batchForm.run_test" label="test_url（会真实调用上游 scrape）">
              <n-input v-model:value="batchForm.test_url" placeholder="https://example.com" />
            </n-form-item>
          </n-form>

          <n-alert
            v-if="batchResult"
            :type="batchResult.failed ? 'warning' : 'success'"
            :title="`执行结果：requested=${batchResult.requested} succeeded=${batchResult.succeeded} failed=${batchResult.failed}`"
          >
            <div v-if="batchTestSummary && (batchTestSummary.ok + batchTestSummary.failed) > 0" class="muted">
              测试结果：ok={{ batchTestSummary.ok }} failed={{ batchTestSummary.failed }}
            </div>
            <div v-if="batchResult.failed" style="margin-top: 8px">
              <div class="muted" style="font-size: 12px; margin-bottom: 6px">失败列表（最多显示前 20 条）：</div>
              <div
                v-for="(r, idx) in batchResult.results.filter((x) => !x.ok).slice(0, 20)"
                :key="`${r.id}-${idx}`"
                class="mono"
                style="font-size: 12px; margin-top: 4px"
              >
                id={{ r.id }} {{ r.error?.code || "ERROR" }}: {{ r.error?.message || "-" }}
              </div>
            </div>
            <n-space style="margin-top: 10px" wrap>
              <n-button size="tiny" @click="copyText(JSON.stringify(batchResult, null, 2))">复制结果 JSON</n-button>
            </n-space>
          </n-alert>

          <n-card v-if="batchResult" size="small" title="详细结果（含测试结果）" style="margin-top: 8px">
            <n-data-table
              :columns="batchResultColumns as any"
              :data="batchResultRows as any"
              size="small"
              striped
              :pagination="false"
              :scroll-x="1200"
            />
          </n-card>

          <n-space justify="end">
            <n-button :disabled="batchApplying" @click="showBatchEditKeys = false">关闭</n-button>
            <n-button type="primary" :loading="batchApplying" @click="submitBatchEditKeys">执行</n-button>
          </n-space>
        </n-space>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showEditKey" preset="card" style="max-width: 640px">
      <n-card :title="`编辑 Key（${editForm.api_key_masked || '-' }）`" :bordered="false">
        <n-space vertical>
          <n-form label-placement="top" :model="editForm">
            <n-form-item label="name（可选）">
              <n-input v-model:value="editForm.name" placeholder="k1" />
            </n-form-item>

            <n-space wrap>
              <n-form-item label="plan_type" style="min-width: 140px">
                <n-select v-model:value="editForm.plan_type" :options="planOptions" />
              </n-form-item>
              <n-form-item label="daily_quota" style="min-width: 140px">
                <n-input-number v-model:value="editForm.daily_quota" />
              </n-form-item>
              <n-form-item label="max_concurrent" style="min-width: 160px">
                <n-input-number v-model:value="editForm.max_concurrent" />
              </n-form-item>
              <n-form-item label="rate_limit_per_min" style="min-width: 180px">
                <n-input-number v-model:value="editForm.rate_limit_per_min" />
              </n-form-item>
              <n-form-item label="启用">
                <n-checkbox v-model:checked="editForm.is_active" />
              </n-form-item>
            </n-space>

            <n-form-item label="保存并测试（可选）test_url">
              <n-input v-model:value="editForm.test_url" placeholder="https://example.com" />
            </n-form-item>
          </n-form>

          <n-alert v-if="editTestResult" :type="editTestResult.ok ? 'success' : 'info'" title="测试结果">
            <pre style="white-space: pre-wrap; margin: 0">{{ JSON.stringify(editTestResult, null, 2) }}</pre>
          </n-alert>

          <n-space justify="end">
            <n-button :disabled="savingKey" @click="showEditKey = false">关闭</n-button>
            <n-button :loading="savingKey" @click="submitEditKey({ testAfterSave: false })">保存</n-button>
            <n-button type="primary" :loading="savingKey" @click="submitEditKey({ testAfterSave: true })">
              保存并测试
            </n-button>
          </n-space>
        </n-space>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showRotateKey" preset="card" style="max-width: 560px">
      <n-card title="轮换 Key（替换上游 api_key）" :bordered="false">
        <n-space vertical>
          <n-form label-placement="top" :model="rotateForm">
            <n-form-item label="new api_key">
              <n-input v-model:value="rotateForm.api_key" placeholder="fc-..." type="password" />
            </n-form-item>
          </n-form>
          <n-space justify="end">
            <n-button :disabled="rotatingKey" @click="showRotateKey = false">取消</n-button>
            <n-button type="primary" :loading="rotatingKey" @click="submitRotateKey">轮换</n-button>
          </n-space>
        </n-space>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showTestKey" preset="card" style="max-width: 640px">
      <n-card title="测试 Key" :bordered="false">
        <n-space vertical>
          <n-form label-placement="top" :model="testForm">
            <n-form-item label="test_url（会真实调用上游 scrape）">
              <n-input v-model:value="testForm.test_url" placeholder="https://example.com" />
            </n-form-item>
          </n-form>

          <n-alert v-if="testResult && testResult.upstream_status_code == null" type="warning" title="上游不可达/超时">
            <div class="muted" style="font-size: 13px">
              <div>
                <span class="mono">upstream_status_code=null</span> 通常表示网络连接失败或超时（请检查
                <span class="mono">FCAM_FIRECRAWL__BASE_URL</span> / 网络 / timeout）。
              </div>
            </div>
          </n-alert>

          <n-alert v-if="testResult" :type="testResult.ok ? 'success' : 'info'" title="测试结果">
            <n-space align="center" justify="space-between" wrap style="margin-bottom: 8px">
              <div class="muted" style="font-size: 12px">
                upstream_status={{ testResult.upstream_status_code ?? "-" }} · latency_ms={{ testResult.latency_ms ?? "-" }} ·
                observed={{ testResult.observed?.status ?? "-" }}
              </div>
              <n-button size="tiny" @click="copyText(JSON.stringify(testResult, null, 2))">复制 JSON</n-button>
            </n-space>
            <pre style="white-space: pre-wrap; margin: 0">{{ JSON.stringify(testResult, null, 2) }}</pre>
          </n-alert>

          <n-space justify="end">
            <n-button :disabled="testingKey" @click="showTestKey = false">关闭</n-button>
            <n-button type="primary" :loading="testingKey" @click="submitTestKey">执行测试</n-button>
          </n-space>
        </n-space>
      </n-card>
    </n-modal>

    <n-drawer v-model:show="showCreditsDrawer" :width="860">
      <n-drawer-content :title="`额度详情（${creditsDrawerKey?.api_key_masked ?? '-' }）`">
        <n-space vertical size="large">
          <n-alert v-if="loadingCreditsDrawer" type="info" title="加载中">正在加载额度信息...</n-alert>

          <credit-display
            v-if="creditsDrawerKey"
            :key-id="creditsDrawerKey.id"
            :remaining-credits="creditsDrawerInfo?.cached_credits.remaining_credits ?? creditsDrawerKey.cached_remaining_credits"
            :plan-credits="creditsDrawerInfo?.cached_credits.plan_credits ?? creditsDrawerKey.cached_plan_credits"
            :total-credits="creditsDrawerInfo?.cached_credits.total_credits ?? creditsDrawerKey.cached_total_credits"
            :last-update-at="creditsDrawerInfo?.cached_credits.last_updated_at ?? creditsDrawerKey.last_credit_check_at"
            :is-estimated="creditsDrawerInfo?.cached_credits.is_estimated ?? false"
            @refresh="() => {
              if (creditsDrawerKey) void loadCreditsDrawer(creditsDrawerKey.id);
              void loadKeys();
              void loadClientCredits();
            }"
          />

          <n-alert v-if="creditsDrawerInfo?.latest_snapshot" type="default" title="最新快照（真实值）">
            <pre style="white-space: pre-wrap; margin: 0">{{ JSON.stringify(creditsDrawerInfo.latest_snapshot, null, 2) }}</pre>
          </n-alert>

          <n-divider />
          <credit-trend-chart v-if="creditsDrawerKey" :key-id="creditsDrawerKey.id" />
        </n-space>
      </n-drawer-content>
    </n-drawer>
  </n-space>
</template>
