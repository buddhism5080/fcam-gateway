<script setup lang="ts">
import type { GlobalThemeOverrides } from "naive-ui";
import {
  NButton,
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  NLoadingBarProvider,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NTag,
  zhCN,
  dateZhCN,
} from "naive-ui";
import { computed, onMounted, ref } from "vue";

import ConnectModal from "@/components/ConnectModal.vue";
import TimezoneSelect from "@/components/TimezoneSelect.vue";
import { adminToken, connectionStatus, disconnectAdminToken, verifyAdminToken } from "@/state/adminAuth";

const showConnect = ref(false);

const navItems = [
  { to: "/dashboard", label: "仪表盘" },
  { to: "/keys", label: "上游密钥" },
  { to: "/clients", label: "下游客户端" },
  { to: "/logs", label: "请求日志" },
  { to: "/audit", label: "审计日志" },
  { to: "/settings", label: "参数设置" },
] as const;

const statusTag = computed(() => {
  if (!adminToken.value) return { type: "warning" as const, label: "未连接" };
  if (connectionStatus.value === "ok") return { type: "success" as const, label: "已连接" };
  if (connectionStatus.value === "unauthorized") return { type: "error" as const, label: "未授权" };
  if (connectionStatus.value === "error") return { type: "error" as const, label: "异常" };
  return { type: "default" as const, label: "待验证" };
});

// UI 配色/组件方案对齐 example/gpt-load（Naive UI themeOverrides）
const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: "#667eea",
    primaryColorHover: "#5a6fd8",
    primaryColorPressed: "#4c63d2",
    primaryColorSuppl: "#8b9df5",
    borderRadius: "12px",
    borderRadiusSmall: "8px",
    fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    // 注意：Naive UI 内部会解析颜色（seemly/rgba），此处不要使用 CSS 变量（var(--xxx)）
    bodyColor: "#f5f7fa",
    cardColor: "rgba(255, 255, 255, 0.95)",
    modalColor: "rgba(255, 255, 255, 0.95)",
    popoverColor: "rgba(255, 255, 255, 0.95)",
    tableColor: "rgba(255, 255, 255, 0.95)",
    inputColor: "#ffffff",
    borderColor: "rgba(0, 0, 0, 0.08)",
    dividerColor: "rgba(0, 0, 0, 0.08)",
    textColorBase: "#1e293b",
    textColor1: "#1e293b",
    textColor2: "#475569",
    textColor3: "#94a3b8",
  },
  Card: {
    paddingMedium: "24px",
    color: "rgba(255, 255, 255, 0.95)",
    textColor: "#1e293b",
    borderColor: "rgba(0, 0, 0, 0.08)",
    boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
  },
  Button: {
    fontWeight: "600",
    heightMedium: "40px",
    heightLarge: "48px",
  },
  Input: {
    heightMedium: "40px",
    heightLarge: "48px",
    color: "#ffffff",
    textColor: "#1e293b",
    placeholderColor: "#94a3b8",
    borderHover: "rgba(102, 126, 234, 0.35)",
    borderFocus: "rgba(102, 126, 234, 0.55)",
  },
  DataTable: {
    borderColor: "rgba(0, 0, 0, 0.08)",
    thColor: "rgba(102, 126, 234, 0.06)",
    thTextColor: "#475569",
    tdTextColor: "#1e293b",
    tdColorHover: "rgba(102, 126, 234, 0.08)",
    tdColorStriped: "rgba(102, 126, 234, 0.03)",
  },
  Modal: {
    color: "rgba(255, 255, 255, 0.95)",
  },
  Message: {
    color: "rgba(255, 255, 255, 0.95)",
    textColor: "#1e293b",
    borderRadius: "10px",
  },
  LoadingBar: {
    colorLoading: "#667eea",
    colorError: "#ff4757",
    height: "3px",
  },
};

onMounted(async () => {
  if (adminToken.value) await verifyAdminToken();
});
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-loading-bar-provider>
      <n-message-provider placement="top-right">
        <n-dialog-provider>
          <n-layout class="main-layout">
            <n-layout-header class="layout-header">
              <div class="header-content">
                <div class="header-brand">
                  <div class="brand-icon">F</div>
                  <h1 class="brand-title">FCAM</h1>
                </div>

                <nav class="header-nav">
                  <router-link v-for="i in navItems" :key="i.to" :to="i.to" class="nav-link">
                    {{ i.label }}
                  </router-link>
                </nav>

                <div class="header-actions">
                  <timezone-select />
                  <n-tag size="small" :type="statusTag.type">{{ statusTag.label }}</n-tag>
                  <n-button size="small" @click="showConnect = true">连接</n-button>
                  <n-button v-if="adminToken" size="small" @click="disconnectAdminToken">断开</n-button>
                </div>
              </div>
            </n-layout-header>

            <n-layout-content class="layout-content">
              <div class="content-wrapper">
                <router-view />
              </div>
            </n-layout-content>
          </n-layout>

          <connect-modal v-model:show="showConnect" />
        </n-dialog-provider>
      </n-message-provider>
    </n-loading-bar-provider>
  </n-config-provider>
</template>

<style scoped>
.main-layout {
  background: transparent;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.layout-header {
  background: var(--header-bg);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border-color-light);
  box-shadow: var(--shadow-sm);
  position: sticky;
  top: 0;
  z-index: 100;
  padding: 0 12px;
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  max-width: 1280px;
  margin: 0 auto;
  gap: 16px;
  width: 100%;
  box-sizing: border-box;
}

.header-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.brand-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 35px;
  height: 35px;
  border-radius: 10px;
  background: var(--primary-gradient);
  color: #ffffff;
  font-weight: 800;
  box-shadow: var(--shadow-md);
}

.brand-title {
  font-size: 1.35rem;
  font-weight: 800;
  background: var(--primary-gradient);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin: 0;
  letter-spacing: -0.3px;
  line-height: 1;
}

/* Flex middle zone — no absolute centering (that overlapped timezone / 参数设置). */
.header-nav {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  gap: 4px;
  flex-wrap: nowrap;
  justify-content: center;
  align-items: center;
  overflow-x: auto;
  scrollbar-width: thin;
}

.nav-link {
  display: inline-flex;
  align-items: center;
  height: 36px;
  padding: 0 10px;
  border-radius: 10px;
  color: var(--text-secondary);
  text-decoration: none;
  border: 1px solid transparent;
  transition:
    background 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease;
  white-space: nowrap;
  flex-shrink: 0;
  position: relative;
  z-index: 1;
}

.nav-link:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
  border-color: var(--border-color-light);
}

.nav-link.router-link-active {
  background: var(--hover-bg);
  color: var(--text-primary);
  border-color: var(--border-color-light);
}

.header-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  position: relative;
  z-index: 2;
}

.layout-content {
  flex: 1;
  overflow: auto;
  background: transparent;
  max-width: 1280px;
  margin: 0 auto;
  width: 100%;
}

.content-wrapper {
  padding: 16px;
  min-height: calc(100vh - 68px);
}

@media (max-width: 1100px) {
  .header-content {
    flex-wrap: wrap;
  }
  .header-nav {
    order: 3;
    flex: 1 1 100%;
    justify-content: flex-start;
    padding-top: 4px;
  }
  .header-actions {
    margin-left: auto;
  }
}
</style>
