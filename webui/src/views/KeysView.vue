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
import { computed, h, onMounted, reactive, ref, watch } from "vue";

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
import { displayTimezone } from "@/state/timezone";
import { formatRelativeTime, formatTimestamp } from "@/utils/time";

const message = useMessage();
const dialog = useDialog();

const loading = ref(false);
const keys = ref<KeyItem[]>([]);
const page = ref(1);
const pageSize = ref(20);
const total = ref(0);
const sortBy = ref<string>("id");
const sortOrder = ref<"asc" | "desc">("desc");
const q = ref("");
const provider = ref<string | null>(null);
const statusFilter = ref<string | null>(null);
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

const statusFilterOptions = [
  { label: "可用", value: "active" },
  { label: "冷却中", value: "cooling" },
  { label: "失败", value: "failed" },
  { label: "已失效", value: "invalid" },
  { label: "已禁用", value: "disabled" },
  { label: "解密失败", value: "decrypt_failed" },
];

const statusType = (s: string) => {
  const v = (s || "").toLowerCase();
  if (v === "active") return "success" as const;
  if (v === "cooling" || v === "failed") return "warning" as const;
  if (v === "invalid" || v === "disabled" || v === "decrypt_failed") return "error" as const;
  return "default" as const;
};

const statusLabel = (s: string) => {
  const v = (s || "").toLowerCase();
  const map: Record<string, string> = {
    active: "可用",
    cooling: "冷却中",
    failed: "失败",
    invalid: "已失效",
    disabled: "已禁用",
    decrypt_failed: "解密失败",
  };
  return map[v] || s || "-";
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
      status: statusFilter.value || undefined,
      sortBy: sortBy.value,
      sortOrder: sortOrder.value,
    });
    keys.value = res.items;
    total.value = res.pagination?.total_items ?? res.items.length;
    // Keep local page in sync with server when out of range
    if (res.pagination?.page && res.pagination.page !== page.value) {
      page.value = res.pagination.page;
    }
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  } finally {
    loading.value = false;
  }
}

/** Server-side pagination. Note: `remote` must be on NDataTable, NOT inside pagination. */
const pagination = computed(() => ({
  page: page.value,
  pageSize: pageSize.value,
  itemCount: total.value,
  pageCount: Math.max(1, Math.ceil((total.value || 0) / (pageSize.value || 1))),
  pageSizes: [20, 50, 100],
  showSizePicker: true,
  prefix: ({ itemCount }: { itemCount: number | undefined }) =>
    `共 ${itemCount ?? total.value} 条 · 第 ${page.value} 页`,
  onUpdatePage: (p: number) => {
    page.value = p;
    void load();
  },
  onUpdatePageSize: (ps: number) => {
    pageSize.value = ps;
    page.value = 1;
    void load();
  },
}));

onMounted(async () => {
  if (adminToken.value) await verifyAdminToken();
  await load();
});

watch(adminToken, async (token) => {
  if (!token) {
    keys.value = [];
    total.value = 0;
    return;
  }
  page.value = 1;
  await load();
});

function onSearch() {
  page.value = 1;
  void load();
}

function onSort(sorter: { columnKey?: string | number; order?: "ascend" | "descend" | false } | null) {
  if (!sorter || !sorter.order || !sorter.columnKey) {
    sortBy.value = "id";
    sortOrder.value = "desc";
  } else {
    sortBy.value = String(sorter.columnKey);
    sortOrder.value = sorter.order === "ascend" ? "asc" : "desc";
  }
  page.value = 1;
  void load();
}

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
    message.success(`已禁用 ${checked.value.length} 个密钥`);
    checked.value = [];
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

function onBatchDelete() {
  if (!checked.value.length) return;
  const n = checked.value.length;
  dialog.warning({
    title: "批量删除密钥",
    content: `将彻底删除选中的 ${n} 个上游密钥（不可恢复，等同 purge）。确认？`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        const res = await batchKeys({ ids: checked.value, purge: true });
        message.success(`已删除 ${res.succeeded} 个 / 失败 ${res.failed}`);
        checked.value = [];
        await load();
      } catch (err) {
        message.error(getFcamErrorMessage(err));
      }
    },
  });
}

