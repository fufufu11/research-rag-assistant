import { ApiClientError } from "./client";
import { SseEventError, SseProtocolError } from "./sse";

export function friendlyApiError(error: unknown, action: string): string {
  if (error instanceof ApiClientError) {
    if (error.status === 400 || error.status === 422) {
      return `${action}失败：请求内容有误，请检查后重试。`;
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
    return `${action}失败：请求未能完成，请重试。`;
  }
  if (error instanceof TypeError) {
    return `${action}失败：无法连接服务，请检查网络后重试。`;
  }
  return `${action}失败：未知错误。`;
}

export function friendlyChatError(error: unknown): string {
  if (error instanceof SseEventError || error instanceof SseProtocolError) {
    return "生成回答失败：服务未能完成回答，请重试。";
  }
  return friendlyApiError(error, "生成回答");
}
