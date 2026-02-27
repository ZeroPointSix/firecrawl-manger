<script setup lang="ts">
import { NButton, NCard, NCheckboxGroup, NDataTable, NDropdown, NInput, NModal, NSelect, NSpace, NTag, useMessage } from "naive-ui";
import { computed, h, onMounted, reactive, ref, watch } from "vue";

import { fetchRequestLogs, type RequestLogItem } from "@/api/logs";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken } from "@/state/adminAuth";
import { formatTimestamp } from "@/utils/time";

const message = useMessage();

const loading = ref(false);
const page = ref(1);
const pageSize = ref(50);
const pages = ref<RequestLogItem[][]>([]);
const cursors = ref<(number | null)[]>([null]);
const hasMoreByPage = ref<boolean[]>([]);
let loadSeq = 0;

const filters = reactive({
  client_id: "",
  endpoint: "",
  success: "",
  level: "",
  q: "",
});

const successOptions = [
  { label: "全部", value: "" },
  { label: "成功", value: "true" },
  { label: "失败", value: "false" },
];

const levelOptions = [
  { label: "全部级别", value: "" },
  { label: "INFO", value: "info" },
  { label: "WARN", value: "warn" },
  { label: "ERROR", value: "error" },
];

const pageSizeOptions = [
  { label: "20 / 页", value: 20 },
  { label: "50 / 页", value: 50 },
  { label: "100 / 页", value: 100 },
];

const endpointOptions = [
  { label: "全部", value: "" },
  { label: "scrape", value: "scrape" },
  { label: "crawl", value: "crawl" },
  { label: "crawl_status", value: "crawl_status" },
  { label: "search", value: "search" },
  { label: "agent", value: "agent" },
];

