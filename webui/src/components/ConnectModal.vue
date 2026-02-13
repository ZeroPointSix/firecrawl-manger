<script setup lang="ts">
import { NAlert, NButton, NCard, NCheckbox, NInput, NModal, NSpace, useMessage } from "naive-ui";
import { computed, ref, watch } from "vue";

import {
  adminToken,
  connectAdminToken,
  connectionStatus,
  lastConnectionError,
  verifyAdminToken,
} from "@/state/adminAuth";

type Props = { show: boolean };
const props = defineProps<Props>();
const emit = defineEmits<{ (e: "update:show", value: boolean): void }>();

const message = useMessage();

const token = ref("");
const persist = ref(true);
const submitting = ref(false);

const statusLabel = computed(() => {
  if (connectionStatus.value === "ok") return "已连接";
  if (connectionStatus.value === "unauthorized") return "未授权";
  if (connectionStatus.value === "error") return "连接失败";
  if (connectionStatus.value === "unknown") return "待验证";
  return "未连接";
});

watch(
  () => props.show,
  (v) => {
    if (!v) return;
    token.value = adminToken.value || "";
  }
);

async function onVerify() {
  submitting.value = true;
  try {
    const ok = await verifyAdminToken();
    if (ok) message.success("连接成功");
    else message.warning(lastConnectionError.value || "连接失败");
  } finally {
    submitting.value = false;
  }
}

async function onConnect() {
  submitting.value = true;
  try {
    await connectAdminToken(token.value, { persist: persist.value });
    if (connectionStatus.value === "ok") {
      message.success("连接成功");
      emit("update:show", false);
      return;
    }
    message.warning(lastConnectionError.value || "连接失败");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <n-modal :show="show" preset="card" style="max-width: 520px" @update:show="(v) => emit('update:show', v)">
      <n-card title="连接 Admin Token" :bordered="false">
      <n-space vertical>
        <div class="muted" style="font-size: 13px">
          该 Token 仅用于调用 <span class="mono">/admin/*</span> 控制面接口。
        </div>

        <n-input
          v-model:value="token"
          type="password"
          placeholder="FCAM_ADMIN_TOKEN"
          :disabled="submitting"
        />

        <n-checkbox v-model:checked="persist" :disabled="submitting">保存在本机（localStorage）</n-checkbox>

        <n-alert v-if="connectionStatus !== 'disconnected'" :type="connectionStatus === 'ok' ? 'success' : 'warning'">
          状态：{{ statusLabel }}
          <template v-if="lastConnectionError">
            <div style="margin-top: 6px">{{ lastConnectionError }}</div>
          </template>
        </n-alert>

        <n-space justify="end">
          <n-button :disabled="submitting" @click="emit('update:show', false)">取消</n-button>
          <n-button :loading="submitting" @click="onVerify">验证</n-button>
          <n-button type="primary" :loading="submitting" @click="onConnect">保存并连接</n-button>
        </n-space>
      </n-space>
    </n-card>
  </n-modal>
</template>
