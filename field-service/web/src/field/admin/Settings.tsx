/** Settings: buckets and their keywords, custom labels awaiting promotion, and per-AC settings.
 * Keywords and per-AC settings are admin-only (keywords are shared by every AC). */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Async, ErrorState, Field, Page, Panel, PillTabs } from "@/components/Layout";
import { Chip, Dot } from "@/components/Values";
import { ApiError, useApi } from "@/lib/api";
import { dateTime, num } from "@/lib/format";
import { bucketColor } from "@/lib/palette";
import { acLabel, useSession } from "@/lib/session";
import { useBucketIndex, useNamer } from "../data";

type Tab = "buckets" | "labels" | "ac";

export function Settings() {
  const { t } = useTranslation();
  const s = useSession();
  const [tab, setTab] = useState<Tab>("buckets");
  const tabs: { id: Tab; label: string }[] = [{ id: "buckets", label: t("settings.tab_buckets") }, { id: "labels", label: t("settings.tab_labels") }];
  if (s.role === "admin") tabs.push({ id: "ac", label: t("settings.tab_ac") });
  return (
    <Page title={t("nav.field.settings")} qualifier={`${s.acNumber} · ${acLabel(s)}`}>
      <PillTabs label={t("nav.field.settings")} value={tab} onChange={setTab} items={tabs} />
      {tab === "buckets" ? <Buckets /> : tab === "labels" ? <Labels /> : <AcSettings />}
    </Page>
  );
}

function useRun() {
  const qc = useQueryClient();
  const [err, setErr] = useState<unknown>(null);
  const run = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); await qc.invalidateQueries(); return true; } catch (e) { setErr(e); return false; }
  };
  const view = err ? <div className="mb-3"><ErrorState error={err} />{err instanceof ApiError ? <p className="mt-1 text-note text-muted">{err.detail}</p> : null}</div> : null;
  return { run, view };
}

