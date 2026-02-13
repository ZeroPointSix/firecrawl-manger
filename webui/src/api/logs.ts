import { http } from "@/api/http";

export type RequestLogItem = {
  id: number;
  created_at: string;
  level: "info" | "warn" | "error";
  request_id: string;
  client_id: number | null;
  api_key_id: number | null;
  api_key_masked: string | null;
  method: string;
  endpoint: string;
  status_code: number | null;
  response_time_ms: number | null;
  success: boolean | null;
  retry_count: number;
  error_message: string | null;
  idempotency_key: string | null;
};

export type CursorPage<T> = { items: T[]; next_cursor: number | null; has_more: boolean };

export async function fetchRequestLogs(params?: Record<string, any>) {
  const res = await http.get<CursorPage<RequestLogItem>>("/admin/logs", { params });
  return res.data;
}

export type AuditLogItem = {
  id: number;
  created_at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  ip: string | null;
  user_agent: string | null;
};

export async function fetchAuditLogs(params?: Record<string, any>) {
  const res = await http.get<CursorPage<AuditLogItem>>("/admin/audit-logs", { params });
  return res.data;
}
