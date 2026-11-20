/** Small value renderers shared by every screen: missing values, money, labels for machine output
 * and estimates, provenance of entries. */
import { useTranslation } from "react-i18next";
import { inr, num, pct } from "@/lib/format";

/** NULL is shown as "—" with the reason on hover (and to screen readers), never as 0. */
export function Missing({ reason }: { reason?: string }) {
  const { t } = useTranslation();
  const why = reason ?? t("common.missing_reason");
  return (
    <span className="cursor-help text-muted" title={why} aria-label={why} data-missing="">
      —
    </span>
  );
}

type V = string | number | null | undefined;

export function Amount({ value, reason, className = "" }: { value: V; reason?: string; className?: string }) {
  const s = inr(value);
  return <span className={`num ${className}`}>{s ?? <Missing reason={reason} />}</span>;
}

export function Num({ value, digits = 0, reason }: { value: V; digits?: number; reason?: string }) {
  const s = num(value, digits);
  return <span className="num">{s ?? <Missing reason={reason} />}</span>;
}

export function Pct({ value, reason }: { value: V; reason?: string }) {
  const s = pct(value);
  return <span className="num">{s ?? <Missing reason={reason} />}</span>;
}

/** Outlined badge, the core's "Modelled estimate" pattern. */
export function Badge({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <span title={title}
      className="inline-flex items-center whitespace-nowrap rounded border border-muted px-1.5 py-px text-label font-normal normal-case tracking-normal text-ink">
      {children}
    </span>
  );
}

/** Machine output (suggested bucket, auto title, transcript) is always labelled. */
export function MachineLabel({ what }: { what?: string }) {
  const { t } = useTranslation();
  return <Badge title={t("common.machine_hint")}>{what ?? t("common.machine")}</Badge>;
}

export function EstimateLabel() {
  const { t } = useTranslation();
  return <Badge title={t("common.estimate_hint")}>{t("common.estimate")}</Badge>;
}

/** "entered by X via Telegram / web" (+ when). */
export function Provenance({ by, via, at }: { by?: string | null; via?: string | null; at?: string | null }) {
  const { t } = useTranslation();
  const channel = via === "telegram" ? t("common.via_telegram") : via === "web" ? t("common.via_web") : via;
  return (
    <span className="text-note text-muted">
      {t("common.entered_by", { name: by ?? t("common.system") })}
      {channel ? ` · ${channel}` : ""}
      {at ? ` · ${at}` : ""}
    </span>
  );
}

/** A chip for flags and small facts. tone picks the status colour; the text always carries the meaning. */
export function Chip({ children, tone = "neutral", icon, title }: {
  children: React.ReactNode; tone?: "neutral" | "ok" | "warn" | "serious" | "bad" | "accent"; icon?: string; title?: string;
}) {
  const dot = { neutral: "", ok: "bg-ok", warn: "bg-warn", serious: "bg-serious", bad: "bg-bad", accent: "bg-accent" }[tone];
  return (
    <span title={title} className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-chip bg-raised px-2 py-0.5 text-label text-ink">
      {icon ? <span aria-hidden>{icon}</span> : dot ? <span aria-hidden className={`inline-block h-2 w-2 rounded-full ${dot}`} /> : null}
      {children}
    </span>
  );
}

const TXN_TONE: Record<string, "ok" | "warn" | "bad" | "neutral"> = { posted: "ok", pending_ack: "warn", disputed: "bad", void: "neutral" };
const TXN_ICON: Record<string, string> = { posted: "✓", pending_ack: "⏳", disputed: "⚠", void: "⊘" };
const REPORT_TONE: Record<string, "ok" | "warn" | "bad" | "neutral" | "accent"> = {
  draft: "neutral", new: "warn", acknowledged: "accent", assigned: "accent", in_progress: "accent",
  resolved: "ok", closed_no_action: "neutral", duplicate: "neutral",
};

/** Status with icon + label: colour never carries the meaning alone. */
export function StatusChip({ status, kind = "txn" }: { status: string; kind?: "txn" | "report" | "issue" }) {
  const { t } = useTranslation();
  if (kind === "txn") return <Chip tone={TXN_TONE[status] ?? "neutral"} icon={TXN_ICON[status]}>{t(`txn_status.${status}`)}</Chip>;
  if (kind === "issue") return <Chip tone={status === "open" ? "warn" : "ok"}>{t(`issue_status.${status}`)}</Chip>;
  return <Chip tone={REPORT_TONE[status] ?? "neutral"}>{t(`report_status.${status}`)}</Chip>;
}

export function FlagChips({ flags }: { flags: string[] | null | undefined }) {
  const { t } = useTranslation();
  if (!flags?.length) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {flags.map((f) => (
        <Chip key={f} tone={f === "ack_overdue" || f === "cash_over_limit" ? "serious" : "warn"} icon="⚑">
          {t(`flag.${f}`)}
        </Chip>
      ))}
    </span>
  );
}

/** Colour dot for a bucket or category; always next to its name. */
export function Dot({ color }: { color: string }) {
  return <span aria-hidden className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color }} />;
}
