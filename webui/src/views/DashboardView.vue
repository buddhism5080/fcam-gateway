<script setup lang="ts">
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NGrid,
  NGridItem,
  NInputNumber,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";
import { computed, h, onMounted, reactive, ref, watch } from "vue";

import { fetchClients, type ClientItem } from "@/api/clients";
import {
  fetchClientUsage,
  fetchDashboardChart,
  fetchDashboardStats,
  fetchEncryptionStatus,
  fetchRuntimeScheduling,
  updateRuntimeScheduling,
  type ClientUsageItem,
  type DashboardChart,
  type DashboardStats,
  type EncryptionStatus,
  type RuntimeScheduling,
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
const clientUsage = ref<ClientUsageItem[]>([]);
const runtime = ref<RuntimeScheduling | null>(null);
const runtimeSaving = ref(false);

const clients = ref<ClientItem[]>([]);
const selectedClientId = ref<number>(0);

const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const runtimeForm = reactive({
  freshness_half_life_seconds: 21600,
  unknown_credit_baseline: 50,
  credit_workers: 4,
});

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

const usageColumns = [
  { title: "Client", key: "name", render: (r: ClientUsageItem) => `${r.name} (#${r.id})` },
  { title: "RPM", key: "rate_limit_per_min", width: 80 },
  { title: "并发", key: "max_concurrent", width: 70 },
  { title: "换Key重试", key: "max_retries", width: 90 },
  { title: "24h请求", key: "requests.total", width: 90, render: (r: ClientUsageItem) => r.requests.total },
  { title: "成功", key: "requests.success", width: 80, render: (r: ClientUsageItem) => r.requests.success },
  { title: "失败", key: "requests.failed", width: 80, render: (r: ClientUsageItem) => r.requests.failed },
  { title: "429", key: "requests.rate_limited", width: 70, render: (r: ClientUsageItem) => r.requests.rate_limited },
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
    chart.value = await fetchDashboardChart({ tz, clientId });
    const usage = await fetchClientUsage(24);
    clientUsage.value = usage.items || [];
    runtime.value = await fetchRuntimeScheduling();
    if (runtime.value) {
      runtimeForm.freshness_half_life_seconds = runtime.value.effective.freshness_half_life_seconds;
      runtimeForm.unknown_credit_baseline = runtime.value.effective.unknown_credit_baseline;
      runtimeForm.credit_workers = runtime.value.effective.credit_workers;
    }
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err), { duration: 5000 });
  } finally {
    loading.value = false;
  }
}

async function onSaveRuntime() {
  runtimeSaving.value = true;
  try {
    runtime.value = await updateRuntimeScheduling({
      freshness_half_life_seconds: runtimeForm.freshness_half_life_seconds,
      unknown_credit_baseline: runtimeForm.unknown_credit_baseline,
      credit_workers: runtimeForm.credit_workers,
    });
    message.success("调度参数已热更新（无需重启）");
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err));
  } finally {
    runtimeSaving.value = false;
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
    runtime.value = null;
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
        <request-trend-chart v-if="chart" :labels="chart.labels" :datasets="chart.datasets" />
        <div v-else class="muted" style="font-size: 13px">暂无数据</div>
      </n-card>

      <n-card style="margin-top: 12px" title="下游 Client 用量（24h）" size="small">
        <n-data-table :columns="usageColumns as any" :data="clientUsage" size="small" :bordered="false" />
      </n-card>

      <n-card style="margin-top: 12px" title="运行时调度参数（热更新，无需重启）" size="small">
        <template #header-extra>
          <n-tag size="small" :type="runtime?.http_connection_pool_enabled ? 'success' : 'default'">
            HTTP 连接池：{{ runtime?.http_connection_pool_enabled ? "开" : "关(默认)" }}
          </n-tag>
        </template>
        <n-form label-placement="left" label-width="180">
          <n-form-item label="freshness half-life (s)">
            <n-input-number v-model:value="runtimeForm.freshness_half_life_seconds" :min="60" :max="604800" />
          </n-form-item>
          <n-form-item label="unknown credit baseline">
            <n-input-number v-model:value="runtimeForm.unknown_credit_baseline" :min="0" :max="1000000" />
          </n-form-item>
          <n-form-item label="credit refresh workers">
            <n-input-number v-model:value="runtimeForm.credit_workers" :min="1" :max="64" />
          </n-form-item>
          <n-space>
            <n-button type="primary" :loading="runtimeSaving" @click="onSaveRuntime">应用</n-button>
            <span class="muted" style="font-size: 12px">
              连接池需改 config / 环境变量
              <code>FCAM_SECURITY__HTTP_CLIENT__CONNECTION_POOL_ENABLED=true</code> 后重启
            </span>
          </n-space>
        </n-form>
      </n-card>
    </n-spin>
  </n-space>
</template>
