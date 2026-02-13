import { createRouter, createWebHashHistory } from "vue-router";

import AuditView from "@/views/AuditView.vue";
import ClientsKeysView from "@/views/ClientsKeysView.vue";
import DashboardView from "@/views/DashboardView.vue";
import LogsView from "@/views/LogsView.vue";

const router = createRouter({
  history: createWebHashHistory("/ui2/"),
  routes: [
    { path: "/", redirect: "/dashboard" },
    { path: "/dashboard", component: DashboardView },
    { path: "/clients", component: ClientsKeysView },
    { path: "/logs", component: LogsView },
    { path: "/audit", component: AuditView },
  ],
});

export default router;

