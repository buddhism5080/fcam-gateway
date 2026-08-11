<script setup lang="ts">
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NTag,
  useDialog,
  useMessage,
} from "naive-ui";
import { computed, h, onMounted, reactive, ref } from "vue";

import { getFcamErrorMessage } from "@/api/http";
import {
  batchKeys,
  createKey,
  fetchKeys,
  importKeysText,
  purgeKey,
  reviveKey,
  testKey,
  updateKey,
  type KeyItem,
} from "@/api/keys";
import { refreshAllCredits } from "@/api/credits";
import { adminToken, verifyAdminToken } from "@/state/adminAuth";
import { formatRelativeTime, formatTimestamp } from "@/utils/time";

const message = useMessage();
const dialog = useDialog();

const loading = ref(false);
const keys = ref<KeyItem[]>([]);
const page = ref(1);
const pageSize = ref(20);
const total = ref(0);
const q = ref("");
const provider = ref<string | null>(null);
const checked = ref<number[]>([]);

const showCreate = ref(false);
const showImport = ref(false);
const createForm = reactive({
  api_key: "",
  name: "",
  provider: "firecrawl",
  max_concurrent: 5,
  rate_limit_per_min: 60,
});
const importText = ref("");
const importProvider = ref("firecrawl");

const providerOptions = [
  { label: "Firecrawl", value: "firecrawl" },
  { label: "Exa", value: "exa" },
];

const statusType = (s: string) => {
  const v = (s || "").toLowerCase();
  if (v === "active") return "success" as const;
  if (v === "cooling" || v === "failed") return "warning" as const;
  if (v === "invalid" || v === "disabled" || v === "decrypt_failed") return "error" as const;
  return "default" as const;
};

async function load() {
  if (!adminToken.value) return;
  loading.value = true;
  try {
    const res = await fetchKeys({
      page: page.value,
      pageSize: pageSize.value,
      q: q.value.trim() || undefined,
      provider: provider.value || undefined,
    });
    keys.value = res.items;
    total.value = res.pagination?.total_items ?? res.items.length;
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  if (adminToken.value) await verifyAdminToken();
  await load();
});

