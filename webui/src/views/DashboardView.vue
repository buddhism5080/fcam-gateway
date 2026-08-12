<script setup lang="ts">
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NGrid,
  NGridItem,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";
import { computed, onMounted, ref, watch } from "vue";

import { fetchClients, type ClientItem } from "@/api/clients";
import {
  fetchClientUsage,
  fetchDashboardChart,
  fetchDashboardStats,
  fetchEncryptionStatus,
  type ClientUsageItem,
  type DashboardChart,
  type DashboardStats,
  type EncryptionStatus,
} from "@/api/dashboard";
import RequestTrendChart from "@/components/RequestTrendChart.vue";
import StatCard from "@/components/StatCard.vue";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken, connectionStatus, verifyAdminToken } from "@/state/adminAuth";
import { displayTimezone } from "@/state/timezone";

const message = useMessage();

const loading = ref(false);
const encryption = ref<EncryptionStatus | null>(null);
const stats = ref<DashboardStats | null>(null);
const chart = ref<DashboardChart | null>(null);
const clientUsage = ref<ClientUsageItem[]>([]);

const clients = ref<ClientItem[]>([]);
const selectedClientId = ref<number>(0);

const clientOptions = computed(() => [
  { label: "全部客户端", value: 0 },
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

const usageColumns = [
  { title: "客户端", key: "name", render: (r: ClientUsageItem) => `${r.name} (#${r.id})` },
  { title: "RPM", key: "rate_limit_per_min", width: 80 },
  { title: "并发", key: "max_concurrent", width: 70 },
  { title: "换密钥重试", key: "max_retries", width: 100 },
  { title: "24h请求", key: "requests.total", width: 90, render: (r: ClientUsageItem) => r.requests.total },
  { title: "成功", key: "requests.success", width: 80, render: (r: ClientUsageItem) => r.requests.success },
  { title: "失败", key: "requests.failed", width: 80, render: (r: ClientUsageItem) => r.requests.failed },
  { title: "限流(429)", key: "requests.rate_limited", width: 90, render: (r: ClientUsageItem) => r.requests.rate_limited },
  { title: "重试合计", key: "requests.retry_sum", width: 90, render: (r: ClientUsageItem) => r.requests.retry_sum },
  {
    title: "错误率",
    key: "requests.error_rate",
    width: 90,
    render: (r: ClientUsageItem) => `${r.requests.error_rate.toFixed(2)}%`,
  },
];

async function loadAll() {
  if (!adminToken.value) return;
  loading.value = true;
  try {
    const clientId = selectedClientId.value || undefined;
    encryption.value = await fetchEncryptionStatus();
    stats.value = await fetchDashboardStats(clientId);
    chart.value = await fetchDashboardChart({ tz: displayTimezone.value, clientId });
    const usage = await fetchClientUsage(24);
    clientUsage.value = usage.items || [];
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
    clientUsage.value = [];
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

watch(displayTimezone, async () => {
  if (adminToken.value) await loadAll();
});
</script>

<template>
  <n-space vertical size="large">
    <n-alert v-if="!adminToken" type="warning" title="未连接管理令牌">
      右上角点击「连接」后再查看仪表盘数据。
    </n-alert>

    <n-alert v-else-if="connectionStatus === 'unauthorized'" type="error" title="管理令牌未授权">
      请确认使用正确的 <span class="mono">FCAM_ADMIN_TOKEN</span>。
    </n-alert>

    <n-alert
      v-if="encryption && encryption.master_key_configured && encryption.has_decrypt_failures"
      type="error"
      title="检测到不可解密的密钥"
    >
      {{ encryption.suggestion || "请检查 FCAM_MASTER_KEY 是否与加密时一致。" }}
    </n-alert>

    <n-card size="small">
      <n-space align="center" justify="space-between">
        <div style="font-weight: 800">仪表盘</div>
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
          <stat-card title="客户端数量" :value="stats?.clients.total ?? '-'" accent="neutral" />
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

      <n-card style="margin-top: 12px" title="24 小时请求趋势（1 小时分桶，按显示时区）" size="small">
        <template #header-extra>
          <n-space align="center" size="small">
            <n-tag v-if="chartTotals" size="small" type="success">成功={{ chartTotals.success ?? 0 }}</n-tag>
            <n-tag v-if="chartTotals" size="small" type="error">失败={{ chartTotals.failed ?? 0 }}</n-tag>
            <span class="mono muted" style="font-size: 12px">时区={{ displayTimezone }}</span>
          </n-space>
        </template>
        <request-trend-chart v-if="chart" :labels="chart.labels" :datasets="chart.datasets" />
        <div v-else class="muted" style="font-size: 13px">暂无数据</div>
      </n-card>

      <n-card style="margin-top: 12px" title="下游客户端用量（24h）" size="small">
        <n-data-table :columns="usageColumns as any" :data="clientUsage" size="small" :bordered="false" />
      </n-card>

      <n-alert style="margin-top: 12px" type="info" :bordered="false">
        调度 / 连接池 / 额度刷新间隔等运行参数已移至顶部导航「参数设置」。
      </n-alert>
    </n-spin>
  </n-space>
</template>
