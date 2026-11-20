/** DEV-ONLY sign-in: pick a demo user; the demo backend issues a stub-core token. */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { setLang } from "@/i18n";
import { useDev } from "./DevSession";

export function SignIn() {
  const { t, i18n } = useTranslation();
  const { users, signIn } = useDev();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const hi = i18n.language === "hi";
  return (
    <div className="flex min-h-screen items-center justify-center bg-page px-page">
      <div className="em-card w-full max-w-md p-card">
        <div className="mb-4 flex items-center justify-between">
          <h1 className="text-title">{t("shell.brand")}</h1>
          <button type="button" className="em-btn" onClick={() => setLang(hi ? "en" : "hi")}>{hi ? "English" : "हिन्दी"}</button>
        </div>
        <p className="mb-1 text-body">{t("signin.title")}</p>
        <p className="mb-4 text-note text-muted">{t("signin.dev_note")}</p>
        {!users.length ? <p role="alert" className="text-note text-bad">{t("signin.no_backend")}</p> : null}
        {error ? <p role="alert" className="mb-2 text-note text-bad">{t("common.error")}</p> : null}
        <ul className="flex flex-col gap-2">
          {users.map((u) => (
            <li key={u.user_id}>
              <button type="button" className="em-btn w-full justify-between py-2" disabled={busy !== null}
                data-testid={`signin-${u.user_id}`} data-persona={u.persona}
                onClick={async () => {
                  setBusy(u.user_id); setError(false);
                  try { await signIn(u.user_id); } catch { setError(true); } finally { setBusy(null); }
                }}>
                <span>{u.name}</span>
                <span className="text-label text-muted">{t(`persona.${u.persona}`)} · {t(`scope.${u.finance_scope}`)}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
