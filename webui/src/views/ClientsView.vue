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
  revealClientToken,
  rotateClientToken,
  updateClient,
  type ClientItem,
} from "@/api/clients";
import { getFcamErrorMessage } from "@/api/http";
import { adminToken, verifyAdminToken } from "@/state/adminAuth";
import { copyFromInputElement, copyTextToClipboard, selectInputElement } from "@/utils/clipboard";
import { formatRelativeTime } from "@/utils/time";
import { nextTick } from "vue";

const message = useMessage();
const dialog = useDialog();

const loading = ref(false);
const clients = ref<ClientItem[]>([]);
const search = ref("");
const checked = ref<number[]>([]);

const showCreate = ref(false);
const tokenModal = ref<{ name: string; token: string; mode: "create" | "rotate" | "reveal" } | null>(null);
/** Native textarea so we can select + copy reliably (NInput wraps and breaks selection). */
const tokenTextareaRef = ref<HTMLTextAreaElement | null>(null);
const createForm = reactive({
  name: "",
  rate_limit_per_min: 60,
  max_concurrent: 10,
  max_retries: 3,
  token: "",
});

async function focusSelectTokenField() {
  await nextTick();
  selectInputElement(tokenTextareaRef.value);
}

async function openTokenModal(payload: { name: string; token: string; mode: "create" | "rotate" | "reveal" }) {
  tokenModal.value = payload;
  await focusSelectTokenField();
}

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase();
  if (!q) return clients.value;
  return clients.value.filter((c) => `${c.id} ${c.name}`.toLowerCase().includes(q));
});

function generateClientTokenLocal(): string {
  // Match server style: fcam_client_ + url-safe random
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let s = "";
  for (const b of bytes) s += alphabet[b % alphabet.length];
  return `fcam_client_${s}`;
}

function validateTokenComplexity(token: string): string | null {
  const t = token.trim();
  if (t.length < 24) return "令牌长度至少 24 个字符";
  if (t.length > 256) return "令牌长度不能超过 256 个字符";
  let classes = 0;
  if (/[a-z]/.test(t)) classes++;
  if (/[A-Z]/.test(t)) classes++;
  if (/[0-9]/.test(t)) classes++;
  if (/[^a-zA-Z0-9]/.test(t)) classes++;
  if (classes < 3) return "令牌需至少包含三类字符（大写/小写/数字/特殊符号）";
  return null;
}

function onRandomToken() {
  createForm.token = generateClientTokenLocal();
  message.success("已生成随机令牌（也可手动修改）");
}

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
  const name = createForm.name.trim();
  if (!name) {
    message.warning("请填写名称");
    return;
  }
  const manual = createForm.token.trim();
  if (manual) {
    const err = validateTokenComplexity(manual);
    if (err) {
      message.error(err);
      return;
    }
  }
  try {
    const res = await createClient({
      name,
      rate_limit_per_min: createForm.rate_limit_per_min,
      max_concurrent: createForm.max_concurrent,
      max_retries: createForm.max_retries,
      token: manual || null,
    });
    await openTokenModal({ name: res.client.name, token: res.token, mode: "create" });
    showCreate.value = false;
    createForm.name = "";
    createForm.token = "";
    await load();
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
}

async function onRotate(row: ClientItem) {
  dialog.warning({
    title: "轮换客户端令牌",
    content: `轮换后旧令牌立即失效。确认轮换「${row.name}」？`,
    positiveText: "轮换",
    negativeText: "取消",
    onPositiveClick: async () => {
      try {
        const res = await rotateClientToken(row.id);
        await openTokenModal({ name: row.name, token: res.token, mode: "rotate" });
        await load();
      } catch (err) {
        message.error(getFcamErrorMessage(err));
      }
    },
  });
}

