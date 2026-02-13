import axios from "axios";
import type { AxiosError } from "axios";

export type FcamErrorResponse = {
  request_id?: string;
  error?: { code?: string; message?: string };
};

export const http = axios.create({
  timeout: 30_000,
  headers: {
    Accept: "application/json",
  },
});

export function setAdminToken(token: string) {
  if (!token) {
    delete http.defaults.headers.common.Authorization;
    return;
  }
  http.defaults.headers.common.Authorization = `Bearer ${token}`;
}

export function getFcamErrorMessage(error: unknown): string {
  const axiosError = error as AxiosError<FcamErrorResponse>;
  const data = axiosError?.response?.data;
  const code = data?.error?.code;
  const msg = data?.error?.message;
  if (code && msg) return `${code}: ${msg}`;
  if (code) return code;
  if (msg) return msg;
  if (axiosError?.message) return axiosError.message;
  return "请求失败";
}

