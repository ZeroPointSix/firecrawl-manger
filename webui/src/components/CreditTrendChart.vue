<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { NButton, NDatePicker, NSpace, useMessage } from "naive-ui";

import { getCreditsHistory } from "@/api/credits";
import { getFcamErrorMessage } from "@/api/http";
import RequestTrendChart from "@/components/RequestTrendChart.vue";

const props = defineProps<{
  keyId: number;
}>();

const message = useMessage();
const loading = ref(false);
const snapshots = ref<Array<{ snapshot_at: string; remaining_credits: number; plan_credits: number }>>([]);
const timeRange = ref<[number, number] | null>(null);

const labels = computed(() => snapshots.value.map((s) => s.snapshot_at));
const remainingSeries = computed(() => snapshots.value.map((s) => s.remaining_credits));
const planSeries = computed(() => snapshots.value.map((s) => s.plan_credits));

async function loadHistory() {
  loading.value = true;
  try {
    const params: { limit: number; since?: string; until?: string } = { limit: 100 };
    if (timeRange.value) {
      params.since = new Date(timeRange.value[0]).toISOString();
      params.until = new Date(timeRange.value[1]).toISOString();
    }
    const res = await getCreditsHistory(props.keyId, params);
    const rows = (res.snapshots || [])
      .filter((s) => !!s.snapshot_at)
      .slice()
      .sort((a, b) => a.snapshot_at.localeCompare(b.snapshot_at));
    snapshots.value = rows as any;
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void loadHistory();
});

watch(
  () => props.keyId,
  () => {
    void loadHistory();
  }
);
</script>

<template>
  <div>
    <n-space align="center" justify="space-between" style="margin-bottom: 10px">
      <div style="font-weight: 800">额度消费趋势</div>
      <n-space align="center">
        <n-date-picker
          v-model:value="timeRange"
          type="datetimerange"
          clearable
          :disabled="loading"
          @update:value="loadHistory"
        />
        <n-button size="small" :loading="loading" @click="loadHistory">刷新</n-button>
      </n-space>
    </n-space>

    <div v-if="!snapshots.length" class="muted" style="font-size: 13px">暂无快照数据（可先手动刷新一次额度）。</div>

    <request-trend-chart
      v-else
      :labels="labels"
      :datasets="[
        { label: '剩余额度', color: '#18a058', data: remainingSeries },
        { label: '计划额度', color: '#909399', data: planSeries }
      ]"
    />
  </div>
</template>