const columns = computed(() => {
  // depend on displayTimezone so absolute timestamps re-render when TZ changes
  void displayTimezone.value;
  const so = (key: string): "ascend" | "descend" | false => {
    if (sortBy.value !== key) return false;
    return sortOrder.value === "asc" ? "ascend" : "descend";
  };
  return [
  { type: "selection" as const },
  { title: "ID", key: "id", width: 70, sorter: "default" as const, sortOrder: so("id") },
  {
    title: "名称 / 掩码",
    key: "name",
    sorter: "default" as const,
    sortOrder: so("name"),
    render: (row: KeyItem) =>
      h("div", [
        h("div", { style: "font-weight:600" }, row.name || "—"),
        h("div", { style: "color:#64748b;font-size:12px" }, row.api_key_masked),
      ]),
  },
  {
    title: "上游服务",
    key: "provider",
    width: 110,
    sorter: "default" as const,
    sortOrder: so("provider"),
    render: (row: KeyItem) => h(NTag, { size: "small", bordered: false }, { default: () => row.provider }),
  },
  {
    title: "状态",
    key: "status",
    width: 110,
    sorter: "default" as const,
    sortOrder: so("status"),
    render: (row: KeyItem) =>
      h(NTag, { type: statusType(row.status), size: "small" }, { default: () => statusLabel(row.status) }),
  },
  {
    title: "调度分",
    key: "selection_score",
    width: 90,
    sorter: "default" as const,
    sortOrder: so("selection_score"),
    render: (r: KeyItem) => (r.selection_score == null ? "-" : Number(r.selection_score).toFixed(2)),
  },
  {
    title: "剩余额度",
    key: "cached_remaining_credits",
    width: 120,
    sorter: "default" as const,
    sortOrder: so("cached_remaining_credits"),
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
    sorter: "default" as const,
    sortOrder: so("last_credit_check_at"),
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
    sorter: "default" as const,
    sortOrder: so("total_requests"),
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
  ];
});
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2>上游密钥池</h2>
        <p class="sub">统一全局池 · 科学调度（额度充足 + 刷新新鲜优先）· 额度为 0 / 失效自动排除</p>
      </div>
      <NSpace>
        <NButton @click="onRefreshCredits">刷新额度</NButton>
        <NButton @click="showImport = true">批量导入</NButton>
        <NButton type="primary" @click="showCreate = true">添加密钥</NButton>
      </NSpace>
    </div>

    <NAlert type="info" :bordered="false" style="margin-bottom: 16px">
      所有下游客户端共享同一上游密钥池。不再按客户端分池，也不再设置密钥日配额。
      失效密钥自动禁用且停止额度刷新；客户端遇 429 / 额度问题 / 密钥失效时自动换密钥重试。
    </NAlert>

    <NCard :bordered="false" class="panel">
      <NSpace style="margin-bottom: 12px" align="center">
        <NInput v-model:value="q" clearable placeholder="搜索名称 / 末四位" style="width: 220px" @keyup.enter="onSearch" />
        <NSelect v-model:value="provider" clearable :options="providerOptions" placeholder="上游服务" style="width: 140px" />
        <NSelect v-model:value="statusFilter" clearable :options="statusFilterOptions" placeholder="状态" style="width: 130px" />
        <NButton @click="onSearch">查询</NButton>
        <NButton :disabled="!checked.length" @click="onBatchDisable">批量禁用</NButton>
        <NButton :disabled="!checked.length" type="error" secondary @click="onBatchDelete">批量删除</NButton>
      </NSpace>

      <NDataTable
        v-model:checked-row-keys="checked"
        :columns="columns as any"
        :data="keys"
        :loading="loading"
        :remote="true"
        :row-key="(r: KeyItem) => r.id"
        :pagination="pagination as any"
        @update:sorter="onSort"
      />
    </NCard>

    <NModal v-model:show="showCreate" preset="card" title="添加上游密钥" style="width: 480px">
      <NForm label-placement="left" label-width="110">
        <NFormItem label="上游密钥" required>
          <NInput v-model:value="createForm.api_key" type="password" show-password-on="click" placeholder="fc-..." />
        </NFormItem>
        <NFormItem label="名称">
          <NInput v-model:value="createForm.name" placeholder="可选备注" />
        </NFormItem>
        <NFormItem label="上游服务">
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

    <NModal v-model:show="showImport" preset="card" title="批量导入密钥" style="width: 560px">
      <NForm label-placement="top">
        <NFormItem label="上游服务">
          <NSelect v-model:value="importProvider" :options="providerOptions" />
        </NFormItem>
        <NFormItem label="每行一个密钥（支持 用户|密码|密钥|验证时间）">
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
