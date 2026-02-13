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

