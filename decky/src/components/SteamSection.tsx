import { ButtonItem, PanelSectionRow, TextField } from "@decky/ui";
import { useEffect, useState } from "react";
import { FaSteam } from "react-icons/fa";
import { KeyTest, Settings, SteamProfile, resolveSteamId, setSettings, steamProfile, testSteamKey } from "../api";
import { useAction } from "../hooks";
import { currentSteamId } from "../steam";
import { ErrorLine, Note } from "./common";
import { ApiKeyQr } from "./QrCode";
import { API_KEY_URL } from "./qrData";
import { SecretField } from "./SecretField";
import { CardState, ConnectionCard, PrimaryButton } from "./ConnectionCard";
import { StatusRow } from "./ui";

const KEY_RE = /^[0-9a-fA-F]{32}$/;
const ID_RE = /^\d{17}$/;

type Act = ReturnType<typeof useAction>;

export const STEAM_ID_HELP = "Paste your profile link, your 17-digit SteamID64, a SteamID3 like [U:1:12345] or your custom profile name. A custom name is looked up with the API key below.";
export const KEY_HELP = "Get a free key at steamcommunity.com/dev/apikey (type any domain name, for example localhost). Scan the code to open the page on your phone.";
export const GAMES_PUBLIC_NOTE = "For Steam Stats, set Game details to Public in your Steam profile's privacy settings. A private profile shows no games.";

/** The persona name and avatar of the saved account, fetched once per id. `null` while loading or when no account is saved. */
function useProfile(id: string): { profile: SteamProfile | null; error: string | null } {
  const [state, setState] = useState<{ id: string; profile: SteamProfile | null; error: string | null } | null>(null);
  useEffect(() => {
    if (!ID_RE.test(id)) return;
    let alive = true;
    steamProfile(id).then((r) => alive && setState(r.ok ? { id, profile: r, error: null } : { id, profile: null, error: r.error }));
    return () => {
      alive = false;
    };
  }, [id]);
  return state && state.id === id ? state : { profile: null, error: null };
}

type TestState = { busy: boolean; ok?: KeyTest; error?: string };

/** The header of the Steam card: who the account is, whether the key is saved or was shown to work, or why it does not. */
export function steamStatus(s: Pick<Settings, "steamid64" | "steam_api_key">, persona: string | null, test: TestState): CardState {
  if (test.error) return { tone: "error", label: "Error", detail: test.error };
  if (!ID_RE.test(s.steamid64)) return { tone: "muted", label: "Not set", detail: "Add your account to show your games and achievements on screens." };
  const who = persona ?? "Account saved";
  if (test.ok) return { tone: "ok", label: "Connected", detail: `${test.ok.persona}, key works` };
  return { tone: "info", label: "Set", detail: `${who}, ${s.steam_api_key === "set" ? "key saved" : "no API key yet"}` };
}

function KeyTester({ s, test, run }: { s: Settings; test: TestState; run: () => void }) {
  const ready = s.steam_api_key === "set" && ID_RE.test(s.steamid64);
  const ok = test.ok;
  return (
    <>
      <ButtonItem layout="below" disabled={test.busy || !ready} onClick={run}>{test.busy ? "Testing…" : "Test key"}</ButtonItem>
      {!ready && <Note>{s.steam_api_key !== "set" ? "Save a key to test it." : "Save your SteamID first: the test asks Steam about that account."}</Note>}
      <ErrorLine error={test.error} />
      {ok && (
        <>
          <PanelSectionRow><StatusRow tone="ok" title={`Works: ${ok.persona}`}>The key is accepted and Steam knows the account.</StatusRow></PanelSectionRow>
          {ok.games_visible === false && (
            <PanelSectionRow>
              <StatusRow tone="warn" title="Game details are private">
                {ok.profile_public ? "Steam Stats cannot read your games until Game details is Public." : "Your profile is private, so Steam Stats cannot read your games. Make the profile and its Game details Public."}
              </StatusRow>
            </PanelSectionRow>
          )}
        </>
      )}
    </>
  );
}

/** Settings, Connections, Steam: the account (one press from the signed-in user, or paste a link), the Web API key with a QR code and a test. */
export function SteamSection({ s, act, open, onToggle }: { s: Settings; act: Act; open: boolean; onToggle: () => void }) {
  const [text, setText] = useState(s.steamid64);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [test, setTest] = useState<TestState>({ busy: false });
  const { profile, error: profileError } = useProfile(s.steamid64);
  const busy = act.busy || working;
  const runTest = async () => {
    setTest({ busy: true });
    const r = await testSteamKey();
    setTest(r.ok ? { busy: false, ok: r } : { busy: false, error: r.error });
  };
  const saveId = (id: string) => act.run(setSettings({ steamid64: id }));
  const useMine = async () => {
    setWorking(true);
    setError(null);
    const id = await currentSteamId();
    if (!id) {
      setError("Steam did not say which account is signed in. Paste your profile link below instead.");
    } else {
      setText(id);
      await saveId(id);
    }
    setWorking(false);
  };
  const save = async () => {
    const t = text.trim();
    setError(null);
    if (t === "" || ID_RE.test(t)) {
      await saveId(t);
      return;
    }
    setWorking(true);
    const r = await resolveSteamId(t);
    if (!r.ok) {
      setError(r.error);
    } else {
      setText(r.steamid64);
      await saveId(r.steamid64);
    }
    setWorking(false);
  };
  const mine = <>Use my Steam account</>;
  return (
    <ConnectionCard title="Steam" icon={<FaSteam />} avatar={profile?.avatar} state={steamStatus(s, profile?.persona ?? null, test)} open={open} onToggle={onToggle}>
      <Note>Optional. Adds your profile, recent games and achievements to screens that show them.</Note>
      {s.steamid64 === "" ? (
        <PrimaryButton disabled={busy} onClick={useMine}>{mine}</PrimaryButton>
      ) : (
        <ButtonItem layout="below" disabled={busy} onClick={useMine}>{mine}</ButtonItem>
      )}
      {profile && (
        <PanelSectionRow>
          <div className="ink-status-d" role="status" aria-label={`Steam account: ${profile.persona}`}>
            {profile.persona} · SteamID64 {s.steamid64}{profile.public ? "" : " · profile is private"}
          </div>
        </PanelSectionRow>
      )}
      {!profile && profileError && <Note>{`The profile could not be looked up: ${profileError}`}</Note>}
      <PanelSectionRow><TextField label="Steam profile link or ID" value={text} onChange={(e) => setText(e.target.value)} /></PanelSectionRow>
      <Note>{STEAM_ID_HELP}</Note>
      <ErrorLine error={error} />
      <ButtonItem layout="below" disabled={busy || text.trim() === s.steamid64} onClick={save}>Save SteamID</ButtonItem>
      <Note>{KEY_HELP}</Note>
      <PanelSectionRow>
        <div className="ink-qr"><ApiKeyQr /><div className="ink-qr-u">{API_KEY_URL.replace("https://", "")}</div></div>
      </PanelSectionRow>
      <SecretField
        label="Steam Web API key"
        isSet={s.steam_api_key === "set"}
        hint="Stored on this device only. It is never shown again."
        normalize={(v) => v.trim()}
        invalid={(v) => (KEY_RE.test(v) ? null : "A Steam Web API key is 32 characters: digits and the letters a to f.")}
        onSave={(v) => setSettings({ steam_api_key: v })}
        onRemove={() => setSettings({ steam_api_key: "" })}
      />
      <KeyTester s={s} test={test} run={runTest} />
      <Note>{GAMES_PUBLIC_NOTE}</Note>
    </ConnectionCard>
  );
}
