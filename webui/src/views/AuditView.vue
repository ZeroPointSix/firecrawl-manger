<script setup lang="ts">
import { NButton, NCard, NDataTable, NInput, NSelect, NSpace, useMessage } from "naive-ui";
import { computed, h, onMounted, reactive, ref, watch } from "vue";

import { fetchAuditLogs, type AuditLogItem } from "@/api/logs";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken } from "@/state/adminAuth";

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

const columns = [
  { title: "时间", key: "created_at", width: 190 },
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
</script>

<template>
  <n-card title="审计日志" size="small">
    <template #header-extra>
      <n-space>
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
        :columns="columns as any"
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
