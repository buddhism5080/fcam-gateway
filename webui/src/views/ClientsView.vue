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
  NSpace,
  NTag,
  useDialog,
  useMessage,
} from "naive-ui";
import { computed, h, onMounted, reactive, ref } from "vue";

import {
  batchUpdateClients,
  createClient,
  fetchClients,
  rotateClientToken,
  updateClient,
  type ClientItem,
} from "@/api/clients";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken, verifyAdminToken } from "@/state/adminAuth";
import { formatRelativeTime } from "@/utils/time";

const message = useMessage();
const dialog = useDialog();

const loading = ref(false);
const clients = ref<ClientItem[]>([]);
const search = ref("");
const checked = ref<number[]>([]);

const showCreate = ref(false);
const tokenModal = ref<{ name: string; token: string } | null>(null);
const createForm = reactive({
  name: "",
  rate_limit_per_min: 60,
  max_concurrent: 10,
  max_retries: 3,
});

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase();
  if (!q) return clients.value;
  return clients.value.filter((c) => `${c.id} ${c.name}`.toLowerCase().includes(q));
});

async function load() {
  if (!adminToken.value) return;
  loading.value = true;
  try {
    clients.value = await fetchClients();
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

async function onCreate() {
  try {
    const res = await createClient({
      name: createForm.name.trim(),
      rate_limit_per_min: createForm.rate_limit_per_min,
      max_concurrent: createForm.max_concurrent,
      max_retries: createForm.max_retries,
    });
    tokenModal.value = { name: res.client.name, token: res.token };
    showCreate.value = false;
    createForm.name = "";
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function onRotate(row: ClientItem) {
  dialog.warning({
    title: "轮换 Client Token",
    content: `轮换后旧 token 立即失效。确认轮换「${row.name}」？`,
    positiveText: "轮换",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        const res = await rotateClientToken(row.id);
        tokenModal.value = { name: row.name, token: res.token };
        await load();
      } catch (err) {
        message.error(getFcamErrorMessage(err));
      }
    },
  });
}

async function onToggle(row: ClientItem) {
  try {
    await updateClient(row.id, { is_active: !row.is_active });
    message.success(row.is_active ? "已禁用" : "已启用");
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function saveLimits(row: ClientItem, patch: Partial<ClientItem>) {
  try {
    await updateClient(row.id, {
      rate_limit_per_min: patch.rate_limit_per_min ?? row.rate_limit_per_min,
      max_concurrent: patch.max_concurrent ?? row.max_concurrent,
      max_retries: patch.max_retries ?? row.max_retries,
    });
    message.success("已更新");
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function onBatch(action: "enable" | "disable" | "delete") {
  if (!checked.value.length) return;
  try {
    const res = await batchUpdateClients({ client_ids: checked.value, action });
    message.success(`成功 ${res.success_count} / 失败 ${res.failed_count}`);
    checked.value = [];
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function copyToken() {
  if (!tokenModal.value) return;
  try {
    await navigator.clipboard.writeText(tokenModal.value.token);
    message.success("已复制");
  } catch {
    message.error("复制失败，请手动选择");
  }
}

const columns = computed(() => [
  { type: "selection" as const },
  { title: "ID", key: "id", width: 70 },
  {
    title: "名称",
    key: "name",
    render: (row: ClientItem) =>
      h("div", [
        h("div", { style: "font-weight:600" }, row.name),
        h("div", { style: "font-size:12px;color:#94a3b8" }, `最近使用 ${formatRelativeTime(row.last_used_at) || "—"}`),
      ]),
  },
  {
    title: "状态",
    key: "status",
    width: 100,
    render: (row: ClientItem) =>
      h(
        NTag,
        { type: row.is_active ? "success" : "error", size: "small" },
        { default: () => (row.is_active ? "active" : "disabled") }
      ),
  },
  {
    title: "RPM",
    key: "rate_limit_per_min",
    width: 120,
    render: (row: ClientItem) =>
      h(NInputNumber, {
        value: row.rate_limit_per_min,
        min: 0,
        size: "small",
        style: "width:100px",
        onUpdateValue: (v: number | null) => {
          if (v != null) saveLimits(row, { rate_limit_per_min: v });
        },
      }),
  },
  {
    title: "并发",
    key: "max_concurrent",
    width: 120,
    render: (row: ClientItem) =>
      h(NInputNumber, {
        value: row.max_concurrent,
        min: 0,
        size: "small",
        style: "width:100px",
        onUpdateValue: (v: number | null) => {
          if (v != null) saveLimits(row, { max_concurrent: v });
        },
      }),
  },
  {
    title: "换 Key 重试",
    key: "max_retries",
    width: 130,
    render: (row: ClientItem) =>
      h(NInputNumber, {
        value: row.max_retries ?? 3,
        min: 0,
        max: 50,
        size: "small",
        style: "width:100px",
        onUpdateValue: (v: number | null) => {
          if (v != null) saveLimits(row, { max_retries: v });
        },
      }),
  },
  {
    title: "操作",
    key: "actions",
    width: 200,
    render: (row: ClientItem) =>
      h(
        NSpace,
        { size: 4 },
        {
          default: () => [
            h(NButton, { size: "tiny", onClick: () => onRotate(row) }, { default: () => "轮换 Token" }),
            h(
              NButton,
              { size: "tiny", secondary: true, onClick: () => onToggle(row) },
              { default: () => (row.is_active ? "禁用" : "启用") }
            ),
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
        <h2>下游 Clients</h2>
        <p class="sub">下游密钥：可配置 RPM、并发、换 Key 重试次数。请求走统一上游池。</p>
      </div>
      <NButton type="primary" @click="showCreate = true">创建 Client</NButton>
    </div>

    <NAlert type="info" :bordered="false" style="margin-bottom: 16px">
      每个 Client 独立设置 <b>RPM</b> 与 <b>并发</b>。当上游 Key 出现额度问题 / 429 / 失效时，
      按该 Client 的 <b>max_retries</b> 自动切换上游 Key，而不是直接把错误返回给调用方。
    </NAlert>

    <NCard :bordered="false" class="panel">
      <NSpace style="margin-bottom: 12px">
        <NInput v-model:value="search" clearable placeholder="搜索 Client" style="width: 220px" />
        <NButton :disabled="!checked.length" @click="onBatch('enable')">批量启用</NButton>
        <NButton :disabled="!checked.length" @click="onBatch('disable')">批量禁用</NButton>
        <NButton :disabled="!checked.length" type="error" secondary @click="onBatch('delete')">批量删除</NButton>
      </NSpace>

      <NDataTable
        v-model:checked-row-keys="checked"
        :columns="columns as any"
        :data="filtered"
        :loading="loading"
        :row-key="(r: ClientItem) => r.id"
      />
    </NCard>

    <NModal v-model:show="showCreate" preset="card" title="创建下游 Client" style="width: 480px">
      <NForm label-placement="left" label-width="120">
        <NFormItem label="名称" required>
          <NInput v-model:value="createForm.name" placeholder="例如 my-bot" />
        </NFormItem>
        <NFormItem label="RPM">
          <NInputNumber v-model:value="createForm.rate_limit_per_min" :min="0" />
        </NFormItem>
        <NFormItem label="并发">
          <NInputNumber v-model:value="createForm.max_concurrent" :min="0" />
        </NFormItem>
        <NFormItem label="换 Key 重试">
          <NInputNumber v-model:value="createForm.max_retries" :min="0" :max="50" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showCreate = false">取消</NButton>
          <NButton type="primary" :disabled="!createForm.name.trim()" @click="onCreate">创建</NButton>
        </NSpace>
      </template>
    </NModal>

    <NModal :show="!!tokenModal" preset="card" title="Client Token（仅显示一次）" style="width: 520px" @update:show="(v:boolean) => !v && (tokenModal = null)">
      <NAlert type="warning" :bordered="false" style="margin-bottom: 12px">
        请立即复制保存。关闭后无法再次查看明文 token，只能轮换。
      </NAlert>
      <div v-if="tokenModal">
        <div style="margin-bottom: 8px; color: #64748b">Client：{{ tokenModal.name }}</div>
        <NInput :value="tokenModal.token" type="textarea" :rows="3" readonly />
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton type="primary" @click="copyToken">复制</NButton>
          <NButton @click="tokenModal = null">关闭</NButton>
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