async function onReveal(row: ClientItem) {
  try {
    const res = await revealClientToken(row.id);
    await openTokenModal({ name: res.name || row.name, token: res.token, mode: "reveal" });
  } catch (err) {
    message.error(getFcamErrorMessage(err));
  }
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
  // Prefer the visible textarea — most reliable under plain HTTP / Discord-embedded browsers.
  let ok = await copyFromInputElement(tokenTextareaRef.value);
  if (!ok) {
    ok = await copyTextToClipboard(tokenModal.value.token);
  }
  // Always re-select so user can Ctrl+C / Cmd+C if OS clipboard still empty
  selectInputElement(tokenTextareaRef.value);
  if (ok) {
    message.success("已复制（若粘贴为空，文本已全选，请再按 Ctrl+C）");
  } else {
    message.warning("自动复制受限：令牌已全选，请按 Ctrl+C（Mac: ⌘C）");
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
        { default: () => (row.is_active ? "启用" : "禁用") }
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
    title: "换密钥重试",
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
    width: 280,
    render: (row: ClientItem) =>
      h(
        NSpace,
        { size: 4 },
        {
          default: () => [
            h(NButton, { size: "tiny", type: "primary", secondary: true, onClick: () => onReveal(row) }, { default: () => "显示令牌" }),
            h(NButton, { size: "tiny", onClick: () => onRotate(row) }, { default: () => "轮换令牌" }),
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
        <h2>下游客户端</h2>
        <p class="sub">下游令牌：可配置 RPM、并发、换密钥重试次数。请求走统一上游池。</p>
      </div>
      <NButton type="primary" @click="showCreate = true">创建客户端</NButton>
    </div>

    <NAlert type="info" :bordered="false" style="margin-bottom: 16px">
      每个客户端独立设置 <b>RPM</b> 与 <b>并发</b>。当上游密钥出现额度问题 / 429 / 失效时，
      按该客户端的 <b>换密钥重试次数</b> 自动切换上游密钥，而不是直接把错误返回给调用方。
      新创建/轮换的令牌会加密存库，可再次「显示令牌」并复制。
    </NAlert>

    <NCard :bordered="false" class="panel">
      <NSpace style="margin-bottom: 12px">
        <NInput v-model:value="search" clearable placeholder="搜索客户端" style="width: 220px" />
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

    <NModal v-model:show="showCreate" preset="card" title="创建下游客户端" style="width: 520px">
      <NForm label-placement="left" label-width="120">
        <NFormItem label="名称" required>
          <NInput v-model:value="createForm.name" placeholder="例如 my-bot" />
        </NFormItem>
        <NFormItem label="下游令牌">
          <NSpace vertical style="width: 100%">
            <NInput
              v-model:value="createForm.token"
              type="textarea"
              :rows="2"
              placeholder="留空则服务端随机生成；也可手动填入（≥24 字符，含大小写/数字/符号中至少三类）"
            />
            <NSpace>
              <NButton size="small" @click="onRandomToken">随机生成</NButton>
              <span class="hint">手动填写时需满足复杂度；随机生成的可直接用</span>
            </NSpace>
          </NSpace>
        </NFormItem>
        <NFormItem label="RPM">
          <NInputNumber v-model:value="createForm.rate_limit_per_min" :min="0" />
        </NFormItem>
        <NFormItem label="并发">
          <NInputNumber v-model:value="createForm.max_concurrent" :min="0" />
        </NFormItem>
        <NFormItem label="换密钥重试">
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

    <NModal
      :show="!!tokenModal"
      preset="card"
      :title="tokenModal?.mode === 'reveal' ? '客户端令牌' : '客户端令牌（请妥善保存）'"
      style="width: 520px"
      @update:show="(v: boolean) => !v && (tokenModal = null)"
    >
      <NAlert v-if="tokenModal?.mode !== 'reveal'" type="warning" :bordered="false" style="margin-bottom: 12px">
        请复制保存。新创建/轮换后的令牌已加密入库，之后仍可点「显示令牌」再次查看。
      </NAlert>
      <NAlert v-else type="info" :bordered="false" style="margin-bottom: 12px">
        以下为解密后的明文令牌，可直接复制使用。
      </NAlert>
      <div v-if="tokenModal">
        <div style="margin-bottom: 8px; color: #64748b">客户端：{{ tokenModal.name }}</div>
        <textarea
          ref="tokenTextareaRef"
          class="token-ta"
          :value="tokenModal.token"
          readonly
          rows="3"
          @focus="selectInputElement(($event.target as HTMLTextAreaElement))"
        />
        <div class="hint" style="margin-top: 8px">
          若按钮复制无效：点输入框会全选，再按 <b>Ctrl+C</b>（Mac: <b>⌘C</b>）。
        </div>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="focusSelectTokenField">全选</NButton>
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
.hint {
  font-size: 12px;
  color: #94a3b8;
}
.token-ta {
  width: 100%;
  box-sizing: border-box;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.45;
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  color: #0f172a;
  resize: vertical;
  outline: none;
}
.token-ta:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
}
</style>
