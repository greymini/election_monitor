/** Team (admin): the member tree with areas, finance scope, coordinator flag, Telegram link status
 * (link token with QR, unlink), PIN unlock and reset, and last active. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Async, Drawer, ErrorState, Field, Page, Panel } from "@/components/Layout";
import { Chip, Missing } from "@/components/Values";
import { ApiError, useApi, useIdempotencyKey } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { acLabel, useSession } from "@/lib/session";
import { useAreaIndex, useNamer } from "../data";

interface Member {
  member_id: string; user_id: string; display_name: string; parent_member_id: string | null; is_principal: boolean;
  finance_scope: "none" | "own" | "team" | "all"; can_give_advance: boolean; is_coordinator: boolean; active: boolean;
  scopes: { block_id: string | null; area_id: string | null }[]; telegram_linked: boolean; telegram_linked_at: string | null;
  last_seen_at: string | null; pin_set: boolean; pin_locked_at: string | null;
}

/** Walk the tree from the roots: parents first, children indented under them. */
export function treeOrder<M extends { member_id: string; parent_member_id: string | null; display_name: string }>(ms: M[]) {
  const kids = new Map<string | null, M[]>();
  for (const m of ms) kids.set(m.parent_member_id, [...(kids.get(m.parent_member_id) ?? []), m]);
  const known = new Set(ms.map((m) => m.member_id));
  const out: { m: M; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const m of (kids.get(parent) ?? []).sort((a, b) => a.display_name.localeCompare(b.display_name, "hi"))) {
      out.push({ m, depth });
      walk(m.member_id, depth + 1);
    }
  };
  walk(null, 0);
  for (const m of ms) if (m.parent_member_id && !known.has(m.parent_member_id)) { out.push({ m, depth: 0 }); walk(m.member_id, 1); }
  return out;
}

export function Team() {
  const { t, i18n } = useTranslation();
  const s = useSession();
  const api = useApi();
  const name = useNamer();
  const places = useAreaIndex();
  const q = useQuery({ queryKey: ["members", s.userId], queryFn: () => api.get<Member[]>("/members") });
  const rows = useMemo(() => treeOrder(q.data ?? []), [q.data]);
  const [edit, setEdit] = useState<Member | null>(null);
  const [importing, setImporting] = useState(false);
  const place = (sc: Member["scopes"][number]) => sc.area_id ? name(places.area.get(sc.area_id), sc.area_id)
    : sc.block_id ? `${name(places.block.get(sc.block_id), sc.block_id)} (${t("team.whole_block")})` : t("team.whole_ac");
  return (
    <Page title={t("nav.field.team")} qualifier={`${s.acNumber} · ${acLabel(s)}`}
      actions={<button type="button" className="em-btn" onClick={() => setImporting(true)} data-testid="import-open">{t("team.import.open")}</button>}>
      <Panel subtitle={t("team.sub")}>
        <Async q={q} empty={(d) => !d.length}>
          {() => (
            <ul className="flex flex-col" data-testid="team-tree">
              {rows.map(({ m, depth }) => (
                <li key={m.member_id} className={`grid grid-cols-[minmax(14rem,1.2fr)_1fr_auto_auto] items-center gap-4 border-t border-divider py-3 ${m.active ? "" : "opacity-60"}`}>
                  <div style={{ paddingLeft: depth * 20 }} className="min-w-0">
                    <div className="truncate font-semibold">{depth ? <span aria-hidden className="text-muted">└ </span> : null}{m.display_name}</div>
                    <div className="flex flex-wrap gap-1 pt-1">
                      {m.is_principal ? <Chip tone="accent">{t("persona.principal")}</Chip> : null}
                      {m.is_coordinator ? <Chip tone="accent">{t("persona.coordinator")}</Chip> : null}
                      <Chip>{t("team.finance")}: {t(`scope.${m.finance_scope}`)}</Chip>
                      {m.can_give_advance ? <Chip>{t("team.gives_advance")}</Chip> : null}
                      {!m.active ? <Chip tone="bad">{t("team.inactive")}</Chip> : null}
                    </div>
                  </div>
                  <div className="text-note text-ink-2">{m.scopes.length ? m.scopes.map(place).join(", ") : <span className="text-muted">{t("team.no_area")}</span>}</div>
                  <div className="flex flex-col items-end gap-1 text-note">
                    {m.telegram_linked ? <Chip tone="ok" icon="✓">{t("team.linked")}</Chip> : <Chip tone="warn" icon="○">{t("team.not_linked")}</Chip>}
                    {m.pin_locked_at ? <Chip tone="bad" icon="🔒">{t("team.pin_locked")}</Chip> : m.pin_set ? <Chip>{t("team.pin_set")}</Chip> : null}
                    <span className="text-muted">{t("team.last_active")} {m.last_seen_at ? dateTime(m.last_seen_at, i18n.language) : <Missing reason={t("team.never_seen")} />}</span>
                  </div>
                  <button type="button" className="em-btn" onClick={() => setEdit(m)} data-testid="member-open">{t("team.manage")}</button>
                </li>
              ))}
            </ul>
          )}
        </Async>
      </Panel>
      <MemberDrawer m={edit} onClose={() => setEdit(null)} members={q.data ?? []} />
      {importing ? <ImportDrawer onClose={() => setImporting(false)} /> : null}
    </Page>
  );
}

