import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { InputBar } from "./InputBar";

describe("InputBar", () => {
  it("渲染 pill 容器 + 上传按钮 + textarea + 发送按钮 + 免责声明", () => {
    render(
      <InputBar
        onSubmit={vi.fn()}
        onUploadFile={vi.fn()}
        isStreaming={false}
        isUploading={false}
      />,
    );
    expect(screen.getByTestId("input-pill")).toBeInTheDocument();
    expect(screen.getByTestId("upload-btn")).toBeInTheDocument();
    expect(screen.getByTestId("input-field")).toBeInTheDocument();
    expect(screen.getByTestId("send-btn")).toBeInTheDocument();
    expect(screen.getByText("AI 可能出错，请核实重要信息")).toBeInTheDocument();
  });

  it("textarea 为空时发送按钮 disabled", () => {
    render(
      <InputBar
        onSubmit={vi.fn()}
        onUploadFile={vi.fn()}
        isStreaming={false}
        isUploading={false}
      />,
    );
    expect(screen.getByTestId("send-btn")).toBeDisabled();
  });

  it("输入文本后发送按钮 enabled", () => {
    render(
      <InputBar
        onSubmit={vi.fn()}
        onUploadFile={vi.fn()}
        isStreaming={false}
        isUploading={false}
      />,
    );
    const ta = screen.getByTestId("input-field") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "你好" } });
    expect(screen.getByTestId("send-btn")).not.toBeDisabled();
  });

  it("Enter 触发 onSubmit 并清空 textarea", () => {
    const onSubmit = vi.fn();
    render(
      <InputBar
        onSubmit={onSubmit}
        onUploadFile={vi.fn()}
        isStreaming={false}
        isUploading={false}
      />,
    );
    const ta = screen.getByTestId("input-field") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "测试问题" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
    expect(onSubmit).toHaveBeenCalledWith("测试问题");
    expect(ta.value).toBe("");
  });

  it("Shift+Enter 不触发 onSubmit（换行）", () => {
    const onSubmit = vi.fn();
    render(
      <InputBar
        onSubmit={onSubmit}
        onUploadFile={vi.fn()}
        isStreaming={false}
        isUploading={false}
      />,
    );
    const ta = screen.getByTestId("input-field") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "测试" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("isStreaming=true 时 textarea + 发送按钮 disabled", () => {
    render(
      <InputBar
        onSubmit={vi.fn()}
        onUploadFile={vi.fn()}
        isStreaming={true}
        isUploading={false}
      />,
    );
    expect(screen.getByTestId("input-field")).toBeDisabled();
    expect(screen.getByTestId("send-btn")).toBeDisabled();
  });

  it("isUploading=true 时上传按钮 disabled 且有 uploading class", () => {
    render(
      <InputBar
        onSubmit={vi.fn()}
        onUploadFile={vi.fn()}
        isStreaming={false}
        isUploading={true}
      />,
    );
    const uploadBtn = screen.getByTestId("upload-btn");
    expect(uploadBtn).toBeDisabled();
    expect(uploadBtn.className).toContain("uploading");
  });

  it("点击上传按钮触发隐藏 file input 的 click", () => {
    render(
      <InputBar
        onSubmit={vi.fn()}
        onUploadFile={vi.fn()}
        isStreaming={false}
        isUploading={false}
      />,
    );
    const fileInput = screen.getByTestId("file-input") as HTMLInputElement;
    const clickSpy = vi.spyOn(fileInput, "click");
    fireEvent.click(screen.getByTestId("upload-btn"));
    expect(clickSpy).toHaveBeenCalled();
  });

  it("文件选择器只接受 PDF", () => {
    render(
      <InputBar
        onSubmit={vi.fn()}
        onUploadFile={vi.fn()}
        isStreaming={false}
        isUploading={false}
      />,
    );
    expect(screen.getByTestId("file-input")).toHaveAttribute(
      "accept",
      ".pdf,application/pdf",
    );
  });

  it("选择文件后调用 onUploadFile 并重置 input value", () => {
    const onUploadFile = vi.fn();
    render(
      <InputBar
        onSubmit={vi.fn()}
        onUploadFile={onUploadFile}
        isStreaming={false}
        isUploading={false}
      />,
    );
    const fileInput = screen.getByTestId("file-input") as HTMLInputElement;
    const file = new File(["dummy"], "test.pdf", { type: "application/pdf" });
    fireEvent.change(fileInput, { target: { files: [file] } });
    expect(onUploadFile).toHaveBeenCalledWith(file);
    expect(fileInput.value).toBe("");
  });
});