const queryParams = computed(() => {
  const params: Record<string, any> = { limit: pageSize.value };
  if (filters.client_id.trim()) params.client_id = Number(filters.client_id);
  if (filters.endpoint) params.endpoint = filters.endpoint;
  if (filters.success) params.success = filters.success === "true";
  if (filters.level) params.level = filters.level;
  if (filters.q.trim()) params.q = filters.q.trim();
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
    const res = await fetchRequestLogs({ ...queryParams.value, cursor: cursor ?? undefined });
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
watch(
  () => [filters.client_id, filters.endpoint, filters.success, filters.level, filters.q, pageSize.value],
  () => {
    if (reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = setTimeout(() => void reload(), 350);
  }
);

function levelTag(row: RequestLogItem) {
  const t = row.level.toUpperCase();
  const type = row.level === "error" ? "error" : row.level === "warn" ? "warning" : "success";
  return h(NTag, { size: "small", type: type as any }, { default: () => t });
}

function redactText(text: string) {
  const bearerRedacted = text.replace(/\bbearer\s+[a-z0-9._\-~+/]+=*\b/gi, "Bearer [REDACTED]");
  const firecrawlRedacted = bearerRedacted.replace(/\bfc-[A-Za-z0-9]{8,}\b/g, "fc-[REDACTED]");
  return firecrawlRedacted;
}

function formatJsonMaybe(value: string | null): string {
  if (!value) return "-";
  const safe = redactText(value);
  try {
    return JSON.stringify(JSON.parse(safe), null, 2);
  } catch {
    return safe;
  }
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    message.success("已复制");
  } catch {
    message.warning("复制失败（请手动复制）");
  }
}

const showDetail = ref(false);
const detailRow = ref<RequestLogItem | null>(null);

function openDetail(row: RequestLogItem) {
  detailRow.value = row;
  showDetail.value = true;
}

watch(showDetail, (v) => {
  if (v) return;
  detailRow.value = null;
});

// 列选择功能
const STORAGE_KEY_VISIBLE_COLUMNS = "fcam_logs_visible_columns";

const allColumnKeys = [
  "created_at",
  "level",
  "endpoint",
  "status_code",
  "success",
  "client_id",
  "api_key_masked",
  "retry_count",
  "error_message",
  "detail",
  "request_id",
];

const columnLabels: Record<string, string> = {
  created_at: "时间",
  level: "级别",
  endpoint: "端点",
  status_code: "状态码",
  success: "成功",
  client_id: "Client ID",
  api_key_masked: "API Key",
  retry_count: "重试次数",
  error_message: "错误信息",
  detail: "详情",
  request_id: "Request ID",
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
    render: (row: RequestLogItem) => formatTimestamp(row.created_at)
  },
  { title: "level", key: "level", width: 90, render: (row: RequestLogItem) => levelTag(row) },
  { title: "endpoint", key: "endpoint", width: 110 },
  { title: "status", key: "status_code", width: 80 },
  {
    title: "ok",
    key: "success",
    width: 70,
    render: (row: RequestLogItem) => {
      const ok = row.success;
      const text = ok === true ? "OK" : ok === false ? "ERR" : "-";
      const color =
        ok === true ? "var(--success-color)" : ok === false ? "var(--error-color)" : "var(--text-tertiary)";
      return h("span", { style: `color:${color}` }, text);
    },
  },
  { title: "client_id", key: "client_id", width: 90 },
  {
    title: "key",
    key: "api_key_masked",
    width: 120,
    render: (row: RequestLogItem) =>
      row.api_key_masked ? h("span", { class: "mono" }, row.api_key_masked) : "-",
  },
  { title: "retry", key: "retry_count", width: 70 },
  { title: "error_code", key: "error_message" },
  {
    title: "详情",
    key: "detail",
    width: 90,
    render: (row: RequestLogItem) =>
      h(
        NButton,
        { size: "tiny", tertiary: true, onClick: () => openDetail(row) },
        { default: () => "查看" }
      ),
  },
  {
    title: "request_id",
    key: "request_id",
    width: 220,
    render: (row: RequestLogItem) => h("span", { class: "mono" }, row.request_id),
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
  <n-card title="请求日志" size="small">
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
      <n-space wrap>
        <n-select v-model:value="pageSize" size="small" :options="pageSizeOptions" style="width: 120px" />
        <n-select v-model:value="filters.level" size="small" :options="levelOptions" style="width: 130px" />
        <n-input v-model:value="filters.client_id" size="small" placeholder="client_id" style="width: 120px" />
        <n-select v-model:value="filters.endpoint" size="small" :options="endpointOptions" style="width: 160px" />
        <n-select v-model:value="filters.success" size="small" :options="successOptions" style="width: 120px" />
        <n-input
          v-model:value="filters.q"
          size="small"
          placeholder="搜索 request_id / endpoint / error..."
          style="width: 360px"
        />
      </n-space>

      <n-data-table
        :columns="filteredColumns as any"
        :data="currentLogs"
        :loading="loading"
        :pagination="false"
        size="small"
        striped
        :scroll-x="1200"
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

  <n-modal v-model:show="showDetail" preset="card" style="max-width: 860px">
    <n-card title="日志详情" :bordered="false">
      <n-space v-if="detailRow" vertical>
        <n-space align="center" justify="space-between">
          <div class="muted" style="font-size: 12px">
            <span class="mono">request_id={{ detailRow.request_id }}</span>
          </div>
          <n-button size="tiny" @click="copyText(detailRow.request_id)">复制 request_id</n-button>
        </n-space>

        <n-space wrap size="small" class="muted" style="font-size: 12px">
          <div>time={{ formatTimestamp(detailRow.created_at) }}</div>
          <div>endpoint={{ detailRow.endpoint }}</div>
          <div>method={{ detailRow.method }}</div>
          <div>status={{ detailRow.status_code ?? "-" }}</div>
          <div>latency_ms={{ detailRow.response_time_ms ?? "-" }}</div>
          <div>retry={{ detailRow.retry_count }}</div>
          <div>client_id={{ detailRow.client_id ?? "-" }}</div>
          <div>key={{ detailRow.api_key_masked ?? "-" }}</div>
          <div>error_code={{ detailRow.error_message ?? "-" }}</div>
          <div>idempotency_key={{ detailRow.idempotency_key ?? "-" }}</div>
        </n-space>

        <n-card size="small" title="error_details（已脱敏/截断）">
          <pre class="mono" style="white-space: pre-wrap; margin: 0">{{ formatJsonMaybe(detailRow.error_details) }}</pre>
        </n-card>

        <n-space justify="end">
          <n-button @click="showDetail = false">关闭</n-button>
        </n-space>
      </n-space>
    </n-card>
  </n-modal>
</template>
