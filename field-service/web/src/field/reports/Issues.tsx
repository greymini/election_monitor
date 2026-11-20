/** Issues: the list with status and counts; a detail drawer with timeline, reports and linked spend,
 * and rename / close for those who may manage issues. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Async, Drawer, ErrorState, Field, Loading, Page, Panel } from "@/components/Layout";
import { Missing, StatusChip } from "@/components/Values";
import { DataTable } from "@/components/DataTable";
import { useApi } from "@/lib/api";
import { dateTime, inr, num } from "@/lib/format";
import { acLabel, useSession } from "@/lib/session";
import { useAreaIndex, useNamer } from "../data";
import { useCategories, useIssues, type Issue } from "./data";
import { ReportDrawer } from "./ReportDrawer";

interface IssueDetail {
  issue: Issue;
  reports: { report_id: string; report_no: string; title: string | null; status: string; filed_at: string; urgency: string }[];
  timeline: { at: string; kind: string; data: Record<string, unknown> | null; actor_name?: string | null; note?: string | null }[];
  linked_spend?: { total: string; entries: { txn_id: string; txn_no: string; amount: string; txn_date: string }[] };
}

export function Issues() {
  const { t, i18n } = useTranslation();
  const s = useSession();
  const name = useNamer();
  const places = useAreaIndex();
  const cats = useCategories();
  const q = useIssues();
  const [sp, setSp] = useSearchParams();
  const open = sp.get("open");
  const setOpen = (id: string | null) => setSp((c) => { const n = new URLSearchParams(c); if (id) n.set("open", id); else n.delete("open"); return n; }, { replace: true });
  return (
    <Page title={t("nav.field.issues")} qualifier={`${s.acNumber} · ${acLabel(s)}`}>
      <Panel subtitle={t("issues.sub")}>
        <Async q={q} empty={(d) => !d.items.length}>
          {(d) => (
            <DataTable rowKey={(i) => i.issue_id} rows={d.items} onRowClick={(i) => setOpen(i.issue_id)} testId="issues-table"
              initialSort={[{ key: "status", dir: -1 }, { key: "opened", dir: -1 }]}
              columns={[
                { key: "no", header: t("issues.no"), render: (i) => i.issue_no, filter: (i) => i.issue_no, sort: (i) => i.issue_no },
                { key: "title", header: t("issues.title"), render: (i) => i.title, filter: (i) => i.title, sort: (i) => i.title },
                { key: "area", header: t("filter.area"), render: (i) => name(places.area.get(i.area_id), i.area_id), filter: (i) => name(places.area.get(i.area_id), i.area_id) },
                { key: "cat", header: t("reports.category"), render: (i) => i.category_id === null ? <Missing /> : name(cats.get(i.category_id), "") },
                { key: "status", header: t("ledger.status"), render: (i) => <StatusChip status={i.status} kind="issue" />, sort: (i) => (i.status === "open" ? 1 : 0) },
                { key: "reports", header: t("issues.reports"), align: "right", render: (i) => num(i.report_count), sort: (i) => i.report_count },
                { key: "opened", header: t("issues.opened"), render: (i) => dateTime(i.created_at, i18n.language), sort: (i) => i.created_at },
              ]} />
          )}
        </Async>
      </Panel>
      <IssueDrawer issueId={open} onClose={() => setOpen(null)} />
    </Page>
  );
}

function IssueDrawer({ issueId, onClose }: { issueId: string | null; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const s = useSession();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["issue", s.userId, issueId], enabled: !!issueId, queryFn: () => api.get<IssueDetail>(`/issues/${issueId}`) });
  const [report, setReport] = useState<string | null>(null);
  const [mode, setMode] = useState<"" | "rename" | "close">("");
  const [text, setText] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const manage = s.isPrincipal || s.isCoordinator;
  async function act(fn: () => Promise<unknown>) {
    setErr(null);
    try { await fn(); setMode(""); setText(""); await qc.invalidateQueries(); } catch (e) { setErr(e); }
  }
  const d = q.data;
  return (
    <>
      <Drawer open={!!issueId && !report} onClose={onClose} wide title={d ? `${d.issue.issue_no} · ${d.issue.title}` : t("nav.field.issues")}>
        {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : d ? (
          <div className="flex flex-col gap-5" data-testid="issue-drawer">
            <div className="flex items-center gap-2"><StatusChip status={d.issue.status} kind="issue" />
              {d.issue.close_note ? <span className="text-note text-muted">{d.issue.close_note}</span> : null}</div>
            <section>
              <h3 className="mb-2 text-label uppercase text-muted">{t("issues.reports")}</h3>
              <ul className="flex flex-col gap-1">
                {d.reports.map((r) => (
                  <li key={r.report_id}>
                    <button type="button" className="flex w-full items-center gap-2 rounded px-1 py-1 text-left hover:bg-raised" onClick={() => setReport(r.report_id)}>
                      <span className="text-note text-muted">{r.report_no}</span><span className="flex-1 truncate">{r.title ?? ""}</span><StatusChip status={r.status} kind="report" />
                    </button>
                  </li>
                ))}
              </ul>
            </section>
            {d.linked_spend ? (
              <section>
                <h3 className="mb-2 text-label uppercase text-muted">{t("reports.linked_spend")}</h3>
                {d.linked_spend.entries.length ? (
                  <ul className="text-body">
                    {d.linked_spend.entries.map((e) => <li key={e.txn_id} className="flex justify-between border-b border-divider py-1"><span>{e.txn_no}</span><span className="num">{inr(e.amount)}</span></li>)}
                    <li className="flex justify-between py-1 font-semibold"><span>{t("spend.total")}</span><span className="num">{inr(d.linked_spend.total)}</span></li>
                  </ul>
                ) : <Missing reason={t("reports.no_spend")} />}
              </section>
            ) : null}
            <section>
              <h3 className="mb-2 text-label uppercase text-muted">{t("reports.timeline")}</h3>
              <ol className="flex flex-col gap-1 border-l border-line pl-4 text-note">
                {d.timeline.map((e, i) => <li key={i}><span className="text-muted">{dateTime(e.at, i18n.language)}</span> · {t(`event.${e.kind}`, { defaultValue: e.kind })}{e.actor_name ? ` · ${e.actor_name}` : ""}{e.note ? ` · ${e.note}` : ""}</li>)}
              </ol>
            </section>
            {manage && d.issue.status === "open" ? (
              <section>
                {err ? <ErrorState error={err} /> : null}
                {mode === "" ? (
                  <div className="flex gap-2">
                    <button type="button" className="em-btn" onClick={() => { setText(d.issue.title); setMode("rename"); }}>{t("issues.rename")}</button>
                    <button type="button" className="em-btn" onClick={() => setMode("close")}>{t("issues.close")}</button>
                  </div>
                ) : (
                  <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void act(() => mode === "rename"
                    ? api.patch(`/issues/${issueId}`, { title: text })
                    : api.post(`/issues/${issueId}/close`, { note: text || null })); }}>
                    <Field label={mode === "rename" ? t("issues.title_label") : t("issues.close_note")}>
                      <input className="em-input" value={text} onChange={(e) => setText(e.target.value)} required={mode === "rename"} maxLength={mode === "rename" ? 120 : 1000} />
                    </Field>
                    <div className="flex gap-2"><button type="submit" className="em-btn-primary">{t("common.save")}</button><button type="button" className="em-btn" onClick={() => setMode("")}>{t("common.cancel")}</button></div>
                  </form>
                )}
              </section>
            ) : null}
          </div>
        ) : null}
      </Drawer>
      <ReportDrawer reportId={report} onClose={() => setReport(null)} />
    </>
  );
}

