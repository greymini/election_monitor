/** DEV-ONLY copy of the Election Monitor shell (design-ref/): brand left; the core's nav groups as
 * inert placeholders; the live Field group (मैदान in Hindi); constituency, language, theme, role, sign out
 * on the right; the core's footer note. Replaced by the real shell at merge. */
import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { setLang } from "@/i18n";
import { useSession, acLabel } from "@/lib/session";
import { useTheme } from "@/lib/theme";
import { FIELD_PAGES } from "@/field/nav";
import { useDev } from "./DevSession";

const CORE_GROUPS = ["overview", "results", "voters", "places", "politics", "analysis", "admin"] as const;
const WITH_MENU = new Set(["results", "voters", "places", "politics", "analysis", "admin"]);

function Menu({ id, label, open, setOpen, active, children }: {
  id: string; label: string; open: string | null; setOpen: (v: string | null) => void; active?: boolean;
  children?: React.ReactNode;
}) {
  const isOpen = open === id;
  return (
    <div className="relative" data-header-item={`nav-${id}`}>
      <button type="button" aria-haspopup={children ? "menu" : undefined} aria-expanded={children ? isOpen : undefined}
        onClick={() => setOpen(isOpen ? null : id)} data-testid={`nav-${id}`}
        className={`flex items-center gap-1 whitespace-nowrap rounded-ctl px-2.5 py-2 text-[16px] min-[1800px]:px-3 min-[1800px]:text-panel ${active || isOpen ? "bg-raised font-semibold text-ink" : "font-normal text-ink-2 hover:text-ink"}`}>
        {label}
        {children ? <span aria-hidden className="text-label">{isOpen ? "▴" : "▾"}</span> : null}
      </button>
      {isOpen && children ? (
        <div role="menu" className="absolute left-0 top-full z-[1100] mt-1 min-w-[15rem] rounded-ctl border border-line bg-surface py-1 shadow-xl">
          {children}
        </div>
      ) : null}
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { t, i18n } = useTranslation();
  const s = useSession();
  const { theme, toggle } = useTheme();
  const { users, current, signIn, signOut } = useDev();
  const [open, setOpen] = useState<string | null>(null);
  const loc = useLocation();
  const ref = useRef<HTMLElement>(null);
  useEffect(() => setOpen(null), [loc.pathname]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(null); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);
  const pages = FIELD_PAGES.filter((p) => p.visible(s));
  const hi = i18n.language === "hi";

  return (
    <div className="flex min-h-screen flex-col">
      {/* One row when everything fits; otherwise the nav and then the controls wrap onto further rows.
          Nothing shrinks below its content, so items never overlap or get pushed off-screen (e2e: header). */}
      <header ref={ref} data-testid="app-header"
        className="sticky top-0 z-[1050] flex min-h-[var(--em-header-h)] flex-wrap items-center gap-x-4 gap-y-1 border-b border-line bg-surface px-page py-2">
        <span className="whitespace-nowrap text-panel font-bold" data-header-item="brand">{t("shell.brand")}</span>
        <nav aria-label={t("shell.nav")} className="flex flex-wrap items-center gap-0.5">
          {CORE_GROUPS.map((g) => (
            <Menu key={g} id={g} label={t(`nav.core.${g}`)} open={open} setOpen={setOpen}>
              {WITH_MENU.has(g) ? (
                <p className="px-4 py-2 text-note text-muted">{t("shell.placeholder_menu")}</p>
              ) : undefined}
            </Menu>
          ))}
          <Menu id="field" label={t("nav.field.group")} open={open} setOpen={setOpen} active={loc.pathname.startsWith("/field")}>
            {pages.map((p) => (
              <NavLink key={p.path} to={p.path} role="menuitem" data-testid={`navlink-${p.path.split("/").pop()}`}
                className={({ isActive }) => `block px-4 py-2 text-panel ${isActive ? "bg-raised font-semibold text-ink" : "text-ink-2 hover:bg-raised hover:text-ink"}`}>
                {t(p.key)}
              </NavLink>
            ))}
          </Menu>
        </nav>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <select className="em-input max-w-[13rem]" data-header-item="constituency" aria-label={t("shell.constituency")} value={s.acNumber} onChange={() => undefined}>
            <option value={s.acNumber}>{s.acNumber} · {acLabel(s)}</option>
          </select>
          <button type="button" className="em-btn" onClick={() => setLang(hi ? "en" : "hi")} data-testid="lang-toggle" data-header-item="lang"
            lang={hi ? "en" : "hi"}>{hi ? "English" : "हिन्दी"}</button>
          <button type="button" className="em-btn" onClick={toggle} aria-label={t(theme === "dark" ? "shell.to_light" : "shell.to_dark")}
            data-testid="theme-toggle" data-header-item="theme">◐</button>
          <span className="rounded-chip bg-raised px-2 py-1 text-note text-ink" data-testid="role-chip" data-header-item="role">{t(`shell.role.${s.role}`)}</span>
          <button type="button" className="em-btn whitespace-nowrap" onClick={signOut} data-header-item="sign-out">{t("shell.sign_out")}</button>
        </div>
      </header>
      {/* DEV-ONLY strip: not part of the core's shell */}
      <div data-dev-only className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-dashed border-line bg-page px-page py-1.5 text-note text-muted">
        <span className="rounded border border-muted px-1.5 text-label">DEV</span>
        <label className="flex items-center gap-2" title={t("shell.view_as_hint")}>
          {t("shell.view_as")}
          <select className="em-input py-0.5" value={current?.user_id ?? ""} data-testid="view-as"
            onChange={(e) => void signIn(e.target.value)}>
            {users.map((u) => <option key={u.user_id} value={u.user_id}>{t(`persona.${u.persona}`)} · {u.name}</option>)}
          </select>
        </label>
        <span>{t("shell.view_as_hint")}</span>
      </div>
      <main className="flex-1">{children}</main>
      <footer className="px-page pb-8 pt-2 text-note text-muted">{t("shell.footer")}</footer>
    </div>
  );
}
