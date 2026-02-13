<script setup lang="ts">
import { NCard } from "naive-ui";

type Accent = "primary" | "success" | "warning" | "danger" | "neutral";

type Props = {
  title: string;
  value: string | number;
  secondary?: string;
  accent?: Accent;
};

const props = withDefaults(defineProps<Props>(), {
  secondary: "",
  accent: "primary",
});
</script>

<template>
  <n-card size="small" class="stat-card" :class="`accent-${props.accent}`">
    <div class="accent-bar" aria-hidden="true" />
    <div class="stat-body">
      <div class="stat-title muted">{{ props.title }}</div>
      <div class="stat-value">{{ props.value }}</div>
      <div class="stat-secondary" :class="{ empty: !props.secondary }">
        {{ props.secondary || " " }}
      </div>
    </div>
  </n-card>
</template>

<style scoped>
.stat-card {
  position: relative;
  overflow: hidden;
  min-height: 118px;
  box-shadow: var(--shadow-md);
  border: 1px solid var(--border-color-light);
  background-color: var(--card-bg);
  background: var(--card-bg);
  backdrop-filter: blur(12px);
}

.accent-bar {
  position: absolute;
  inset: 0 0 auto 0;
  height: 3px;
  background: var(--primary-gradient);
  opacity: 0.95;
}

.stat-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.stat-title {
  font-size: 12px;
}

.stat-value {
  font-size: 28px;
  font-weight: 800;
  letter-spacing: -0.3px;
  line-height: 1.1;
}

.stat-secondary {
  font-size: 12px;
  color: var(--text-secondary);
  min-height: 16px;
}

.stat-secondary.empty {
  opacity: 0;
}

.accent-primary .accent-bar {
  background: var(--primary-gradient);
}

.accent-success .accent-bar {
  background: var(--success-gradient);
}

.accent-warning .accent-bar {
  background: var(--warning-gradient);
}

.accent-danger .accent-bar {
  background: var(--secondary-gradient);
}

.accent-neutral .accent-bar {
  background: linear-gradient(135deg, rgba(148, 163, 184, 0.9), rgba(148, 163, 184, 0.5));
}
</style>