function MemberDrawer({ m, onClose, members }: { m: Member | null; onClose: () => void; members: Member[] }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const [err, setErr] = useState<unknown>(null);
  const [link, setLink] = useState<{ deep_link: string; expires_at: string; qr: string } | null>(null);
  const [scope, setScope] = useState<Member["finance_scope"]>("own");
  const [coord, setCoord] = useState(false);
  const [advance, setAdvance] = useState(false);
  const [parent, setParent] = useState("");
  useEffect(() => {
    if (m) { setScope(m.finance_scope); setCoord(m.is_coordinator); setAdvance(m.can_give_advance); setParent(m.parent_member_id ?? ""); setLink(null); setErr(null); }
  }, [m]);
  async function run(fn: () => Promise<unknown>) {
    setErr(null);
    try { await fn(); await qc.invalidateQueries({ queryKey: ["members"] }); } catch (e) { setErr(e); }
  }
  if (!m) return null;
  return (
    <Drawer open onClose={onClose} title={m.display_name}>
      <div className="flex flex-col gap-5" data-testid="member-drawer">
        {err ? <div><ErrorState error={err} />{err instanceof ApiError ? <p className="mt-1 text-note text-muted">{err.detail}</p> : null}</div> : null}
        <section>
          <h3 className="mb-2 text-label uppercase text-muted">{t("team.telegram")}</h3>
          <p className="mb-2 text-body">{m.telegram_linked ? t("team.linked_since", { at: dateTime(m.telegram_linked_at, i18n.language) }) : t("team.not_linked_hint")}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="em-btn-primary" data-testid="make-link" onClick={() => run(async () => {
              const r = await api.post<{ deep_link: string; expires_at: string }>(`/members/${m.member_id}/link-token`);
              setLink({ ...r, qr: await QRCode.toDataURL(r.deep_link, { margin: 1, width: 220 }) });
            })}>{m.telegram_linked ? t("team.relink") : t("team.make_link")}</button>
            {m.telegram_linked ? <button type="button" className="em-btn" onClick={() => run(() => api.del(`/members/${m.member_id}/link`))}>{t("team.unlink")}</button> : null}
          </div>
          {link ? (
            <div className="mt-3 flex items-start gap-4" data-testid="link-qr">
              <img src={link.qr} alt={t("team.qr_alt")} className="rounded-ctl bg-white p-1" width={160} height={160} />
              <div className="min-w-0 text-note">
                <p className="mb-1 text-ink">{t("team.link_hint")}</p>
                <code className="block break-all rounded bg-raised px-2 py-1 font-mono text-label">{link.deep_link}</code>
                <p className="mt-1 text-muted">{t("team.link_expires", { at: dateTime(link.expires_at, i18n.language) })}</p>
              </div>
            </div>
          ) : null}
        </section>
        <section>
          <h3 className="mb-2 text-label uppercase text-muted">{t("team.pin")}</h3>
          <p className="mb-2 text-body">{m.pin_locked_at ? t("team.pin_locked_since", { at: dateTime(m.pin_locked_at, i18n.language) }) : m.pin_set ? t("team.pin_set") : t("team.pin_not_set")}</p>
          <div className="flex gap-2">
            {m.pin_locked_at ? <button type="button" className="em-btn" onClick={() => run(() => api.post(`/members/${m.member_id}/pin/unlock`))}>{t("team.pin_unlock")}</button> : null}
            {m.pin_set ? <button type="button" className="em-btn" onClick={() => run(() => api.post(`/members/${m.member_id}/pin/reset`))}>{t("team.pin_reset")}</button> : null}
          </div>
        </section>
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void run(() => api.patch(`/members/${m.member_id}`, {
          finance_scope: scope, is_coordinator: coord, can_give_advance: advance,
          ...(m.is_principal ? {} : { parent_member_id: parent || null }) })); }}>
          <h3 className="text-label uppercase text-muted">{t("team.role")}</h3>
          {!m.is_principal ? (
            <Field label={t("team.reports_to")}>
              <select className="em-input" value={parent} onChange={(e) => setParent(e.target.value)}>
                {members.filter((x) => x.member_id !== m.member_id && x.active).map((x) => <option key={x.member_id} value={x.member_id}>{x.display_name}</option>)}
              </select>
            </Field>
          ) : null}
          <Field label={t("team.finance")}>
            <select className="em-input" value={scope} onChange={(e) => setScope(e.target.value as Member["finance_scope"])}>
              {(["none", "own", "team", "all"] as const).map((x) => <option key={x} value={x}>{t(`scope.${x}`)} · {t(`team.scope_hint.${x}`)}</option>)}
            </select>
          </Field>
          <label className="flex items-center gap-2 text-body"><input type="checkbox" checked={coord} onChange={(e) => setCoord(e.target.checked)} />{t("team.coordinator")}</label>
          <label className="flex items-center gap-2 text-body"><input type="checkbox" checked={advance} onChange={(e) => setAdvance(e.target.checked)} />{t("team.gives_advance")}</label>
          <div><button type="submit" className="em-btn-primary">{t("common.save")}</button></div>
        </form>
      </div>
    </Drawer>
  );
}

