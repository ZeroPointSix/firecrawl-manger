/**
 * 格式化时间戳为易读格式
 * @param timestamp ISO 8601 格式的时间戳（如 "2026-02-25T07:38:33.656012Z"）
 * @returns 格式化后的时间字符串（如 "2026-02-25 07:38:33"）
 */
export function formatTimestamp(timestamp: string | null | undefined): string {
  if (!timestamp) return "-";

  try {
    const date = new Date(timestamp);

    // 检查日期是否有效
    if (isNaN(date.getTime())) return timestamp;

    // 格式化为 YYYY-MM-DD HH:mm:ss
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    const hours = String(date.getHours()).padStart(2, "0");
    const minutes = String(date.getMinutes()).padStart(2, "0");
    const seconds = String(date.getSeconds()).padStart(2, "0");

    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
  } catch {
    return timestamp;
  }
}

/**
 * 格式化时间戳为相对时间（如 "5 分钟前" / "55 分钟后"）。
 */
export function formatRelativeTime(timestamp: string | null | undefined): string {
  if (!timestamp) return "-";

  try {
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return timestamp;

    const diffMs = date.getTime() - Date.now();
    const absSeconds = Math.floor(Math.abs(diffMs) / 1000);
    const isFuture = diffMs > 0;

    if (absSeconds < 5) return isFuture ? "马上" : "刚刚";
    if (absSeconds < 60) return `${absSeconds} 秒${isFuture ? "后" : "前"}`;

    const absMinutes = Math.floor(absSeconds / 60);
    if (absMinutes < 60) return `${absMinutes} 分钟${isFuture ? "后" : "前"}`;

    const absHours = Math.floor(absMinutes / 60);
    if (absHours < 24) return `${absHours} 小时${isFuture ? "后" : "前"}`;

    const absDays = Math.floor(absHours / 24);
    if (absDays < 7) return `${absDays} 天${isFuture ? "后" : "前"}`;

    return formatTimestamp(timestamp);
  } catch {
    return timestamp;
  }
}

export function formatDate(timestamp: string | null | undefined): string {
  if (!timestamp) return "-";
  try {
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return timestamp;

    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  } catch {
    return timestamp;
  }
}
