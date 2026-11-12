// A form generated from a screen's `settings` schema (screen.json). Types: bool, number, string, enum.
import { ButtonItem, DropdownItem, PanelSectionRow, SliderField, TextField, ToggleField } from "@decky/ui";
import { useEffect, useState } from "react";
import { Result, SettingDef, SettingValue } from "../api";
import { useAction } from "../hooks";
import { ErrorLine, Note } from "./common";

type Draft = Record<string, SettingValue>;

function Control({ def, value, onChange }: { def: SettingDef; value: SettingValue; onChange: (v: SettingValue) => void }) {
  const label = def.label ?? def.key;
  switch (def.type) {
    case "bool":
      return <ToggleField label={label} checked={value === true} onChange={onChange} />;
    case "enum":
      return (
        <DropdownItem
          label={label}
          rgOptions={(def.values ?? []).map((v) => ({ data: v, label: v }))}
          selectedOption={value}
          onChange={(o: { data: string }) => onChange(o.data)}
        />
      );
    case "number":
      if (def.min !== undefined && def.max !== undefined) {
        const step = Number.isInteger(def.min) && Number.isInteger(def.max) && Number.isInteger(def.default as number) ? 1 : (def.max - def.min) / 100;
        return <SliderField label={label} value={Number(value)} min={def.min} max={def.max} step={step} showValue onChange={onChange} />;
      }
      return (
        <TextField
          label={label}
          mustBeNumeric
          value={String(value)}
          onChange={(e) => {
            const n = Number(e.target.value);
            // keep what the user typed if it is not a number yet; the backend rejects it with a message
            onChange(e.target.value.trim() !== "" && Number.isFinite(n) ? n : e.target.value);
          }}
        />
      );
    default:
      return <TextField label={label} value={String(value)} onChange={(e) => onChange(e.target.value)} />;
  }
}

const effective = (src: Draft, d: SettingDef) => src[d.key] ?? d.default;

export function SettingForm({ screenId, schema, values, onSave }: {
  screenId: string;
  schema: SettingDef[];
  values: Draft;
  onSave: (id: string, draft: Draft) => Promise<Result>;
}) {
  const stored = JSON.stringify(values);
  const [draft, setDraft] = useState<Draft>(values);
  const [saved, setSaved] = useState(false);
  const act = useAction();
  useEffect(() => setDraft(values), [stored]); // eslint-disable-line react-hooks/exhaustive-deps -- follow the backend when it changes the values
  if (schema.length === 0) return null;
  const dirty = schema.some((d) => effective(draft, d) !== effective(values, d));
  const atDefaults = schema.every((d) => effective(values, d) === d.default);
  const save = async (d: Draft) => {
    const r = await act.run(onSave(screenId, d));
    setSaved(r.ok);
    if (r.ok && Object.keys(d).length === 0) setDraft(Object.fromEntries(schema.map((s) => [s.key, s.default])));
  };
  return (
    <>
      {schema.map((def) => (
        <PanelSectionRow key={def.key}>
          <Control def={def} value={effective(draft, def)} onChange={(v) => { setSaved(false); setDraft((d) => ({ ...d, [def.key]: v })); }} />
        </PanelSectionRow>
      ))}
      <ErrorLine error={act.error} />
      {dirty && !act.error && <Note>You have unsaved changes.</Note>}
      {!dirty && saved && !act.error && <Note>Saved. The preview above shows the new look.</Note>}
      <ButtonItem layout="below" disabled={act.busy || !dirty} onClick={() => save(draft)}>
        Save settings
      </ButtonItem>
      <ButtonItem layout="below" disabled={act.busy || atDefaults} onClick={() => save({})}>
        Reset to defaults
      </ButtonItem>
    </>
  );
}
