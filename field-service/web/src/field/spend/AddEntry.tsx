/** Add entry: the web form equivalent to the bot for expense, advance and return. Before saving it
 * asks the API for a preview and shows the same warnings as the bot (sensitive bucket in the election
 * window, cash over the window's limit, large amount): one extra confirm, never a block. */
import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ErrorState, Field, Page, Panel, PillTabs } from "@/components/Layout";
import { Chip } from "@/components/Values";
import { ApiError, useApi, useIdempotencyKey } from "@/lib/api";
import { inr, todayIst } from "@/lib/format";
import { atLeast, useSession, acLabel } from "@/lib/session";
import { useAreaIndex, useBalances, useBucketIndex, useNamer } from "../data";

type Kind = "expense" | "advance" | "return";
const PAY = ["cash", "upi", "bank_transfer", "cheque", "credit", "in_kind"];
const FUND = ["own", "family_business", "party", "supporter_cash", "supporter_in_kind", "advance", "own_pocket_reimbursable", "other"];
const PAYEE = ["vendor", "individual", "institution", "group"];
const REACH = ["person", "family", "village", "event"];

interface Preview { flags: string[]; warnings: string[]; in_election_window: boolean }

export function AddEntry() {
  const { t } = useTranslation();
  const s = useSession();
  const api = useApi();
  const idem = useIdempotencyKey();
  const qc = useQueryClient();
  const name = useNamer();
  const buckets = useBucketIndex();
  const places = useAreaIndex();
  const members = useBalances(atLeast(s, "team"));
  const kinds: Kind[] = atLeast(s, "team") ? ["expense", "advance", "return"] : ["expense", "return"];
  const [kind, setKind] = useState<Kind>("expense");
  const blank = { amount: "", top: "", sub: "", custom_label: "", txn_date: todayIst(), area: "", pay_mode: "cash",
    fund_source: "", payee_kind: "", payee_label: "", reach_count: "", reach_unit: "person", remark: "", to: "", payer: "" };
  const [v, setV] = useState(blank);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState<{ txn_no: string; amount: string; receiver_message: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof blank) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    setV((cur) => ({ ...cur, [k]: e.target.value, ...(k === "top" ? { sub: "" } : {}) }));
    setPreview(null);
  };
  const top = buckets.byId.get(Number(v.top));
  const others = useMemo(() => (members.data?.members ?? []).filter((m) => m.member_id !== s.memberId), [members.data, s.memberId]);

  function body() {
    const area = places.area.get(v.area);
    const common = { kind, amount: v.amount, txn_date: v.txn_date || null, pay_mode: v.pay_mode || null, remark: v.remark || null };
    if (kind === "expense") {
      return { ...common, bucket_id: Number(v.sub || v.top), custom_label: v.custom_label || null, area_id: v.area || null,
        block_id: area?.block_id ?? null, fund_source: v.fund_source || null, payee_kind: v.payee_kind || null,
        payee_label: v.payee_label || null, reach_count: v.reach_count ? Number(v.reach_count) : null,
        reach_unit: v.reach_count ? v.reach_unit : null, from_member_id: v.payer || null };
    }
    if (kind === "advance") return { ...common, to_member_id: v.to };
    return { kind, amount: v.amount, pay_mode: v.pay_mode || null, remark: v.remark || null, to_member_id: v.to || null };
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      if (!preview) {
        const p = await api.post<Preview>("/txns/preview", body());
        setPreview(p);
        if (p.warnings.length) return;          // show the warnings; the next press saves
      }
      const b = body();
      const r = await api.create<{ txn: { txn_no: string; amount: string }; receiver_message: string | null }>("/txns", b, idem.keyFor(b));
      idem.done();
      setSaved({ ...r.txn, receiver_message: r.receiver_message });
      setV({ ...blank, txn_date: v.txn_date, pay_mode: v.pay_mode });
      setPreview(null);
      await qc.invalidateQueries();
    } catch (x) {
      setErr(x); setPreview(null);
    } finally { setBusy(false); }
  }

  return (
    <Page title={t("nav.field.entry")} qualifier={`${s.acNumber} · ${acLabel(s)}`}>
      <PillTabs label={t("entry.kind")} value={kind} onChange={(k) => { setKind(k); setPreview(null); setErr(null); }}
        items={kinds.map((k) => ({ id: k, label: t(`kind.${k}`) }))} />
      {saved ? (
        <div role="status" className="em-card border-ok/60 p-card text-body" data-testid="entry-saved">
          <span aria-hidden className="text-ok">✓ </span>
          {t("entry.saved", { no: saved.txn_no, amount: inr(saved.amount) })}{" "}
          {saved.receiver_message ? <span className="text-note text-muted">{t(`entry.receiver_${saved.receiver_message}`)}</span> : null}{" "}
          <Link className="em-link" to="/field/ledger">{t("entry.see_ledger")}</Link>
        </div>
      ) : null}
      <Panel>
        <form className="grid max-w-4xl gap-4 md:grid-cols-2" onSubmit={submit} data-testid="entry-form">
          <Field label={t("entry.amount")}>
            <input className="em-input num text-left" inputMode="decimal" required pattern="\d+(\.\d{1,2})?" value={v.amount}
              onChange={set("amount")} data-testid="entry-amount" />
          </Field>
          {kind !== "return" ? (
            <Field label={t("entry.date")}><input type="date" className="em-input" value={v.txn_date} max={todayIst()} onChange={set("txn_date")} /></Field>
          ) : <span />}
          {kind === "expense" ? (
            <>
              <Field label={t("entry.bucket")}>
                <select className="em-input" required value={v.top} onChange={set("top")} data-testid="entry-bucket">
                  <option value="" />
                  {buckets.tops.map((b) => <option key={b.bucket_id} value={b.bucket_id}>{name(b)}{b.sensitive ? ` · ${t("entry.sensitive")}` : ""}</option>)}
                </select>
              </Field>
              <Field label={t("entry.subhead")}>
                <select className="em-input" value={v.sub} onChange={set("sub")} disabled={!top?.subheads.length}>
                  <option value="">{t("entry.no_subhead")}</option>
                  {top?.subheads.map((b) => <option key={b.bucket_id} value={b.bucket_id}>{name(b)}</option>)}
                </select>
              </Field>
              {top?.code === "OTHER" ? (
                <Field label={t("entry.custom_label")}><input className="em-input" required maxLength={40} value={v.custom_label} onChange={set("custom_label")} /></Field>
              ) : null}
              <Field label={t("entry.area")}>
                <select className="em-input" value={v.area} onChange={set("area")}>
                  <option value="">{t("entry.area_default")}</option>
                  {places.blocks.map((b) => (
                    <optgroup key={b.block_id} label={name(b)}>
                      {places.areas.filter((a) => a.block_id === b.block_id).map((a) => <option key={a.area_id} value={a.area_id}>{name(a)}</option>)}
                    </optgroup>
                  ))}
                </select>
              </Field>
              <Field label={t("entry.fund_source")}>
                <select className="em-input" value={v.fund_source} onChange={set("fund_source")}>
                  <option value="">{t("entry.fund_default")}</option>
                  {FUND.map((f) => <option key={f} value={f}>{t(`fund_source.${f}`)}</option>)}
                </select>
              </Field>
              <Field label={t("entry.payee_kind")}>
                <select className="em-input" value={v.payee_kind} onChange={set("payee_kind")}>
                  <option value="" />
                  {PAYEE.map((p) => <option key={p} value={p}>{t(`payee.${p}`)}</option>)}
                </select>
              </Field>
              <Field label={t("entry.payee_label")}><input className="em-input" maxLength={80} value={v.payee_label} onChange={set("payee_label")} /></Field>
              {top?.ask_reach ? (
                <>
                  <Field label={t("entry.reach")}><input className="em-input" inputMode="numeric" pattern="\d*" value={v.reach_count} onChange={set("reach_count")} /></Field>
                  <Field label={t("entry.reach_unit")}>
                    <select className="em-input" value={v.reach_unit} onChange={set("reach_unit")}>
                      {REACH.map((r) => <option key={r} value={r}>{t(`reach.${r}`)}</option>)}
                    </select>
                  </Field>
                </>
              ) : null}
              {atLeast(s, "all") && others.length ? (
                <Field label={t("entry.paid_by")}>
                  <select className="em-input" value={v.payer} onChange={set("payer")}>
                    <option value="">{t("entry.paid_by_me")}</option>
                    {others.map((m) => <option key={m.member_id} value={m.member_id}>{m.display_name}</option>)}
                  </select>
                </Field>
              ) : null}
            </>
          ) : (
            <Field label={kind === "advance" ? t("entry.to_member") : t("entry.return_to")}>
              <select className="em-input" value={v.to} onChange={set("to")} required={kind === "advance"} data-testid="entry-to">
                <option value="">{kind === "return" ? t("entry.return_default") : ""}</option>
                {others.map((m) => <option key={m.member_id} value={m.member_id}>{m.display_name}</option>)}
              </select>
            </Field>
          )}
          <Field label={t("entry.pay_mode")}>
            <select className="em-input" value={v.pay_mode} onChange={set("pay_mode")}>
              {PAY.map((p) => <option key={p} value={p}>{t(`pay_mode.${p}`)}</option>)}
            </select>
          </Field>
          <div className="md:col-span-2">
            <Field label={t("entry.remark")}><textarea className="em-input" rows={2} maxLength={500} value={v.remark} onChange={set("remark")} /></Field>
          </div>
          {preview?.warnings.length ? (
            <div role="alert" className="rounded-ctl border border-warn/70 bg-warn/10 p-3 md:col-span-2" data-testid="entry-warnings">
              <p className="mb-2 text-body">{t("entry.warn_title")}</p>
              <ul className="flex flex-col gap-1 text-note">
                {preview.warnings.map((w) => <li key={w} className="flex items-center gap-2"><Chip tone="warn" icon="⚑">{t(`flag.${w}`)}</Chip>{t(`entry.warn.${w}`)}</li>)}
              </ul>
            </div>
          ) : null}
          {err ? (
            <div className="md:col-span-2"><ErrorState error={err} />
              {err instanceof ApiError ? <p className="mt-1 text-note text-muted">{err.detail}</p> : null}</div>
          ) : null}
          <div className="flex gap-2 md:col-span-2">
            <button type="submit" className="em-btn-primary" disabled={busy} data-testid="entry-submit">
              {preview?.warnings.length ? t("entry.save_anyway") : t("entry.save")}
            </button>
            <button type="button" className="em-btn" onClick={() => { setV(blank); setPreview(null); setErr(null); }}>{t("common.clear")}</button>
          </div>
        </form>
      </Panel>
    </Page>
  );
}