async function onRevive(row: KeyItem) {
  try {
    const res = await reviveKey(row.id, { test: true, requeue_refresh: true });
    const ok = res.test && typeof res.test === "object" && "ok" in (res.test as object) ? (res.test as { ok: boolean }).ok : true;
    message.success(ok ? `已复活 Key #${row.id}` : `已复活但测活失败 Key #${row.id}`);
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function onCreate() {

  try {
    await createKey({
      api_key: createForm.api_key.trim(),
      name: createForm.name.trim() || null,
      provider: createForm.provider,
      max_concurrent: createForm.max_concurrent,
      rate_limit_per_min: createForm.rate_limit_per_min,
    });
    message.success("已加入全局 Key 池");
    showCreate.value = false;
    createForm.api_key = "";
    createForm.name = "";
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function onImport() {
  try {
    const res = await importKeysText({
      text: importText.value,
      provider: importProvider.value,
    });
    message.success(`导入完成：新建 ${res.created} / 更新 ${res.updated} / 跳过 ${res.skipped} / 失败 ${res.failed}`);
    showImport.value = false;
    importText.value = "";
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function onTest(row: KeyItem) {
  try {
    const res = await testKey(row.id);
    message[res.ok ? "success" : "warning"](
      res.ok ? `Key #${row.id} 可用 (${res.latency_ms}ms)` : `Key #${row.id} 失败 status=${res.upstream_status_code}`
    );
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function onToggle(row: KeyItem) {
  try {
    await updateKey(row.id, { is_active: !row.is_active });
    message.success(row.is_active ? "已禁用" : "已启用");
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

function onPurge(row: KeyItem) {
  dialog.warning({
    title: "彻底删除 Key",
    content: `确认 purge Key #${row.id}（${row.api_key_masked}）？不可恢复。`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        await purgeKey(row.id);
        message.success("已删除");
        await load();
      } catch (err) {
        message.error(getFcamErrorMessage(err));
      }
    },
  });
}

async function onRefreshCredits() {
  try {
    await refreshAllCredits({ force: true });
    message.success("已触发额度刷新（并发 workers）");
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function onBatchDisable() {
  if (!checked.value.length) return;
  try {
    await batchKeys({ ids: checked.value, patch: { is_active: false } });
    message.success(`已禁用 ${checked.value.length} 个 Key`);
    checked.value = [];
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

const columns = computed(() => [
  { type: "selection" as const },
  { title: "ID", key: "id", width: 70 },
  {
    title: "名称 / 掩码",
    key: "name",
    render: (row: KeyItem) =>
      h("div", [
        h("div", { style: "font-weight:600" }, row.name || "—"),
        h("div", { style: "color:#64748b;font-size:12px" }, row.api_key_masked),
      ]),
  },
  {
    title: "Provider",
    key: "provider",
    width: 100,
    render: (row: KeyItem) => h(NTag, { size: "small", bordered: false }, { default: () => row.provider }),
  },
  {
    title: "状态",
    key: "status",
    width: 110,
    render: (row: KeyItem) =>
      h(NTag, { type: statusType(row.status), size: "small" }, { default: () => row.status }),
  },
  {
    title: "调度分",
    key: "selection_score",
    width: 90,
    render: (r: KeyItem) => (r.selection_score == null ? "-" : Number(r.selection_score).toFixed(2)),
  },
  {
    title: "剩余额度",
    key: "cached_remaining_credits",
    width: 120,
    render: (row: KeyItem) => {
      const r = row.cached_remaining_credits;
      const p = row.cached_plan_credits;
      if (r == null) return h("span", { style: "color:#94a3b8" }, "未知");
      return h("span", { style: r <= 0 ? "color:#ef4444;font-weight:600" : "" }, p != null ? `${r} / ${p}` : String(r));
    },
  },
  {
    title: "额度刷新",
    key: "last_credit_check_at",
    width: 140,
    render: (row: KeyItem) =>
      h("div", { style: "font-size:12px;color:#64748b" }, [
        h("div", formatRelativeTime(row.last_credit_check_at) || "从未"),
        h("div", row.next_refresh_at ? `下次 ${formatTimestamp(row.next_refresh_at)}` : "不再刷新"),
      ]),
  },
  {
    title: "请求数",
    key: "total_requests",
    width: 90,
  },
  {
    title: "操作",
    key: "actions",
    width: 220,
    render: (row: KeyItem) =>
      h(
        NSpace,
        { size: 4 },
        {
          default: () => [
                      h(NButton, { size: "tiny", onClick: () => onTest(row) }, { default: () => "测试" }),
                      h(
                        NButton,
                        {
                          size: "tiny",
                          type: "warning",
                          secondary: true,
                          onClick: () => onRevive(row),
                        },
                        { default: () => "复活" },
                      ),
                      h(
                        NButton,
                        { size: "tiny", secondary: true, onClick: () => onToggle(row) },
                        { default: () => (row.is_active ? "禁用" : "启用") },
                      ),
                      h(NButton, { size: "tiny", type: "error", quaternary: true, onClick: () => onPurge(row) }, { default: () => "删除" }),
                    ],
        }
      ),
  },
]);
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2>上游 Key 池</h2>
        <p class="sub">统一全局池 · 科学调度（额度充足 + 刷新新鲜优先）· 额度为 0 / 失效自动排除</p>
      </div>
      <NSpace>
        <NButton @click="onRefreshCredits">刷新额度</NButton>
        <NButton @click="showImport = true">批量导入</NButton>
        <NButton type="primary" @click="showCreate = true">添加 Key</NButton>
      </NSpace>
    </div>

    <NAlert type="info" :bordered="false" style="margin-bottom: 16px">
      所有下游 Client 共享同一上游 Key 池。不再按 Client 分池，也不再设置 Key daily quota。
      失效 Key 自动禁用且停止额度刷新；客户端遇 429 / 额度问题 / Key 失效时自动换 Key 重试。
    </NAlert>

    <NCard :bordered="false" class="panel">
      <NSpace style="margin-bottom: 12px" align="center">
        <NInput v-model:value="q" clearable placeholder="搜索名称 / last4" style="width: 220px" @keyup.enter="load" />
        <NSelect v-model:value="provider" clearable :options="providerOptions" placeholder="Provider" style="width: 140px" />
        <NButton @click="load">查询</NButton>
        <NButton :disabled="!checked.length" @click="onBatchDisable">批量禁用</NButton>
      </NSpace>

      <NDataTable
        v-model:checked-row-keys="checked"
        :columns="columns as any"
        :data="keys"
        :loading="loading"
        :row-key="(r: KeyItem) => r.id"
        :pagination="{
          page,
          pageSize,
          itemCount: total,
          onUpdatePage: (p: number) => { page = p; load(); },
          onUpdatePageSize: (ps: number) => { pageSize = ps; page = 1; load(); },
        }"
      />
    </NCard>

    <NModal v-model:show="showCreate" preset="card" title="添加上游 Key" style="width: 480px">
      <NForm label-placement="left" label-width="110">
        <NFormItem label="API Key" required>
          <NInput v-model:value="createForm.api_key" type="password" show-password-on="click" placeholder="fc-..." />
        </NFormItem>
        <NFormItem label="名称">
          <NInput v-model:value="createForm.name" placeholder="可选备注" />
        </NFormItem>
        <NFormItem label="Provider">
          <NSelect v-model:value="createForm.provider" :options="providerOptions" />
        </NFormItem>
        <NFormItem label="软并发上限">
          <NInputNumber v-model:value="createForm.max_concurrent" :min="0" />
        </NFormItem>
        <NFormItem label="软 RPM">
          <NInputNumber v-model:value="createForm.rate_limit_per_min" :min="0" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showCreate = false">取消</NButton>
          <NButton type="primary" :disabled="createForm.api_key.trim().length < 8" @click="onCreate">添加</NButton>
        </NSpace>
      </template>
    </NModal>

    <NModal v-model:show="showImport" preset="card" title="批量导入 Key" style="width: 560px">
      <NForm label-placement="top">
        <NFormItem label="Provider">
          <NSelect v-model:value="importProvider" :options="providerOptions" />
        </NFormItem>
        <NFormItem label="每行一个 Key（支持 user|pass|api_key|verified_at）">
          <NInput v-model:value="importText" type="textarea" :rows="10" placeholder="fc-xxx&#10;fc-yyy" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showImport = false">取消</NButton>
          <NButton type="primary" :disabled="!importText.trim()" @click="onImport">导入</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.page {
  padding: 8px 4px 24px;
}
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}
.page-head h2 {
  margin: 0;
  font-size: 22px;
  color: #0f172a;
}
.sub {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
}
.panel {
  background: rgba(255, 255, 255, 0.92);
  border-radius: 16px;
}
</style>
