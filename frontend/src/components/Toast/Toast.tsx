import { useEffect, useState } from "react";

// Toast：底部居中悬浮提示
// 用法：const { showToast, toastNode } = useToast(); showToast('已复制');
// 简化版：受控模式，由父组件传入 message + onClose
interface ToastProps {
  message: string | null;
  onClose: () => void;
  duration?: number;
}

export function Toast({ message, onClose, duration = 1800 }: ToastProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (message) {
      setVisible(true);
      const timer = setTimeout(() => {
        setVisible(false);
        // 等动画结束再 onClose 清空 message
        setTimeout(onClose, 250);
      }, duration);
      return () => clearTimeout(timer);
    }
    setVisible(false);
  }, [message, duration, onClose]);

  if (!message) return null;

  return (
    <div
      className={`toast ${visible ? "show" : ""}`}
      role="status"
      aria-live="polite"
    >
      {message}
    </div>
  );
}
