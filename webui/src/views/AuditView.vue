<script setup lang="ts">
import { NButton, NCard, NDataTable, NInput, NSpace, useMessage } from "naive-ui";
import { computed, h, onMounted, reactive, ref, watch } from "vue";

import { fetchAuditLogs, type AuditLogItem } from "@/api/logs";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken } from "@/state/adminAuth";

const message = useMessage();

const loading = ref(false);
const logs = ref<AuditLogItem[]>([]);
const cursor = ref<number | null>(null);
const hasMore = ref(false);

const filters = reactive({
  action: "",
  resource_type: "",
  resource_id: "",
});

const queryParams = computed(() => {
  const params: Record<string, any> = { limit: 50 };
  if (cursor.value) params.cursor = cursor.value;
  if (filters.action.trim()) params.action = filters.action.trim();
  if (filters.resource_type.trim()) params.resource_type = filters.resource_type.trim();
  if (filters.resource_id.trim()) params.resource_id = filters.resource_id.trim();
  return params;
});

async function loadFirstPage() {
  if (!adminToken.value) return;
  loading.value = true;
  try {
    cursor.value = null;
    const res = await fetchAuditLogs({ ...queryParams.value, cursor: undefined });
    logs.value = res.items;
    cursor.value = res.next_cursor;
    hasMore.value = res.has_more;
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    loading.value = false;
  }
}

async function loadMore() {
  if (!adminToken.value) return;
  if (!cursor.value) return;
  loading.value = true;
  try {
    const res = await fetchAuditLogs(queryParams.value);
    logs.value = logs.value.concat(res.items);
    cursor.value = res.next_cursor;
    hasMore.value = res.has_more;
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await loadFirstPage();
});

watch(adminToken, async (token) => {
  if (!token) {
    logs.value = [];
    cursor.value = null;
    hasMore.value = false;
    return;
  }
  await loadFirstPage();
});

watch(
  () => [filters.action, filters.resource_type, filters.resource_id],
  async () => {
    await loadFirstPage();
  }
);

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
        <n-button size="small" :loading="loading" @click="loadFirstPage">刷新</n-button>
      </n-space>
    </template>

    <n-space vertical>
      <n-space>
        <n-input v-model:value="filters.action" size="small" placeholder="action" style="width: 220px" />
        <n-input v-model:value="filters.resource_type" size="small" placeholder="resource_type" style="width: 160px" />
        <n-input v-model:value="filters.resource_id" size="small" placeholder="resource_id" style="width: 180px" />
      </n-space>

      <n-data-table
        :columns="columns as any"
        :data="logs"
        :loading="loading"
        :pagination="false"
        size="small"
        striped
        :scroll-x="1100"
      />

      <n-space justify="end">
        <n-button v-if="hasMore" size="small" :loading="loading" @click="loadMore">加载更多</n-button>
      </n-space>
    </n-space>
  </n-card>
</template>
