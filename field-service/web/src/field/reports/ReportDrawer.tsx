/** Report detail drawer: gallery, audio, transcript (labelled machine-written, with a correct-it
 * action), timeline, duplicate suggestions, linked issue and spend, and the actions the viewer may
 * take (the API sends `can`; it also re-checks every action). */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Drawer, ErrorState, Field, Loading } from "@/components/Layout";
import { Chip, Dot, MachineLabel, Missing, StatusChip } from "@/components/Values";
import { ApiError, useApi, useIdempotencyKey } from "@/lib/api";
import { dateTime, inr } from "@/lib/format";
import { categoryColor, OTHER } from "@/lib/palette";
import { useSession } from "@/lib/session";
import { useAreaIndex, useNamer } from "../data";
import { useCategories, useIssues, type ReportDetail } from "./data";

const CLOSED = ["resolved", "closed_no_action", "duplicate"];

export function ReportDrawer({ reportId, onClose }: { reportId: string | null; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const idem = useIdempotencyKey();
  const s = useSession();
  const qc = useQueryClient();
  const name = useNamer();
  const places = useAreaIndex();
  const cats = useCategories();
  const q = useQuery({ queryKey: ["report", s.userId, reportId], enabled: !!reportId,
    queryFn: () => api.get<ReportDetail>(`/reports/${reportId}`) });
  const d = q.data;
  const assignable = useQuery({ queryKey: ["assignable", s.userId, reportId], enabled: !!reportId && !!d?.can.triage,
    queryFn: () => api.get<{ members: { member_id: string; display_name: string; covers: boolean }[] }>(`/reports/${reportId}/assignable`) });
  const issues = useIssues();
  const [err, setErr] = useState<unknown>(null);
  const [panel, setPanel] = useState<"" | "assign" | "close" | "transcript" | "title" | "issue">("");
  const [text, setText] = useState("");
  const [choice, setChoice] = useState("");
  const [closeAs, setCloseAs] = useState<"resolved" | "closed_no_action">("resolved");

  async function act(path: string, body: unknown = {}) {
    setErr(null);
    try {
      if (path === "/issues") { await api.create(path, body, idem.keyFor(body)); idem.done(); }   // creates: keyed
      else await api.post(path, body);
      setPanel(""); setText(""); setChoice("");
      await qc.invalidateQueries();
    } catch (e) { setErr(e); }
  }
  const r = d?.report;
  const base = `/reports/${reportId}`;
  const sameAreaIssues = (issues.data?.items ?? []).filter((i) => i.status === "open" && r && i.area_id === r.area_id);

  return (
    <Drawer open={!!reportId} onClose={() => { setPanel(""); setErr(null); onClose(); }} wide
      title={r ? `${r.report_no}` : t("nav.field.reports")}>
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : d && r ? (
        <div className="flex flex-col gap-5" data-testid="report-drawer">
          <div className="flex flex-wrap items-center gap-2">
            {r.urgency === "urgent" ? <Chip tone="bad" icon="●">{t("reports.urgent")}</Chip> : null}
            <StatusChip status={r.status} kind="report" />
            {r.restricted ? <Chip tone="serious" icon="🔒">{t("reports.restricted")}</Chip> : null}
            <span className="inline-flex items-center gap-1.5 text-body">
              <Dot color={r.category_id === null ? OTHER : categoryColor(r.category_code)} />
              {r.category_id === null ? <span className="text-warn">{t("reports.unsorted")}</span> : name(cats.get(r.category_id), r.category_name_hi ?? "")}
            </span>
          </div>
          <div>
            <h3 className="text-panel">{r.title ?? <span className="text-muted">{t("reports.no_title")}</span>}
              {r.title_source === "auto" ? <span className="ml-2 align-middle"><MachineLabel what={t("reports.auto_title")} /></span> : null}</h3>
            <p className="text-note text-muted">
              {r.area_id ? name(places.area.get(r.area_id), r.area_id) : t("spend.no_area")} · {t("reports.reporter")} {r.reporter_name}
              {" · "}{t("reports.filed")} {dateTime(r.filed_at, i18n.language)}
            </p>
          </div>

          {d.media.some((m) => m.kind !== "voice") ? (
            <Section title={t("reports.photos")}>
              <div className="grid grid-cols-3 gap-2" data-testid="gallery">
                {d.media.filter((m) => m.kind !== "voice").map((m) => m.url ? (
                  <a key={m.asset_id} href={m.url} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-ctl border border-line">
                    <img src={m.url} alt={m.role === "closing" ? t("reports.closing_photo") : t("reports.photo")} className="aspect-[4/3] w-full object-cover" />
                    {m.role === "closing" ? <span className="block px-2 py-1 text-label text-muted">{t("reports.closing_photo")}</span> : null}
                  </a>
                ) : <div key={m.asset_id} className="flex aspect-[4/3] items-center justify-center rounded-ctl bg-raised text-label text-muted">{t(`reports.media_${m.status === "failed" ? "failed" : "pending"}`)}</div>)}
              </div>
            </Section>
          ) : null}
          {d.media.some((m) => m.kind === "voice") ? (
            <Section title={t("reports.voice")}>
              {d.media.filter((m) => m.kind === "voice").map((m) => m.url
                ? <audio key={m.asset_id} controls preload="none" src={m.url} className="w-full" data-testid="audio" />
                : <p key={m.asset_id} className="text-note text-muted">{t(`reports.media_${m.status === "failed" ? "failed" : "pending"}`)}</p>)}
            </Section>
          ) : null}

          <Section title={t("reports.transcript")}>
            {d.transcript.text ? (
              <div data-testid="transcript">
                <p className="mb-1 flex flex-wrap items-center gap-2">
                  {d.transcript.source === "machine" ? <MachineLabel what={t("reports.machine_written")} /> : <Chip tone="ok" icon="✓">{t("reports.corrected")}</Chip>}
                  {d.transcript.source === "machine" && d.transcript.confidence ? <span className="text-label text-muted">{t("reports.confidence", { n: Math.round(Number(d.transcript.confidence) * 100) })}</span> : null}
                </p>
                <p className="whitespace-pre-wrap text-body">{d.transcript.text}</p>
              </div>
            ) : <p className="text-note text-muted">{t(`reports.transcript_${d.transcript.status}`)}</p>}
            {d.can.correct && d.transcript.status !== "none" ? (
              panel === "transcript" ? (
                <form className="mt-2 flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void act(`${base}/transcript`, { text }); }}>
                  <textarea className="em-input" rows={4} required value={text} onChange={(e) => setText(e.target.value)} aria-label={t("reports.correct")} />
                  <div className="flex gap-2"><button type="submit" className="em-btn-primary">{t("common.save")}</button><button type="button" className="em-btn" onClick={() => setPanel("")}>{t("common.cancel")}</button></div>
                </form>
              ) : <button type="button" className="em-btn mt-2" onClick={() => { setText(d.transcript.text ?? ""); setPanel("transcript"); }}>{t("reports.correct")}</button>
            ) : null}
          </Section>

          {r.note_text ? <Section title={t("reports.note")}><p className="whitespace-pre-wrap text-body">{r.note_text}</p></Section> : null}

          <Section title={t("reports.links")}>
            <dl className="grid grid-cols-[9rem_1fr] gap-x-4 gap-y-2 text-body">
              <dt className="text-note text-muted">{t("reports.issue")}</dt>
              <dd>{d.issue ? <><Link className="em-link" to={`/field/issues?open=${d.issue.issue_id}`}>{d.issue.issue_no}</Link> {d.issue.title} <StatusChip status={d.issue.status} kind="issue" /></> : <Missing reason={t("reports.no_issue")} />}</dd>
              {d.linked_spend ? (
                <>
                  <dt className="text-note text-muted">{t("reports.linked_spend")}</dt>
                  <dd data-testid="linked-spend">{d.linked_spend.entries.length ? `${inr(d.linked_spend.total)} · ${t("reports.spend_entries", { n: d.linked_spend.entries.length })}` : <Missing reason={t("reports.no_spend")} />}</dd>
                </>
              ) : null}
            </dl>
          </Section>

          {d.duplicate_suggestions.length ? (
            <Section title={t("reports.duplicates")}>
              <ul className="flex flex-col gap-1 text-body">
                {d.duplicate_suggestions.map((x) => (
                  <li key={x.report_id} className="flex flex-wrap items-center gap-2">
                    <span>{x.report_no}</span><StatusChip status={x.status} kind="report" />
                    <span className="text-note text-muted">{t(`reports.dup_reason.${x.reason}`, { defaultValue: x.reason })}</span>
                    {d.can.triage && !CLOSED.includes(r.status) ? (
                      <button type="button" className="em-btn" onClick={() => act(`${base}/merge`, { into_report_id: x.report_id })}>{t("reports.merge_into", { no: x.report_no })}</button>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          <Section title={t("entry.actions")}>
            {err ? <div className="mb-2"><ErrorState error={err} />{err instanceof ApiError ? <p className="mt-1 text-note text-muted">{err.detail}</p> : null}</div> : null}
            <div className="flex flex-wrap gap-2" data-testid="report-actions">
              {d.can.triage && r.status === "new" ? <button type="button" className="em-btn-primary" onClick={() => act(`${base}/ack`)} data-testid="act-ack">{t("reports.act.ack")}</button> : null}
              {d.can.triage && !CLOSED.includes(r.status) ? <button type="button" className="em-btn" onClick={() => setPanel("assign")} data-testid="act-assign">{t("reports.act.assign")}</button> : null}
              {d.can.work && r.status === "assigned" ? <button type="button" className="em-btn" onClick={() => act(`${base}/status`, { status: "in_progress" })}>{t("reports.act.start")}</button> : null}
              {(d.can.work || d.can.triage) && !CLOSED.includes(r.status) && r.status !== "new" ? <button type="button" className="em-btn" onClick={() => setPanel("close")} data-testid="act-close">{t("reports.act.close")}</button> : null}
              {d.can.reopen && CLOSED.includes(r.status) ? <button type="button" className="em-btn" onClick={() => act(`${base}/status`, { status: "acknowledged" })}>{t("reports.act.reopen")}</button> : null}
              {(d.can.triage || d.can.work) ? <button type="button" className="em-btn" onClick={() => { setText(r.title ?? ""); setPanel("title"); }}>{t("reports.act.title")}</button> : null}
              {d.can.manage_issues && !d.issue && r.area_id ? <button type="button" className="em-btn" onClick={() => setPanel("issue")}>{t("reports.act.link_issue")}</button> : null}
              {d.can.triage ? (
                <button type="button" className="em-btn" onClick={() => act(`${base}/urgency`, { urgency: r.urgency === "urgent" ? "normal" : "urgent" })}>
                  {r.urgency === "urgent" ? t("reports.act.not_urgent") : t("reports.act.urgent")}</button>
              ) : null}
            </div>
            {panel === "assign" ? (
              <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); void act(`${base}/assign`, { member_id: choice }); }}>
                <Field label={t("reports.assign_to")}>
                  <select className="em-input" required value={choice} onChange={(e) => setChoice(e.target.value)} data-testid="assign-select">
                    <option value="" />
                    {(assignable.data?.members ?? []).map((m) => <option key={m.member_id} value={m.member_id}>{m.display_name}{m.covers ? ` · ${t("reports.covers_area")}` : ""}</option>)}
                  </select>
                </Field>
                <button type="submit" className="em-btn-primary" data-testid="assign-submit">{t("reports.act.assign")}</button>
                <button type="button" className="em-btn" onClick={() => setPanel("")}>{t("common.cancel")}</button>
              </form>
            ) : panel === "close" ? (
              <form className="mt-3 flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void act(`${base}/status`, { status: closeAs, note: text || null }); }}>
                <div className="flex gap-2">
                  {(["resolved", "closed_no_action"] as const).map((x) => (
                    <label key={x} className="flex items-center gap-1.5 text-body"><input type="radio" name="closeas" checked={closeAs === x} onChange={() => setCloseAs(x)} />{t(`report_status.${x}`)}</label>
                  ))}
                </div>
                <Field label={t("reports.close_note")}><textarea className="em-input" rows={2} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} data-testid="close-note" /></Field>
                <div className="flex gap-2"><button type="submit" className="em-btn-primary" data-testid="close-submit">{t("reports.act.close")}</button><button type="button" className="em-btn" onClick={() => setPanel("")}>{t("common.cancel")}</button></div>
              </form>
            ) : panel === "title" ? (
              <form className="mt-3 flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); void act(`${base}/title`, { title: text.trim() || null }); }}>
                <Field label={t("reports.title_label")}><input className="em-input" maxLength={80} value={text} onChange={(e) => setText(e.target.value)} /></Field>
                <p className="text-note text-muted">{t("reports.title_hint")}</p>
                <div className="flex gap-2"><button type="submit" className="em-btn-primary">{t("common.save")}</button><button type="button" className="em-btn" onClick={() => setPanel("")}>{t("common.cancel")}</button></div>
              </form>
            ) : panel === "issue" ? (
              <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(e) => {
                e.preventDefault();
                void (choice === "new" ? act("/issues", { report_id: r.report_id, title: text.trim() || null }) : act(`${base}/link-issue`, { issue_id: choice }));
              }}>
                <Field label={t("reports.issue")}>
                  <select className="em-input" required value={choice} onChange={(e) => setChoice(e.target.value)}>
                    <option value="" />
                    {sameAreaIssues.map((i) => <option key={i.issue_id} value={i.issue_id}>{i.issue_no} · {i.title}</option>)}
                    <option value="new">{t("reports.new_issue")}</option>
                  </select>
                </Field>
                {choice === "new" ? <Field label={t("issues.title_label")}><input className="em-input" maxLength={120} value={text} onChange={(e) => setText(e.target.value)} placeholder={t("issues.title_default")} /></Field> : null}
                <button type="submit" className="em-btn-primary">{t("common.save")}</button>
                <button type="button" className="em-btn" onClick={() => setPanel("")}>{t("common.cancel")}</button>
              </form>
            ) : null}
          </Section>

          <Section title={t("reports.timeline")}>
            <ol className="flex flex-col gap-1 border-l border-line pl-4 text-note" data-testid="timeline">
              {d.events.map((e, i) => (
                <li key={i}><span className="text-muted">{dateTime(e.at, i18n.language)}</span> · {t(`event.${e.kind}`, { defaultValue: e.kind })}{e.kind === "status_changed" && typeof e.data?.to === "string" ? `: ${t(`report_status.${e.data.to}`)}` : ""}
                  {e.actor_name ? ` · ${e.actor_name}` : ""}{e.note ? ` · ${e.note}` : ""}</li>
              ))}
            </ol>
          </Section>
        </div>
      ) : null}
    </Drawer>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h3 className="mb-2 text-label uppercase text-muted">{title}</h3>{children}</section>;
}
