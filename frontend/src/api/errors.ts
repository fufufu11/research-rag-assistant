import { ApiClientError } from "./client";

export function friendlyApiError(error: unknown, action: string): string {
  if (error instanceof ApiClientError) {
    if (error.status === 400 || error.status === 422) {
      return `${action}失败：请求内容有误。${error.detail}`;
    }
    if (error.status === 401 || error.status === 403) {
      return `${action}失败：API Key 无效或未设置。`;
    }
    if (error.status === 404) {
      return `${action}失败：目标不存在或已被删除。`;
    }
    if (error.status >= 500) {
      return `${action}失败：服务暂时不可用，请稍后重试。`;
    }
    return `${action}失败：${error.detail}`;
  }
  if (error instanceof TypeError) {
    return `${action}失败：无法连接服务，请检查网络后重试。`;
  }
  return `${action}失败：未知错误。`;
}
