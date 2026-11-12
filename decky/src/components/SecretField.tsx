// A secret (Wi-Fi password, Steam Web API key) the user can set or remove but never read back.
// Rules: the input is a masked password field (Steam's own text field: the controller keyboard opens when it is activated),
// it starts empty (the backend only says "set" or "unset", so there is nothing to prefill), the draft is cleared the moment it
// is sent, and the value is scrubbed from any error text that comes back.
import { ButtonItem, PanelSectionRow, TextField } from "@decky/ui";
import { useState } from "react";
import { Result } from "../api";
import { ErrorLine } from "./common";
import { StatusRow } from "./ui";

export function SecretField({ label, isSet, hint, onSave, onRemove, normalize = (v) => v, invalid }: {
  label: string;
  isSet: boolean;
  hint?: string;
  onSave: (value: string) => Promise<Result>;
  onRemove: () => Promise<Result>;
  normalize?: (v: string) => string;
  invalid?: (v: string) => string | null;
}) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const value = normalize(draft);
  const problem = value === "" ? null : invalid?.(value) ?? null;
  const run = async (p: Promise<Result>, secret: string) => {
    setBusy(true);
    setError(null);
    const r = await p;
    setBusy(false);
    if (!r.ok) setError(secret ? r.error.split(secret).join("***") : r.error);
  };
  const save = () => {
    const sent = value;
    setDraft(""); // never keep the secret in the UI after it is sent
    return run(onSave(sent), sent);
  };
  return (
    <>
      <PanelSectionRow>
        <StatusRow tone={isSet ? "ok" : "muted"} title={`${label}: ${isSet ? "saved" : "not set"}`}>
          {hint ?? "Stored on this device. It is never shown again."}
        </StatusRow>
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField label={isSet ? `New ${label}` : label} bIsPassword value={draft} onChange={(e) => setDraft(e.target.value)} />
      </PanelSectionRow>
      <ErrorLine error={problem ?? error} />
      <ButtonItem layout="below" disabled={busy || value === "" || problem !== null} onClick={save}>
        {isSet ? `Replace ${label}` : `Save ${label}`}
      </ButtonItem>
      {isSet && (
        <ButtonItem layout="below" disabled={busy} onClick={() => run(onRemove(), "")}>
          {`Remove ${label}`}
        </ButtonItem>
      )}
    </>
  );
}
