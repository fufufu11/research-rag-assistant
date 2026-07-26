import { useRef, useState, useEffect } from "react";
import type { KeyboardEvent } from "react";

// InputBar：底部 pill 形状输入栏
// - 左侧 '+' 上传按钮（触发隐藏 file input）
// - 中间 textarea（Enter 发送，Shift+Enter 换行）
// - 右侧圆形发送按钮
// - 上方居中免责声明
// - 流式输出期间禁用输入
// 设计稿：.trae/handoffs/ui_claude_v1.html .input-wrap
interface InputBarProps {
  onSubmit: (text: string) => void;
  onUploadFile: (file: File) => void;
  isStreaming: boolean;
  isUploading: boolean;
  disabled?: boolean;
}

export function InputBar({
  onSubmit,
  onUploadFile,
  isStreaming,
  isUploading,
  disabled = false,
}: InputBarProps) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 自动调整 textarea 高度
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
  }, [text]);

  const handleSubmit = () => {
    const trimmed = text.trim();
    if (!trimmed || isStreaming || disabled) return;
    onSubmit(trimmed);
    setText("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      onUploadFile(file);
      // 重置 input 以允许重复选择同一文件
      e.target.value = "";
    }
  };

  const isDisabled = isStreaming || disabled;

  return (
    <div className="input-wrap" data-testid="input-wrap">
      <div className="input-container">
        <div className="input-disclaimer">
          AI 可能出错，请核实重要信息
        </div>
        <div className="input-pill" data-testid="input-pill">
          <button
            type="button"
            className={`upload-btn ${isUploading ? "uploading" : ""}`}
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
            aria-label="上传文档"
            data-testid="upload-btn"
          >
            +
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,application/pdf"
            onChange={handleFileChange}
            style={{ display: "none" }}
            data-testid="file-input"
          />
          <textarea
            ref={textareaRef}
            className="input-field"
            placeholder={
              isStreaming ? "生成中…" : "输入问题，Enter 发送，Shift+Enter 换行"
            }
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isDisabled}
            rows={1}
            aria-label="问题输入框"
            data-testid="input-field"
          />
          <button
            type="button"
            className="send-btn"
            onClick={handleSubmit}
            disabled={!text.trim() || isDisabled}
            aria-label="发送"
            data-testid="send-btn"
          >
            ↑
          </button>
        </div>
      </div>
    </div>
  );
}
