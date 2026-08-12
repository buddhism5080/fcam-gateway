import { createRouter, createWebHashHistory } from "vue-router";

import AuditView from "@/views/AuditView.vue";
import ClientsView from "@/views/ClientsView.vue";
import DashboardView from "@/views/DashboardView.vue";
import KeysView from "@/views/KeysView.vue";
import LogsView from "@/views/LogsView.vue";
import SettingsView from "@/views/SettingsView.vue";

const router = createRouter({
  history: createWebHashHistory("/ui2/"),
  routes: [
    { path: "/", redirect: "/dashboard" },
    { path: "/dashboard", component: DashboardView },
    { path: "/keys", component: KeysView },
    { path: "/clients", component: ClientsView },
    // back-compat alias for old combined page
    { path: "/clients-keys", redirect: "/keys" },
    { path: "/logs", component: LogsView },
    { path: "/audit", component: AuditView },
    { path: "/settings", component: SettingsView },
  ],
});

export default router;
