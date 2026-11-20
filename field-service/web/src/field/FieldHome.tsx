/** Field (मैदान) landing: what this member can open, as cards. */
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Page } from "@/components/Layout";
import { useSession, acLabel } from "@/lib/session";
import { FIELD_PAGES } from "./nav";

export function FieldHome() {
  const { t } = useTranslation();
  const s = useSession();
  const pages = FIELD_PAGES.filter((p) => p.visible(s));
  return (
    <Page title={t("nav.field.group")} qualifier={`${s.acNumber} · ${acLabel(s)}`}>
      <div className="grid gap-gap [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]">
        {pages.map((p) => (
          <Link key={p.path} to={p.path} className="em-card block p-card hover:bg-raised">
            <div className="text-label uppercase text-muted">{t(`nav.field.section_${p.section}`)}</div>
            <div className="mt-1 text-panel">{t(p.key)}</div>
            <div className="mt-1 text-note text-muted">{t(`${p.key}_hint`)}</div>
          </Link>
        ))}
      </div>
    </Page>
  );
}
