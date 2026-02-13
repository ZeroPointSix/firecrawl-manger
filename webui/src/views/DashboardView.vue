<script setup lang="ts">
import { NAlert, NButton, NCard, NGrid, NGridItem, NSelect, NSpace, NSpin, NTag, useMessage } from "naive-ui";
import { computed, onMounted, ref, watch } from "vue";

import { fetchClients, type ClientItem } from "@/api/clients";
import {
  fetchDashboardChart,
  fetchDashboardStats,
  fetchEncryptionStatus,
  type DashboardChart,
  type DashboardStats,
  type EncryptionStatus,
} from "@/api/dashboard";
import RequestTrendChart from "@/components/RequestTrendChart.vue";
import StatCard from "@/components/StatCard.vue";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken, connectionStatus, verifyAdminToken } from "@/state/adminAuth";

const message = useMessage();

const loading = ref(false);
const encryption = ref<EncryptionStatus | null>(null);
const stats = ref<DashboardStats | null>(null);
const chart = ref<DashboardChart | null>(null);

const clients = ref<ClientItem[]>([]);
const selectedClientId = ref<number>(0);

const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const clientOptions = computed(() => [
  { label: "全部 Clients", value: 0 },
  ...clients.value.map((c) => ({ label: `${c.name} (#${c.id})`, value: c.id })),
]);

const chartTotals = computed(() => {
  if (!chart.value) return null;
  const totals: Record<string, number> = {};
  for (const ds of chart.value.datasets) {
    totals[ds.label] = ds.data.reduce((sum, v) => sum + (Number.isFinite(v) ? v : 0), 0);
  }
  return totals;
});

async function loadAll() {
  if (!adminToken.value) return;
  loading.value = true;
  try {
    const clientId = selectedClientId.value || undefined;
    encryption.value = await fetchEncryptionStatus();
    stats.value = await fetchDashboardStats(clientId);
    chart.value = await fetchDashboardChart({ tz, clientId });
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  if (adminToken.value) await verifyAdminToken();

  if (!adminToken.value) return;
  try {
    clients.value = (await fetchClients()).filter((c) => c.is_active);
  } catch (err: unknown) {
    message.warning(getFcamErrorMessage(err));
  }
  await loadAll();
});

watch(adminToken, async (token) => {
  if (!token) {
    encryption.value = null;
    stats.value = null;
    chart.value = null;
    clients.value = [];
    selectedClientId.value = 0;
    return;
  }

  await verifyAdminToken();
  try {
    clients.value = (await fetchClients()).filter((c) => c.is_active);
  } catch (err: unknown) {
    message.warning(getFcamErrorMessage(err));
  }
  await loadAll();
});

watch(selectedClientId, async () => {
  await loadAll();
});
</script>

<template>
  <n-space vertical size="large">
    <n-alert v-if="!adminToken" type="warning" title="未连接 Admin Token">
      右上角点击「连接」后再查看仪表盘数据。
    </n-alert>

    <n-alert v-else-if="connectionStatus === 'unauthorized'" type="error" title="Admin Token 未授权">
      请确认使用正确的 <span class="mono">FCAM_ADMIN_TOKEN</span>。
    </n-alert>

    <n-alert
      v-if="encryption && encryption.master_key_configured && encryption.has_decrypt_failures"
      type="error"
      title="检测到不可解密的 Key"
    >
      {{ encryption.suggestion || "请检查 FCAM_MASTER_KEY 是否与加密时一致。" }}
    </n-alert>

    <n-card size="small">
      <n-space align="center" justify="space-between">
        <div style="font-weight: 800">Dashboard</div>
        <n-space align="center">
          <n-select
            v-model:value="selectedClientId"
            size="small"
            style="min-width: 220px"
            :options="clientOptions"
          />
          <n-button size="small" :loading="loading" @click="loadAll">刷新</n-button>
        </n-space>
      </n-space>
    </n-card>

  <n-spin :show="loading">
      <n-grid cols="2 s:4" :x-gap="12" :y-gap="12" responsive="screen">
        <n-grid-item>
          <stat-card
            title="密钥数量"
            :value="stats?.keys.total ?? '-'"
            :secondary="stats ? `失败/不可解密：${stats.keys.failed}` : ''"
            accent="primary"
          />
        </n-grid-item>

        <n-grid-item>
          <stat-card title="Clients 数量" :value="stats?.clients.total ?? '-'" accent="neutral" />
        </n-grid-item>

        <n-grid-item>
          <stat-card
            title="24 小时请求"
            :value="stats?.requests_24h.total ?? '-'"
            :secondary="
              stats
                ? `成功：${stats.requests_24h.total - stats.requests_24h.failed} · 失败：${stats.requests_24h.failed}`
                : ''
            "
            accent="success"
          />
        </n-grid-item>

        <n-grid-item>
          <stat-card
            title="24 小时错误率"
            :value="stats ? `${stats.requests_24h.error_rate.toFixed(2)}%` : '-'"
            :secondary="stats ? `失败：${stats.requests_24h.failed}` : ''"
            accent="danger"
          />
        </n-grid-item>
      </n-grid>

      <n-card style="margin-top: 12px" title="24 小时请求趋势（1h bucket，本地时区展示）" size="small">
        <template #header-extra>
          <n-space align="center" size="small">
            <n-tag v-if="chartTotals" size="small" type="success">success={{ chartTotals.success ?? 0 }}</n-tag>
            <n-tag v-if="chartTotals" size="small" type="error">failed={{ chartTotals.failed ?? 0 }}</n-tag>
            <span class="mono muted" style="font-size: 12px">tz={{ tz }}</span>
          </n-space>
        </template>
        <request-trend-chart
          v-if="chart"
          :labels="chart.labels"
          :datasets="chart.datasets"
        />
        <div v-else class="muted" style="font-size: 13px">暂无数据</div>
      </n-card>
    </n-spin>
  </n-space>
</template>
