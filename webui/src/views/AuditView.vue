<script setup lang="ts">
import { NButton, NCard, NCheckboxGroup, NDataTable, NDropdown, NInput, NSelect, NSpace, useMessage } from "naive-ui";
import { computed, h, onMounted, reactive, ref, watch } from "vue";

import { fetchAuditLogs, type AuditLogItem } from "@/api/logs";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken } from "@/state/adminAuth";
import { formatTimestamp } from "@/utils/time";

const message = useMessage();

const loading = ref(false);
const page = ref(1);
const pageSize = ref(50);
const pages = ref<AuditLogItem[][]>([]);
const cursors = ref<(number | null)[]>([null]);
const hasMoreByPage = ref<boolean[]>([]);
let loadSeq = 0;

const filters = reactive({
  action: "",
  resource_type: "",
  resource_id: "",
});

const pageSizeOptions = [
  { label: "20 / 页", value: 20 },
  { label: "50 / 页", value: 50 },
  { label: "100 / 页", value: 100 },
];

const queryParams = computed(() => {
  const params: Record<string, any> = { limit: pageSize.value };
  if (filters.action.trim()) params.action = filters.action.trim();
  if (filters.resource_type.trim()) params.resource_type = filters.resource_type.trim();
  if (filters.resource_id.trim()) params.resource_id = filters.resource_id.trim();
  return params;
});

const currentLogs = computed(() => pages.value[page.value - 1] || []);
const currentHasMore = computed(() => hasMoreByPage.value[page.value - 1] || false);
const canGoNext = computed(() => Boolean(pages.value[page.value]) || currentHasMore.value);

function resetPagination() {
  page.value = 1;
  pages.value = [];
  cursors.value = [null];
  hasMoreByPage.value = [];
}

async function loadPage(targetPage: number) {
  if (!adminToken.value) return;
  if (targetPage <= 0) return;
  const cursor = cursors.value[targetPage - 1];
  if (cursor === undefined) return;

  const seq = ++loadSeq;
  loading.value = true;
  try {
    const res = await fetchAuditLogs({ ...queryParams.value, cursor: cursor ?? undefined });
    if (seq !== loadSeq) return;
    pages.value[targetPage - 1] = res.items;
    hasMoreByPage.value[targetPage - 1] = res.has_more;
    cursors.value[targetPage] = res.next_cursor;
    page.value = targetPage;
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

async function reload() {
  resetPagination();
  await loadPage(1);
}

async function goPrev() {
  if (page.value <= 1) return;
  page.value -= 1;
}

async function goNext() {
  const target = page.value + 1;
  if (pages.value[target - 1]) {
    page.value = target;
    return;
  }
  if (!currentHasMore.value) return;
  await loadPage(target);
}

onMounted(async () => {
  await reload();
});

watch(adminToken, async (token) => {
  if (!token) {
    resetPagination();
    return;
  }
  await reload();
});

let reloadTimer: ReturnType<typeof setTimeout> | undefined;
watch(() => [filters.action, filters.resource_type, filters.resource_id, pageSize.value], () => {
  if (reloadTimer) clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => void reload(), 350);
});

// 列选择功能
const STORAGE_KEY_VISIBLE_COLUMNS = "fcam_audit_visible_columns";

const allColumnKeys = [
  "created_at",
  "action",
  "resource_type",
  "resource_id",
  "ip",
  "user_agent",
];

const columnLabels: Record<string, string> = {
  created_at: "时间",
  action: "操作",
  resource_type: "资源类型",
  resource_id: "资源ID",
  ip: "IP地址",
  user_agent: "User Agent",
};

// 从 localStorage 加载可见列配置
function loadVisibleColumns(): string[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEY_VISIBLE_COLUMNS);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed)) return parsed;
    }
  } catch {
    // ignore
  }
  // 默认显示所有列
  return [...allColumnKeys];
}

const visibleColumns = ref<string[]>(loadVisibleColumns());

// 保存可见列配置到 localStorage
watch(visibleColumns, (cols) => {
  try {
    localStorage.setItem(STORAGE_KEY_VISIBLE_COLUMNS, JSON.stringify(cols));
  } catch {
    // ignore
  }
}, { deep: true });

const columns = [
  {
    title: "时间",
    key: "created_at",
    width: 170,
    render: (row: AuditLogItem) => formatTimestamp(row.created_at)
  },
  { title: "action", key: "action", width: 220 },
  { title: "resource_type", key: "resource_type", width: 130 },
  { title: "resource_id", key: "resource_id", width: 140 },
  { title: "ip", key: "ip", width: 140 },
  {
    title: "user_agent",
    key: "user_agent",
    render: (row: AuditLogItem) => h("span", { style: "color: var(--text-tertiary)" }, row.user_agent || "-"),
  },
];

// 根据用户选择过滤列
const filteredColumns = computed(() => {
  return columns.filter(col => visibleColumns.value.includes(col.key));
});

// 列选择选项
const columnOptions = computed(() => {
  return allColumnKeys.map(key => ({
    label: columnLabels[key] || key,
    value: key,
  }));
});
</script>

<template>
  <n-card title="审计日志" size="small">
    <template #header-extra>
      <n-space>
        <n-dropdown trigger="click">
          <template #trigger>
            <n-button size="small">列</n-button>
          </template>
          <div style="padding: 12px; min-width: 200px">
            <n-checkbox-group v-model:value="visibleColumns">
              <n-space vertical>
                <n-checkbox v-for="opt in columnOptions" :key="opt.value" :value="opt.value" :label="opt.label" />
              </n-space>
            </n-checkbox-group>
          </div>
        </n-dropdown>
        <n-button size="small" :loading="loading" @click="reload">刷新</n-button>
      </n-space>
    </template>

    <n-space vertical>
      <n-space>
        <n-select v-model:value="pageSize" size="small" :options="pageSizeOptions" style="width: 120px" />
        <n-input v-model:value="filters.action" size="small" placeholder="action" style="width: 220px" />
        <n-input v-model:value="filters.resource_type" size="small" placeholder="resource_type" style="width: 160px" />
        <n-input v-model:value="filters.resource_id" size="small" placeholder="resource_id" style="width: 180px" />
      </n-space>

      <n-data-table
        :columns="filteredColumns as any"
        :data="currentLogs"
        :loading="loading"
        :pagination="false"
        size="small"
        striped
        :scroll-x="1100"
      />

      <n-space justify="space-between" align="center">
        <div class="muted" style="font-size: 12px">第 {{ page }} 页 · 本页 {{ currentLogs.length }} 条</div>
        <n-space>
          <n-button size="small" :disabled="page <= 1 || loading" @click="goPrev">上一页</n-button>
          <n-button size="small" :disabled="!canGoNext || loading" @click="goNext">下一页</n-button>
        </n-space>
      </n-space>
    </n-space>
  </n-card>
</template>