function Buckets() {
  const { t } = useTranslation();
  const api = useApi();
  const s = useSession();
  const name = useNamer();
  const buckets = useBucketIndex();
  const kw = useQuery({ queryKey: ["keywords", s.userId], queryFn: () => api.get<{ items: { keyword: string; script: string; bucket_id: number }[] }>("/keywords") });
  const { run, view } = useRun();
  const [adding, setAdding] = useState<Record<number, string>>({});
  const admin = s.role === "admin";
  return (
    <Panel title={t("settings.buckets")} subtitle={t("settings.buckets_sub")} footnote={admin ? t("settings.keywords_shared") : t("settings.keywords_admin_only")}>
      {view}
      <Async q={kw}>
        {(d) => (
          <ul className="flex flex-col" data-testid="bucket-list">
            {buckets.tops.map((b) => {
              const words = d.items.filter((k) => k.bucket_id === b.bucket_id);
              return (
                <li key={b.bucket_id} className="grid grid-cols-[minmax(12rem,16rem)_1fr] gap-4 border-t border-divider py-3">
                  <div>
                    <div className="flex items-center gap-2 font-semibold"><Dot color={bucketColor(b.code)} />{name(b)}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {b.sensitive ? <Chip tone="warn">{t("settings.sensitive")}</Chip> : null}
                      {b.ask_reach ? <Chip>{t("settings.asks_reach")}</Chip> : null}
                    </div>
                    {b.subheads.length ? <p className="mt-1 text-note text-muted">{b.subheads.map((sh) => name(sh)).join(" · ")}</p> : null}
                  </div>
                  <div>
                    <div className="flex flex-wrap gap-1">
                      {words.map((k) => (
                        <span key={k.keyword} className="inline-flex items-center gap-1 rounded-chip bg-raised px-2 py-0.5 text-label">
                          {k.keyword}
                          {admin ? <button type="button" aria-label={t("settings.remove_keyword", { k: k.keyword })} className="text-muted hover:text-bad"
                            onClick={() => run(() => api.del(`/buckets/${b.bucket_id}/keywords/${encodeURIComponent(k.keyword)}`))}>✕</button> : null}
                        </span>
                      ))}
                      {!words.length ? <span className="text-note text-muted">{t("settings.no_keywords")}</span> : null}
                    </div>
                    {admin ? (
                      <form className="mt-2 flex gap-2" onSubmit={async (e) => {
                        e.preventDefault();
                        if (await run(() => api.post(`/buckets/${b.bucket_id}/keywords`, { keyword: adding[b.bucket_id] ?? "" }))) setAdding((x) => ({ ...x, [b.bucket_id]: "" }));
                      }}>
                        <input className="em-input" placeholder={t("settings.add_keyword")} aria-label={`${t("settings.add_keyword")}: ${name(b)}`} maxLength={60}
                          value={adding[b.bucket_id] ?? ""} onChange={(e) => setAdding((x) => ({ ...x, [b.bucket_id]: e.target.value }))} />
                        <button type="submit" className="em-btn" disabled={!(adding[b.bucket_id] ?? "").trim()}>{t("settings.add")}</button>
                      </form>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Async>
    </Panel>
  );
}

function Labels() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const s = useSession();
  const name = useNamer();
  const buckets = useBucketIndex();
  const q = useQuery({ queryKey: ["labels", s.userId],
    queryFn: () => api.get<{ min_count: number; items: { label_norm: string; count: number; last_seen: string; review_listed_at: string | null }[] }>("/labels/pending") });
  const { run, view } = useRun();
  const [form, setForm] = useState<{ label: string; parent: string; name_hi: string; name_en: string } | null>(null);
  return (
    <Panel title={t("settings.labels")} subtitle={t("settings.labels_sub", { n: q.data?.min_count ?? 3 })}>
      {view}
      <Async q={q} empty={(d) => !d.items.length}>
        {(d) => (
          <ul className="flex flex-col" data-testid="label-list">
            {d.items.map((l) => (
              <li key={l.label_norm} className="border-t border-divider py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-semibold">{l.label_norm}</span>
                  <Chip>{t("settings.used_times", { n: num(l.count) })}</Chip>
                  <span className="text-note text-muted">{t("settings.last_used")} {dateTime(l.last_seen, i18n.language)}</span>
                  {l.review_listed_at ? <span className="text-note text-muted">· {t("settings.listed", { at: dateTime(l.review_listed_at, i18n.language) })}</span> : null}
                  <button type="button" className="em-btn ml-auto" onClick={() => setForm({ label: l.label_norm, parent: "", name_hi: l.label_norm, name_en: "" })}>{t("settings.promote")}</button>
                </div>
                {form?.label === l.label_norm ? (
                  <form className="mt-3 grid gap-3 md:grid-cols-4" data-testid="promote-form" onSubmit={async (e) => {
                    e.preventDefault();
                    if (await run(() => api.post("/labels/promote", { label_norm: form.label, parent_bucket_id: Number(form.parent), name_hi: form.name_hi, name_en: form.name_en }))) setForm(null);
                  }}>
                    <Field label={t("settings.parent_bucket")}>
                      <select className="em-input" required value={form.parent} onChange={(e) => setForm({ ...form, parent: e.target.value })}>
                        <option value="" />
                        {buckets.tops.filter((b) => b.code !== "OTHER").map((b) => <option key={b.bucket_id} value={b.bucket_id}>{name(b)}</option>)}
                      </select>
                    </Field>
                    <Field label={t("settings.name_hi")}><input className="em-input" required maxLength={60} value={form.name_hi} onChange={(e) => setForm({ ...form, name_hi: e.target.value })} /></Field>
                    <Field label={t("settings.name_en")}><input className="em-input" required maxLength={60} value={form.name_en} onChange={(e) => setForm({ ...form, name_en: e.target.value })} /></Field>
                    <div className="flex items-end gap-2"><button type="submit" className="em-btn-primary">{t("settings.promote")}</button><button type="button" className="em-btn" onClick={() => setForm(null)}>{t("common.cancel")}</button></div>
                    <p className="text-note text-muted md:col-span-4">{t("settings.promote_hint")}</p>
                  </form>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Async>
    </Panel>
  );
}

const AC_KEYS = ["large_entry", "pin_limit", "tail_threshold", "autofile_minutes", "ack_remind_hours", "ack_flag_hours",
  "followup_days", "chat_cleanup_hours", "media_retention_days", "void_retention_days"] as const;

function AcSettings() {
  const { t } = useTranslation();
  const api = useApi();
  const s = useSession();
  const q = useQuery({ queryKey: ["ac-settings", s.userId],
    queryFn: () => api.get<Record<string, { value: string | number | null; default: string | number | null; source: string }>>("/settings") });
  const { run, view } = useRun();
  const [edit, setEdit] = useState<Record<string, string>>({});
  return (
    <Panel title={t("settings.ac")} subtitle={t("settings.ac_sub")}>
      {view}
      <Async q={q}>
        {(d) => (
          <ul className="flex flex-col" data-testid="ac-settings">
            {AC_KEYS.filter((k) => k in d).map((k) => (
              <li key={k} className="grid grid-cols-[minmax(14rem,1fr)_10rem_auto] items-center gap-3 border-t border-divider py-2">
                <div>
                  <div className="text-body">{t(`settings.key.${k}`)}</div>
                  <div className="text-note text-muted">
                    {d[k].source === "override" ? <Chip tone="accent">{t("settings.overridden")}</Chip> : <Chip>{t("settings.default")}</Chip>}
                    {" "}{t("settings.default_is", { v: d[k].default ?? "—" })}
                  </div>
                </div>
                <input className="em-input num" inputMode="decimal" aria-label={t(`settings.key.${k}`)}
                  value={edit[k] ?? String(d[k].value ?? "")} onChange={(e) => setEdit((x) => ({ ...x, [k]: e.target.value }))} />
                <div className="flex gap-1">
                  <button type="button" className="em-btn" disabled={edit[k] === undefined}
                    onClick={async () => { if (await run(() => api.patch("/settings", { [k]: edit[k] }))) setEdit((x) => { const n = { ...x }; delete n[k]; return n; }); }}>{t("common.save")}</button>
                  {d[k].source === "override" ? <button type="button" className="em-btn" onClick={() => run(() => api.patch("/settings", { [k]: null }))}>{t("settings.reset")}</button> : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Async>
    </Panel>
  );
}
