<script setup lang="ts">
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInputNumber,
  NSpace,
  NSpin,
  NSwitch,
  NTag,
  useMessage,
} from "naive-ui";
import { onMounted, reactive, ref, watch } from "vue";

import {
  fetchRuntimeScheduling,
  updateRuntimeScheduling,
  type RuntimeScheduling,
} from "@/api/dashboard";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken, connectionStatus, verifyAdminToken } from "@/state/adminAuth";

const message = useMessage();
const loading = ref(false);
const saving = ref(false);
const runtime = ref<RuntimeScheduling | null>(null);

const form = reactive({
  freshness_half_life_seconds: 21600,
  unknown_credit_baseline: 50,
  credit_workers: 4,
  http_connection_pool_enabled: false,
  credit_batch_size: 10,
  credit_batch_delay_seconds: 5,
  credit_refresh_check_interval_seconds: 300,
  credit_retry_delay_minutes: 10,
  epsilon_greedy: 0.1,
});

function applyRuntime(r: RuntimeScheduling) {
  runtime.value = r;
  const e = r.effective;
  form.freshness_half_life_seconds = e.freshness_half_life_seconds;
  form.unknown_credit_baseline = e.unknown_credit_baseline;
  form.credit_workers = e.credit_workers;
  form.http_connection_pool_enabled = Boolean(r.http_connection_pool_enabled);
  form.credit_batch_size = e.credit_batch_size ?? r.file.credit_batch_size ?? 10;
  form.credit_batch_delay_seconds =
    e.credit_batch_delay_seconds ?? r.file.credit_batch_delay_seconds ?? 5;
  form.credit_refresh_check_interval_seconds =
    e.credit_refresh_check_interval_seconds ?? r.file.credit_refresh_check_interval_seconds ?? 300;
  form.credit_retry_delay_minutes =
    e.credit_retry_delay_minutes ?? r.file.credit_retry_delay_minutes ?? 10;
  form.epsilon_greedy = e.epsilon_greedy ?? r.file.epsilon_greedy ?? 0.1;
}

async function load() {
  if (!adminToken.value) return;
  loading.value = true;
  try {
    applyRuntime(await fetchRuntimeScheduling());
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err));
  } finally {
    loading.value = false;
  }
}

async function onSave() {
  saving.value = true;
  try {
    const r = await updateRuntimeScheduling({
      freshness_half_life_seconds: form.freshness_half_life_seconds,
      unknown_credit_baseline: form.unknown_credit_baseline,
      credit_workers: form.credit_workers,
      http_connection_pool_enabled: form.http_connection_pool_enabled,
      credit_batch_size: form.credit_batch_size,
      credit_batch_delay_seconds: form.credit_batch_delay_seconds,
      credit_refresh_check_interval_seconds: form.credit_refresh_check_interval_seconds,
      credit_retry_delay_minutes: form.credit_retry_delay_minutes,
      epsilon_greedy: form.epsilon_greedy,
    });
    applyRuntime(r);
    message.success("参数已热更新并持久化（重启后仍生效）");
  } catch (err: unknown) {
    message.error(getFcamErrorMessage(err));
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  if (adminToken.value) await verifyAdminToken();
  await load();
});

watch(adminToken, async (token) => {
  if (!token) {
    runtime.value = null;
    return;
  }
  await load();
});
</script>

