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
  opts: { clientId?: number; unassigned?: boolean; page?: number; pageSize?: number; q?: string } = {}
) {
  const params: Record<string, number | string> = {};
  if (opts.unassigned) params.client_id = 0;
  else if (opts.clientId) params.client_id = opts.clientId;
  if (opts.page) params.page = opts.page;
  if (opts.pageSize) params.page_size = opts.pageSize;
  if (opts.q) params.q = opts.q;
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
  const res = await http.post<TestKeyResponse>(`/admin/keys/${keyId}/test`, payload || {});
  return res.data;
}
