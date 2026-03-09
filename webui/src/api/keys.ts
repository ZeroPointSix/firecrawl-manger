import { http } from "@/api/http";

export type KeyItem = {
  id: number;
  client_id: number | null;
  name: string | null;
  api_key_masked: string;
  account_username: string | null;
  account_verified_at: string | null;
  plan_type: string;
  is_active: boolean;
  status: string;
  daily_quota: number;
  daily_usage: number;
  quota_reset_at: string | null;
  max_concurrent: number;
  current_concurrent: number;
  rate_limit_per_min: number;
  cooldown_until: string | null;
  total_requests: number;
  last_used_at: string | null;
  created_at: string;
  last_credit_snapshot_id: number | null;
  last_credit_check_at: string | null;
  cached_remaining_credits: number | null;
  cached_plan_credits: number | null;
  cached_total_credits?: number | null;
  cached_is_estimated?: boolean;
  billing_period_start?: string | null;
  billing_period_end?: string | null;
  next_refresh_at: string | null;
  provider: string;
};

export type Pagination = {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
};

export type KeyListResponse = {
  items: KeyItem[];
  pagination?: Pagination;
};

export async function fetchKeys(
  opts: { clientId?: number; unassigned?: boolean; page?: number; pageSize?: number; q?: string; provider?: string } = {}
) {
  const params: Record<string, number | string> = {};
  if (opts.unassigned) params.client_id = 0;
  else if (opts.clientId) params.client_id = opts.clientId;
  if (opts.page) params.page = opts.page;
  if (opts.pageSize) params.page_size = opts.pageSize;
  if (opts.q) params.q = opts.q;
  if (opts.provider) params.provider = opts.provider;
  const res = await http.get<KeyListResponse>("/admin/keys", { params: Object.keys(params).length ? params : undefined });
  return res.data;
}

export type CreateKeyRequest = {
  api_key: string;
  client_id?: number | null;
  name?: string | null;
  plan_type?: string;
  daily_quota?: number;
  max_concurrent?: number;
  rate_limit_per_min?: number;
  is_active?: boolean;
  provider?: string;
};

export async function createKey(payload: CreateKeyRequest) {
  const res = await http.post<KeyItem>("/admin/keys", payload);
  return res.data;
}

export type ImportKeysTextRequest = {
  client_id?: number | null;
  text: string;
  plan_type?: string;
  daily_quota?: number;
  max_concurrent?: number;
  rate_limit_per_min?: number;
  is_active?: boolean;
  provider?: string;
};

export async function importKeysText(payload: ImportKeysTextRequest) {
  const res = await http.post<{
    created: number;
    updated: number;
    skipped: number;
    failed: number;
    failures: Array<{ line_no: number; raw: string; message: string }>;
  }>("/admin/keys/import-text", payload);
  return res.data;
}

export type UpdateKeyRequest = {
  client_id?: number | null;
  name?: string | null;
  plan_type?: string | null;
  daily_quota?: number | null;
  max_concurrent?: number | null;
  rate_limit_per_min?: number | null;
  is_active?: boolean | null;
  api_key?: string | null;
};

export async function updateKey(keyId: number, payload: UpdateKeyRequest) {
  const res = await http.put<KeyItem>(`/admin/keys/${keyId}`, payload);
  return res.data;
}

export async function deleteKey(keyId: number) {
  await http.delete(`/admin/keys/${keyId}`);
}

export async function purgeKey(keyId: number) {
  await http.delete(`/admin/keys/${keyId}/purge`);
}

export type TestKeyRequest = {
  mode?: string;
  test_url?: string;
};

export type TestKeyResponse = {
  key_id: number;
  ok: boolean;
  upstream_status_code: number | null;
  latency_ms: number | null;
  observed: {
    cooldown_until: string | null;
    status: string;
  };
};

export async function testKey(keyId: number, payload?: TestKeyRequest) {
  const res = await http.post<TestKeyResponse>(`/admin/keys/${keyId}/test`, payload || {}, { timeout: 60_000 });
  return res.data;
}

export type BatchKeyPatch = {
  name?: string | null;
  plan_type?: string | null;
  daily_quota?: number | null;
  max_concurrent?: number | null;
  rate_limit_per_min?: number | null;
  is_active?: boolean | null;
};

export type BatchKeyTest = {
  mode?: string;
  test_url?: string;
};

export type BatchKeysRequest = {
  ids: number[];
  patch?: BatchKeyPatch;
  reset_cooldown?: boolean;
  soft_delete?: boolean;
  test?: BatchKeyTest;
};

export type BatchKeysResultItem = {
  id: number;
  ok: boolean;
  key?: KeyItem;
  test?: { ok: boolean; upstream_status_code: number | null; latency_ms: number | null } | null;
  error?: { code: string; message: string };
};

export type BatchKeysResponse = {
  requested: number;
  succeeded: number;
  failed: number;
  results: BatchKeysResultItem[];
};

export async function batchKeys(payload: BatchKeysRequest) {
  const timeout = payload.test ? 120_000 : undefined;
  const res = await http.post<BatchKeysResponse>("/admin/keys/batch", payload, timeout ? { timeout } : undefined);
  return res.data;
}
