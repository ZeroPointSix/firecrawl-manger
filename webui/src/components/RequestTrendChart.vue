<script setup lang="ts">
import { computed, ref } from "vue";

type Dataset = { label: string; color: string; data: number[] };

type Props = {
  labels: string[];
  datasets: Dataset[];
};

const props = defineProps<Props>();

const width = 860;
const height = 260;
const padding = { top: 24, right: 20, bottom: 42, left: 56 };

const plotWidth = width - padding.left - padding.right;
const plotHeight = height - padding.top - padding.bottom;

const maxY = computed(() => {
  const all = props.datasets.flatMap((d) => d.data);
  const m = Math.max(...all, 0);
  if (m <= 0) return 10;
  const step = Math.ceil(m / 4);
  return step * 4;
});

function x(i: number) {
  const n = Math.max(props.labels.length - 1, 1);
  return padding.left + (i / n) * plotWidth;
}

function y(v: number) {
  const ratio = v / maxY.value;
  return padding.top + (1 - ratio) * plotHeight;
}

const yTicks = computed(() => {
  const step = maxY.value / 4;
  return Array.from({ length: 5 }, (_v, idx) => ({
    idx,
    value: maxY.value - idx * step,
    y: padding.top + idx * (plotHeight / 4),
  }));
});

const xLabels = computed(() => {
  const n = props.labels.length;
  if (!n) return [];
  const step = Math.max(Math.ceil(n / 8), 1);
  return props.labels
    .map((raw, idx) => ({ raw, idx }))
    .filter((_v, i) => i % step === 0)
    .slice(0, 8);
});

function fmtTime(raw: string) {
  const d = new Date(raw);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
}

function linePoints(data: number[]) {
  return data.map((v, i) => `${x(i)},${y(v)}`).join(" ");
}

function areaPath(data: number[]) {
  if (!data.length) return "";
  const bottom = height - padding.bottom;
  const firstX = x(0);
  const lastX = x(data.length - 1);
  const top = data.map((v, i) => `${x(i)} ${y(v)}`).join(" L ");
  return `M ${firstX} ${bottom} L ${top} L ${lastX} ${bottom} Z`;
}