const IMPORT_COLUMNS = "user_id,display_name,parent_user_id,finance_scope,can_give_advance,is_coordinator,areas,blocks";
// Placeholders, not real or demo ids: replace each <...> with the core user, block and area ids.
const IMPORT_EXAMPLE = "<incharge-user-id>,<In-charge name>,<candidate-user-id>,team,yes,no,,<block-id>\n"
  + "<worker-user-id>,<Worker name>,<incharge-user-id>,own,no,no,<area-id>;<area-id>,\n";

interface ImportRow { row: number; user_id: string; display_name: string; ok: boolean; error: string | null }
interface ImportResult { ok: boolean; imported: boolean; total: number; errors: number; rows: ImportRow[] }

/** Team import from CSV: check first (nothing is kept), then import. All or nothing on the server. */
function ImportDrawer({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const api = useApi();
  const idem = useIdempotencyKey();
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [checked, setChecked] = useState<{ text: string; result: ImportResult } | null>(null);
  const [done, setDone] = useState<number | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function send(dryRun: boolean) {
    setBusy(true); setErr(null);
    try {
      const result = dryRun ? await api.post<ImportResult>("/members/import?dry_run=true", { csv: text })
        : await api.create<ImportResult>("/members/import?dry_run=false", { csv: text }, idem.keyFor(text));
      setChecked({ text, result });
      if (result.imported) { setDone(result.total); await qc.invalidateQueries({ queryKey: ["members"] }); }
    } catch (e) { setErr(e); setChecked(null); } finally { setBusy(false); }
  }
  function template() {
    const url = URL.createObjectURL(new Blob([`${IMPORT_COLUMNS}\n${IMPORT_EXAMPLE}`], { type: "text/csv" }));
    Object.assign(document.createElement("a"), { href: url, download: "team-import.csv" }).click();
    URL.revokeObjectURL(url);
  }
  const ready = checked && checked.text === text && checked.result.ok && !checked.result.imported;
  return (
    <Drawer open wide onClose={onClose} title={t("team.import.title")}>
      <div className="flex flex-col gap-4" data-testid="import-drawer">
        <p className="text-body">{t("team.import.intro")}</p>
        <div>
          <div className="mb-1 text-label uppercase text-muted">{t("team.import.columns")}</div>
          <code className="block break-all rounded bg-raised px-2 py-1 font-mono text-label" lang="en">{IMPORT_COLUMNS}</code>
          <ul className="mt-2 list-disc pl-5 text-note text-ink-2">
            <li>{t("team.import.rule_parent")}</li>
            <li>{t("team.import.rule_scope")}</li>
            <li>{t("team.import.rule_flags")}</li>
            <li>{t("team.import.rule_areas")}</li>
          </ul>
          <button type="button" className="em-link mt-2 text-note" onClick={template}>{t("team.import.template")}</button>
        </div>
        <Field label={t("team.import.file")}>
          <input type="file" accept=".csv,text/csv" data-testid="import-file" className="text-note"
            onChange={async (e) => { const f = e.target.files?.[0]; if (f) setText(await f.text()); }} />
        </Field>
        <Field label={t("team.import.or_paste")}>
          <textarea className="em-input min-h-[8rem] font-mono text-label" lang="en" value={text} data-testid="import-text"
            onChange={(e) => setText(e.target.value)} spellCheck={false} />
        </Field>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="em-btn" disabled={!text.trim() || busy} onClick={() => void send(true)} data-testid="import-check">{t("team.import.check")}</button>
          <button type="button" className="em-btn-primary" disabled={!ready || busy} onClick={() => void send(false)} data-testid="import-run">{t("team.import.run")}</button>
        </div>
        {err ? <div><ErrorState error={err} />{err instanceof ApiError ? <p className="mt-1 text-note text-muted" lang="en">{err.detail}</p> : null}</div> : null}
        {done !== null ? <p className="text-body text-ok" data-testid="import-done">✓ {t("team.import.done", { n: done })}</p> : null}
        {checked && done === null ? (
          <div data-testid="import-result">
            <p className={`mb-2 text-body ${checked.result.ok ? "text-ok" : "text-bad"}`}>
              {checked.result.ok ? `✓ ${t("team.import.all_ok", { n: checked.result.total })}`
                : `✕ ${t("team.import.has_errors", { bad: checked.result.errors, n: checked.result.total })}`}
            </p>
            <table className="w-full text-note">
              <thead><tr className="text-left text-muted"><th className="py-1 pr-3">{t("team.import.line")}</th><th className="pr-3">{t("team.import.member")}</th><th>{t("team.import.result")}</th></tr></thead>
              <tbody>
                {checked.result.rows.map((r) => (
                  <tr key={r.row} className="border-t border-divider" data-ok={r.ok}>
                    <td className="num py-1 pr-3">{r.row}</td>
                    <td className="pr-3">{r.display_name || r.user_id}</td>
                    <td>{r.ok ? <span className="text-ok">✓ {t("team.import.row_ok")}</span> : <span className="text-bad">✕ <span lang="en">{r.error}</span></span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </Drawer>
  );
}
