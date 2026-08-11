import { http } from "@/api/http";

export type EncryptionStatus = {
  master_key_configured: boolean;
  has_decrypt_failures: boolean;
  suggestion: string;
};

export async function fetchEncryptionStatus() {
  const res = await http.get<EncryptionStatus>("/admin/encryption-status");
  return res.data;
}

export type DashboardStats = {
  keys: { total: number; failed: number };
  clients: { total: number };
  requests_24h: { total: number; failed: number; error_rate: number };
};

export async function fetchDashboardStats(clientId?: number) {
  const res = await http.get<DashboardStats>("/admin/dashboard/stats", {
    params: clientId ? { client_id: clientId } : undefined,
  });
  return res.data;
}

export type ChartDataset = { label: string; color: string; data: number[] };
export type DashboardChart = {
  range: string;
  bucket: string;
  tz: string;
  labels: string[];
  datasets: ChartDataset[];
};

export async function fetchDashboardChart(opts: { tz: string; clientId?: number }) {
  const params: Record<string, string | number> = { tz: opts.tz, range: "24h", bucket: "hour" };
  if (opts.clientId) params.client_id = opts.clientId;
  const res = await http.get<DashboardChart>("/admin/dashboard/chart", { params });
  return res.data;
}

export type ClientUsageItem = {
  id: number;
  name: string;
  is_active: boolean;
  status: string;
  rate_limit_per_min: number;
  max_concurrent: number;
  max_retries: number;
  daily_quota: number | null;
  daily_usage: number;
  requests: {
    total: number;
    failed: number;
    success: number;
    rate_limited: number;
    retry_sum: number;
    error_rate: number;
  };
};

export async function fetchClientUsage(hours = 24) {
  const res = await http.get<{ hours: number; items: ClientUsageItem[] }>("/admin/dashboard/clients", {
    params: { hours },
  });
  return res.data;
}

export type RuntimeScheduling = {
  file: {
    freshness_half_life_seconds: number;
    unknown_credit_baseline: number;
    credit_workers: number;
  };
  effective: {
    freshness_half_life_seconds: number;
    unknown_credit_baseline: number;
    credit_workers: number;
  };
  overrides: Record<string, number | null | undefined>;
  http_connection_pool_enabled: boolean;
};

export async function fetchRuntimeScheduling() {
  const res = await http.get<RuntimeScheduling>("/admin/runtime/scheduling");
  return res.data;
}

export async function updateRuntimeScheduling(payload: {
  freshness_half_life_seconds?: number | null;
  unknown_credit_baseline?: number | null;
  credit_workers?: number | null;
  clear_credit_workers_override?: boolean;
}) {
  const res = await http.put<RuntimeScheduling>("/admin/runtime/scheduling", payload);
  return res.data;
}