<template>
  <n-space vertical size="large">
    <n-alert v-if="!adminToken" type="warning" title="未连接管理令牌">
      右上角点击「连接」后再修改运行参数。
    </n-alert>
    <n-alert v-else-if="connectionStatus === 'unauthorized'" type="error" title="管理令牌未授权" />

    <div class="page-head">
      <div>
        <h2>参数设置</h2>
        <p class="sub">热更新 · 写入 runtime_settings.json · 重启不丢失</p>
      </div>
      <n-space>
        <n-button :loading="loading" @click="load">重新加载</n-button>
        <n-button type="primary" :loading="saving" :disabled="!adminToken" @click="onSave">保存并应用</n-button>
      </n-space>
    </div>

    <n-spin :show="loading">
      <n-card title="调度与连接" size="small">
        <template #header-extra>
          <n-tag size="small" :type="form.http_connection_pool_enabled ? 'success' : 'default'">
            连接池：{{ form.http_connection_pool_enabled ? "开" : "关" }}
          </n-tag>
        </template>
        <n-form label-placement="left" label-width="220">
          <n-form-item label="新鲜度半衰期（秒）">
            <n-input-number v-model:value="form.freshness_half_life_seconds" :min="60" :max="604800" />
          </n-form-item>
          <n-form-item label="未知额度基线（额度）">
            <n-space align="center">
              <n-input-number v-model:value="form.unknown_credit_baseline" :min="0" :max="1000000" />
              <span class="hint">按「假定剩余额度」理解；权重 = log1p(该值)，如 50 → log1p(50)</span>
            </n-space>
          </n-form-item>
          <n-form-item label="HTTP 连接池">
            <n-space align="center">
              <n-switch v-model:value="form.http_connection_pool_enabled" />
              <span class="muted">热开关；关闭会立刻释放已复用连接</span>
            </n-space>
          </n-form-item>
          <n-form-item label="ε-greedy / 轮转探索率">
            <n-space align="center">
              <n-input-number
                v-model:value="form.epsilon_greedy"
                :min="0"
                :max="1"
                :step="0.01"
                :precision="3"
              />
              <span class="hint">默认 0.1；约 10% 请求在合格 key 间轮转探索，其余走高分。0 = 关闭。热更新并持久化。</span>
            </n-space>
          </n-form-item>
        </n-form>
      </n-card>

      <n-card style="margin-top: 12px" title="额度刷新（错峰 / 并发）" size="small">
        <n-form label-placement="left" label-width="220">
          <n-form-item label="刷新并发 workers">
            <n-input-number v-model:value="form.credit_workers" :min="1" :max="64" />
            <span class="hint">HTTP 并发探测数；写库仍串行</span>
          </n-form-item>
          <n-form-item label="每批数量 batch_size">
            <n-input-number v-model:value="form.credit_batch_size" :min="1" :max="500" />
          </n-form-item>
          <n-form-item label="批间间隔（秒）">
            <n-input-number v-model:value="form.credit_batch_delay_seconds" :min="0" :max="3600" />
            <span class="hint">默认 5；批与批之间的错峰 sleep</span>
          </n-form-item>
          <n-form-item label="整轮扫描间隔（秒）">
            <n-input-number v-model:value="form.credit_refresh_check_interval_seconds" :min="1" :max="86400" />
            <span class="hint">默认 300；一轮结束后再等多久扫下一轮</span>
          </n-form-item>
          <n-form-item label="失败退避（分钟）">
            <n-input-number v-model:value="form.credit_retry_delay_minutes" :min="1" :max="1440" />
            <span class="hint">探测失败后推迟该 key 的下次刷新</span>
          </n-form-item>
        </n-form>
      </n-card>

      <n-card v-if="runtime" style="margin-top: 12px" title="持久化" size="small">
        <div class="mono muted" style="font-size: 13px">
          路径：{{ runtime.persist_path || "（未配置，仅内存）" }}
        </div>
        <div class="muted" style="margin-top: 8px; font-size: 12px">
          保存后写入该 JSON；进程启动时自动加载。配置文件/环境变量仍是「未覆盖字段」的默认值。
        </div>
      </n-card>
    </n-spin>
  </n-space>
</template>

<style scoped>
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}
.page-head h2 {
  margin: 0;
  font-size: 22px;
}
.sub {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
}
.muted {
  color: #64748b;
  font-size: 12px;
}
.hint {
  margin-left: 12px;
  color: #94a3b8;
  font-size: 12px;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
</style>