function gradientId(label: string) {
  return `grad_${label.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
}

const hoverIndex = ref<number | null>(null);

function onPointerMove(e: PointerEvent) {
  const svg = e.currentTarget as SVGSVGElement | null;
  if (!svg) return;
  const rect = svg.getBoundingClientRect();
  if (!rect.width || !rect.height) return;

  const px = ((e.clientX - rect.left) / rect.width) * width;
  const py = ((e.clientY - rect.top) / rect.height) * height;
  if (px < padding.left || px > width - padding.right || py < padding.top || py > height - padding.bottom) {
    hoverIndex.value = null;
    return;
  }

  const n = props.labels.length;
  if (!n) {
    hoverIndex.value = null;
    return;
  }

  const ratio = (px - padding.left) / plotWidth;
  const idx = Math.round(ratio * (n - 1));
  hoverIndex.value = Math.min(n - 1, Math.max(0, idx));
}

function onPointerLeave() {
  hoverIndex.value = null;
}

const hoverLabel = computed(() => {
  if (hoverIndex.value === null) return null;
  return props.labels[hoverIndex.value] || null;
});

const hoverValues = computed(() => {
  if (hoverIndex.value === null) return null;
  const idx = hoverIndex.value;
  return props.datasets.map((ds) => ({
    label: ds.label,
    color: ds.color,
    value: ds.data[idx] ?? 0,
    x: x(idx),
    y: y(ds.data[idx] ?? 0),
  }));
});

const tooltip = computed(() => {
  if (hoverIndex.value === null || !hoverValues.value || !hoverLabel.value) return null;

  const tooltipWidth = 190;
  const lineHeight = 18;
  const headerHeight = 18;
  const paddingY = 10;
  const heightPx = paddingY * 2 + headerHeight + hoverValues.value.length * lineHeight;

  const anchorX = x(hoverIndex.value);
  let xPos = anchorX + 12;
  if (xPos + tooltipWidth > width - padding.right) xPos = anchorX - tooltipWidth - 12;
  const yPos = padding.top + 8;

  return { x: xPos, y: yPos, w: tooltipWidth, h: heightPx, lineHeight, headerHeight, paddingY };
});
</script>

<template>
  <div style="width: 100%; overflow: auto">
    <svg
      :viewBox="`0 0 ${width} ${height}`"
      style="width: 100%; min-width: 740px; cursor: crosshair"
      @pointermove="onPointerMove"
      @pointerleave="onPointerLeave"
    >
      <defs>
        <linearGradient v-for="ds in datasets" :id="gradientId(ds.label)" :key="ds.label" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" :stop-color="ds.color" stop-opacity="0.28" />
          <stop offset="100%" :stop-color="ds.color" stop-opacity="0.02" />
        </linearGradient>
      </defs>

      <rect x="0" y="0" :width="width" :height="height" fill="var(--chart-bg)" rx="12" />

      <!-- axes -->
      <line
        :x1="padding.left"
        :y1="padding.top"
        :x2="padding.left"
        :y2="height - padding.bottom"
        stroke="var(--chart-axis)"
        stroke-width="2"
      />
      <line
        :x1="padding.left"
        :y1="height - padding.bottom"
        :x2="width - padding.right"
        :y2="height - padding.bottom"
        stroke="var(--chart-axis)"
        stroke-width="2"
      />

      <!-- y ticks -->
      <g v-for="t in yTicks" :key="t.idx">
        <line
          :x1="padding.left"
          :x2="width - padding.right"
          :y1="t.y"
          :y2="t.y"
          stroke="var(--chart-grid)"
          stroke-width="1"
        />
        <text
          :x="padding.left - 10"
          :y="t.y + 4"
          text-anchor="end"
          font-size="11"
          fill="var(--chart-text)"
        >
          {{ Math.round(t.value) }}
        </text>
      </g>

      <!-- lines -->
      <g v-for="ds in datasets" :key="ds.label">
        <path :d="areaPath(ds.data)" :fill="`url(#${gradientId(ds.label)})`" />
        <polyline
          :points="linePoints(ds.data)"
          fill="none"
          :stroke="ds.color"
          stroke-width="2.5"
          stroke-linejoin="round"
          stroke-linecap="round"
        />
      </g>

      <!-- x labels -->
      <g v-for="item in xLabels" :key="item.idx">
        <text
          :x="x(item.idx)"
          :y="height - padding.bottom + 22"
          text-anchor="middle"
          font-size="11"
          fill="var(--chart-text)"
        >
          {{ fmtTime(item.raw) }}
        </text>
      </g>

      <!-- legend -->
      <g :transform="`translate(${padding.left},${padding.top - 8})`">
        <g v-for="(ds, idx) in datasets" :key="ds.label" :transform="`translate(${idx * 120},0)`">
          <circle cx="6" cy="6" r="5" :fill="ds.color" />
          <text x="16" y="10" font-size="12" fill="var(--text-primary)">{{ ds.label }}</text>
        </g>
      </g>

      <!-- hover -->
      <g v-if="hoverIndex !== null && hoverValues">
        <line
          :x1="x(hoverIndex)"
          :x2="x(hoverIndex)"
          :y1="padding.top"
          :y2="height - padding.bottom"
          stroke="var(--chart-axis)"
          stroke-width="1"
          stroke-dasharray="4 4"
          opacity="0.9"
        />
        <g v-for="hv in hoverValues" :key="hv.label">
          <circle :cx="hv.x" :cy="hv.y" r="5" :fill="hv.color" stroke="white" stroke-width="2" />
        </g>

        <g v-if="tooltip && hoverLabel" :transform="`translate(${tooltip.x},${tooltip.y})`">
          <rect
            x="0"
            y="0"
            :width="tooltip.w"
            :height="tooltip.h"
            rx="10"
            fill="var(--chart-legend-bg)"
            stroke="var(--border-color-light)"
          />
          <text x="12" y="22" font-size="12" fill="var(--chart-legend-text)" style="font-weight: 700">
            {{ fmtTime(hoverLabel) }}
          </text>
          <g
            v-for="(hv, idx) in hoverValues"
            :key="hv.label"
            :transform="`translate(0, ${tooltip.paddingY + tooltip.headerHeight + idx * tooltip.lineHeight})`"
          >
            <circle cx="14" cy="10" r="5" :fill="hv.color" />
            <text x="26" y="14" font-size="12" fill="var(--chart-legend-secondary)">{{ hv.label }}: {{ hv.value }}</text>
          </g>
        </g>
      </g>
    </svg>
  </div>
</template>
