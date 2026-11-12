import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SecretField } from "../src/components/SecretField";

const SECRET = "hunter2-hunter2";
const setup = (o: Partial<Parameters<typeof SecretField>[0]> = {}) => {
  const onSave = vi.fn(async (_v: string) => ({ ok: true as const }));
  const onRemove = vi.fn(async () => ({ ok: true as const }));
  const view = render(<SecretField label="Wi-Fi password" isSet={false} onSave={onSave} onRemove={onRemove} {...o} />);
  return { onSave, onRemove, ...view };
};
const input = (name = "Wi-Fi password") => screen.getByLabelText(name) as HTMLInputElement;

describe("SecretField", () => {
  it("is a masked password input that starts empty, with a 'not set' state in words", () => {
    setup();
    expect(input()).toHaveAttribute("type", "password");
    expect(input()).toHaveValue("");
    expect(screen.getByRole("status", { name: "Wi-Fi password: not set" })).toBeInTheDocument();
  });

  it("when a value is saved: says 'saved', never prefills, offers Replace and Remove", () => {
    const { container } = setup({ isSet: true });
    expect(screen.getByRole("status", { name: "Wi-Fi password: saved" })).toBeInTheDocument();
    expect(input("New Wi-Fi password")).toHaveValue("");
    expect(input("New Wi-Fi password")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Replace Wi-Fi password" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Wi-Fi password" })).toBeInTheDocument();
    expect(container.querySelectorAll("input[value]:not([value=''])")).toHaveLength(0);
  });

  it("with nothing saved there is nothing to remove, and Save needs a value", () => {
    setup();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Save Wi-Fi password" })).toBeDisabled();
    fireEvent.change(input(), { target: { value: "x" } });
    expect(screen.getByRole("button", { name: "Save Wi-Fi password" })).not.toBeDisabled();
  });

  it("save sends the value, empties the field at once, and the value is nowhere in the page afterwards", async () => {
    const { onSave, container } = setup();
    fireEvent.change(input(), { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save Wi-Fi password" }));
    expect(onSave).toHaveBeenCalledExactlyOnceWith(SECRET);
    await waitFor(() => expect(screen.getByRole("button", { name: "Save Wi-Fi password" })).toBeDisabled());
    expect(input()).toHaveValue("");
    expect(container.innerHTML).not.toContain(SECRET);
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it("remove sends an empty value through onRemove and nothing else", async () => {
    const { onSave, onRemove } = setup({ isSet: true });
    fireEvent.click(screen.getByRole("button", { name: "Remove Wi-Fi password" }));
    await waitFor(() => expect(onRemove).toHaveBeenCalledTimes(1));
    expect(onSave).not.toHaveBeenCalled();
  });

  it("a refused save shows the reason, scrubs the secret from it, and does not put the secret back", async () => {
    const onSave = vi.fn(async () => ({ ok: false as const, error: `bad value ${SECRET} rejected` }));
    const { container } = setup({ onSave });
    fireEvent.change(input(), { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save Wi-Fi password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("bad value *** rejected");
    expect(container.innerHTML).not.toContain(SECRET);
    expect(input()).toHaveValue("");
  });

  it("a refused removal shows the reason", async () => {
    setup({ isSet: true, onRemove: async () => ({ ok: false, error: "cannot save settings" }) });
    fireEvent.click(screen.getByRole("button", { name: "Remove Wi-Fi password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot save settings");
  });

  it("normalize trims what is sent; invalid blocks Save and explains, without echoing the value", () => {
    const { onSave } = setup({ normalize: (v) => v.trim(), invalid: (v) => (v.length < 4 ? "too short" : null) });
    fireEvent.change(input(), { target: { value: "  ab  " } });
    expect(screen.getByRole("alert")).toHaveTextContent("too short");
    expect(screen.getByRole("alert")).not.toHaveTextContent("ab");
    expect(screen.getByRole("button", { name: "Save Wi-Fi password" })).toBeDisabled();
    fireEvent.change(input(), { target: { value: "  abcd  " } });
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save Wi-Fi password" }));
    expect(onSave).toHaveBeenCalledWith("abcd");
  });

  it("whitespace alone is nothing to save", () => {
    setup({ normalize: (v) => v.trim() });
    fireEvent.change(input(), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "Save Wi-Fi password" })).toBeDisabled();
  });

  it("locks both buttons while a request is running", async () => {
    let done: (v: { ok: true }) => void = () => undefined;
    setup({ isSet: true, onSave: () => new Promise((r) => { done = r; }) });
    fireEvent.change(input("New Wi-Fi password"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Replace Wi-Fi password" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Remove Wi-Fi password" })).toBeDisabled());
    done({ ok: true });
    await waitFor(() => expect(screen.getByRole("button", { name: "Remove Wi-Fi password" })).not.toBeDisabled());
  });

  it("never logs the secret", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => undefined));
    setup();
    fireEvent.change(input(), { target: { value: SECRET } });
    fireEvent.click(screen.getByRole("button", { name: "Save Wi-Fi password" }));
    await waitFor(() => expect(input()).toHaveValue(""));
    for (const s of spies) expect(JSON.stringify(s.mock.calls)).not.toContain(SECRET);
  });
});
