<script setup lang="ts">
import { computed, ref } from "vue";
import { NButton, NProgress, NSpace, NTag, useMessage } from "naive-ui";

import { refreshKeyCredits } from "@/api/credits";
import { getFcamErrorMessage } from "@/api/http";
import { formatRelativeTime, formatTimestamp } from "@/utils/time";

const props = defineProps<{
  keyId: number;
  remainingCredits: number | null;
  planCredits: number | null;
  totalCredits?: number | null;
  lastUpdateAt: string | null;
  isEstimated?: boolean;
}>();

const emit = defineEmits<{
  (e: "refresh"): void;
}>();

const message = useMessage();
const refreshing = ref(false);

const usagePercentage = computed(() => {
  const total = props.totalCredits ?? 0;
  const remaining = props.remainingCredits ?? 0;
  if (!total) return 0;
  const used = total - remaining;
  return Math.max(0, Math.min(100, (used / total) * 100));
});

const isExhausted = computed(() => {
  const remaining = props.remainingCredits;
  if (remaining == null) return false;
  return remaining <= 0;
});

const status = computed(() => {
  const total = props.totalCredits ?? 0;
  const remaining = props.remainingCredits ?? 0;
  if (!total) return "default";
  if (remaining <= 0) return "default";
  const pctRemaining = (remaining / total) * 100;
  if (pctRemaining < 20) return "error";
  if (pctRemaining < 50) return "warning";
  return "success";
});

const progressColor = computed(() => {
  const total = props.totalCredits ?? 0;
  const remaining = props.remainingCredits ?? 0;
  if (!total) return undefined;
  if (remaining <= 0) return "#909399";
  const pctRemaining = (remaining / total) * 100;
  if (pctRemaining < 20) return "#d03050";
  if (pctRemaining < 50) return "#f0a020";
  return "#18a058";
});

async function handleRefresh() {
  refreshing.value = true;
  try {
    await refreshKeyCredits(props.keyId);
    emit("refresh");
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    refreshing.value = false;
  }
}
</script>

<template>
  <div style="display: flex; flex-direction: column; gap: 6px">
    <n-progress
      type="line"
      :percentage="usagePercentage"
      :status="status as any"
      :color="progressColor"
      :height="8"
    />

    <n-space align="center" justify="space-between" :wrap="false">
      <div class="muted" style="font-size: 12px; white-space: nowrap">
        <template v-if="totalCredits == null || remainingCredits == null">未初始化</template>
        <template v-else>
          <span v-if="isEstimated">~</span>{{ remainingCredits.toLocaleString() }} / {{ totalCredits.toLocaleString() }}
          <n-tag v-if="isExhausted" size="small" style="margin-left: 6px">已耗尽</n-tag>
        </template>
      </div>
      <n-button size="tiny" :loading="refreshing" @click="handleRefresh">刷新</n-button>
    </n-space>

    <div class="muted" style="font-size: 12px; white-space: nowrap" :title="formatTimestamp(lastUpdateAt)">
      最后更新: {{ formatRelativeTime(lastUpdateAt) }}
    </div>
  </div>
</template>
