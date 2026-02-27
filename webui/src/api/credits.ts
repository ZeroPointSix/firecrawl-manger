import { http } from "@/api/http";

export type CreditSnapshot = {
  remaining_credits: number;
  plan_credits: number;
  billing_period_start?: string | null;
  billing_period_end?: string | null;
  snapshot_at: string;
  fetch_success: boolean;
  error_message?: string | null;
};

export type CachedCredits = {
  remaining_credits: number | null;
  plan_credits: number | null;
  total_credits?: number | null;
  last_updated_at: string | null;
  is_estimated: boolean;
};

export type CreditInfo = {
  api_key_id: number;
  cached_credits: CachedCredits;
  latest_snapshot: CreditSnapshot | null;
  next_refresh_at: string | null;
};

export type ClientCreditsInfo = {
  client_id: number;
  client_name: string;
  total_remaining_credits: number;
  total_plan_credits: number;
  total_credits?: number;
  usage_percentage: number;
  keys: Array<{
    api_key_id: number;
    name: string | null;
    remaining_credits: number;
    plan_credits: number;
    total_credits?: number;
    usage_percentage: number;
    last_updated_at: string | null;
  }>;
};

export async function getKeyCredits(keyId: number) {
  const res = await http.get<CreditInfo>(`/admin/keys/${keyId}/credits`);
  return res.data;
}

export async function getClientCredits(clientId: number) {
  const res = await http.get<ClientCreditsInfo>(`/admin/clients/${clientId}/credits`);
  return res.data;
}

export async function refreshKeyCredits(keyId: number) {
  const res = await http.post<{ api_key_id: number; snapshot: CreditSnapshot }>(
    `/admin/keys/${keyId}/credits/refresh`
  );
  return res.data;
}

export async function refreshAllCredits(payload?: { key_ids?: number[]; force?: boolean }) {
  const res = await http.post<{
    total: number;
    success: number;
    failed: number;
    results: Array<{
      api_key_id: number;
      success: boolean;
      remaining_credits?: number;
      plan_credits?: number;
      error?: string;
    }>;
  }>("/admin/keys/credits/refresh-all", payload || {});
  return res.data;
}

export async function getCreditsHistory(
  keyId: number,
  params: {
    since?: string;
    until?: string;
    limit?: number;
  } = {}
) {
  const res = await http.get<{ api_key_id: number; snapshots: CreditSnapshot[]; total_count: number }>(
    `/admin/keys/${keyId}/credits/history`,
    { params }
  );
  return res.data;
}

