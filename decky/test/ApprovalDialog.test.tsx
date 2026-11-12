import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApprovalDialog, CODE_WARNING } from "../src/components/ApprovalDialog";
import { screen as scr } from "./fixtures";

const SHA = "ab".repeat(32);
const pkg = scr("hello", { name: "Hello Code", author: "Mallory", version: "2.3.4", builtin: false, has_code: true, code_sha256: SHA, needs_approval: true, approved: false });

describe("ApprovalDialog", () => {
  it("shows author, id, version, sha256 and the not-sandboxed warning", () => {
    render(<ApprovalDialog pkg={pkg} onApprove={vi.fn()} />);
    const d = screen.getByRole("dialog");
    expect(d).toHaveTextContent("Hello Code");
    expect(d).toHaveTextContent("Author: Mallory");
    expect(d).toHaveTextContent("Id: hello");
    expect(d).toHaveTextContent("Version: 2.3.4");
    expect(d).toHaveTextContent(SHA);
    expect(screen.getByRole("alert")).toHaveTextContent(CODE_WARNING);
    expect(CODE_WARNING).toMatch(/NOT sandboxed/);
    expect(CODE_WARNING).toMatch(/read your files and use the network/);
  });

  it("approve calls back with exactly this package and closes", () => {
    const onApprove = vi.fn(), closeModal = vi.fn();
    render(<ApprovalDialog pkg={pkg} onApprove={onApprove} closeModal={closeModal} />);
    fireEvent.click(screen.getByText("Approve and enable"));
    expect(onApprove).toHaveBeenCalledWith(pkg);
    expect(closeModal).toHaveBeenCalled();
  });

  it("cancel approves nothing", () => {
    const onApprove = vi.fn(), closeModal = vi.fn();
    render(<ApprovalDialog pkg={pkg} onApprove={onApprove} closeModal={closeModal} />);
    fireEvent.click(screen.getByText("Cancel"));
    expect(onApprove).not.toHaveBeenCalled();
    expect(closeModal).toHaveBeenCalled();
  });
});
