/** Page, KPI card, panel, pill tabs, filter row, drawer, and empty / error / loading states, built
 * to the core app's patterns (design-ref/NOTES.md). */
import { cloneElement, isValidElement, useEffect, useId, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Missing } from "./Values";

export function Page({ title, qualifier, badge, actions, children }: {
  title: string; qualifier?: string; badge?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="px-page pb-10 pt-5">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h1 className="flex flex-wrap items-baseline gap-2 text-title">
          {title}
          {qualifier ? <span className="text-panel font-normal text-muted">{qualifier}</span> : null}
          {badge}
        </h1>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <div className="flex flex-col gap-5">{children}</div>
    </div>
  );
}

/** Label UPPERCASE muted, value 36px bold, one or two muted context lines. */
export function KpiCard({ label, value, sub, missingReason, badge, testId }: {
  label: string; value: React.ReactNode | null | undefined; sub?: React.ReactNode; missingReason?: string;
  badge?: React.ReactNode; testId?: string;
}) {
  return (
    <div className="em-card min-w-0 flex-1 p-card" data-testid={testId}>
      <div className="flex items-center gap-2 text-label uppercase text-muted">{label}{badge}</div>
      <div className="mt-2 truncate text-kpi">{value ?? <Missing reason={missingReason} />}</div>
      {sub ? <div className="mt-1 text-note text-muted">{sub}</div> : null}
    </div>
  );
}

export function KpiRow({ children }: { children: React.ReactNode }) {
  return <div className="grid gap-gap [grid-template-columns:repeat(auto-fit,minmax(200px,1fr))]">{children}</div>;
}

export function Panel({ title, subtitle, action, footnote, children, className = "", testId }: {
  title?: React.ReactNode; subtitle?: React.ReactNode; action?: React.ReactNode; footnote?: React.ReactNode;
  children: React.ReactNode; className?: string; testId?: string;
}) {
  return (
    <section className={`em-card min-w-0 p-card ${className}`} data-testid={testId}>
      {title || action ? (
        <div className="mb-1 flex items-start justify-between gap-3">
          {title ? <h2 className="text-panel">{title}</h2> : <span />}
          {action ? <div className="shrink-0 text-note">{action}</div> : null}
        </div>
      ) : null}
      {subtitle ? <p className="mb-4 text-note text-muted">{subtitle}</p> : null}
      {children}
      {footnote ? <p className="mt-4 text-note text-muted">{footnote}</p> : null}
    </section>
  );
}

export function Grid({ cols = 2, children }: { cols?: 1 | 2 | 3; children: React.ReactNode }) {
  const c = { 1: "", 2: "lg:grid-cols-2", 3: "lg:grid-cols-3" }[cols];
  return <div className={`grid grid-cols-1 gap-5 ${c}`}>{children}</div>;
}

export interface PillItem<T extends string> { id: T; label: string; count?: number }

/** Pill tabs: inactive = surface + border; active = accent. */
export function PillTabs<T extends string>({ items, value, onChange, label }: {
  items: PillItem<T>[]; value: T; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1.5">
      {items.map((it) => (
        <button key={it.id} role="tab" type="button" aria-selected={it.id === value} onClick={() => onChange(it.id)}
          className={it.id === value ? "em-btn-primary" : "em-btn"}>
          {it.label}
          {it.count !== undefined ? <span className="text-label opacity-80">{it.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function FilterRow({ children, onClear }: { children: React.ReactNode; onClear?: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-end gap-2" role="search">
      {children}
      {onClear ? <button type="button" className="em-btn" onClick={onClear}>{t("common.clear")}</button> : null}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex flex-col gap-1 text-label text-muted">
      {label}
      <span className="[&>*]:w-full">{withId(children, id)}</span>
    </label>
  );
}

function withId(children: React.ReactNode, id: string) {
  return isValidElement(children) ? cloneElement(children as React.ReactElement<{ id?: string }>, { id }) : children;
}

export function Select<T extends string>({ value, onChange, options, label, allLabel, testId }: {
  value: T | ""; onChange: (v: T | "") => void; options: { value: T; label: string }[]; label: string;
  allLabel?: string; testId?: string;
}) {
  return (
    <Field label={label}>
      <select className="em-input min-w-[10rem]" value={value} data-testid={testId}
        onChange={(e) => onChange(e.target.value as T | "")}>
        {allLabel !== undefined ? <option value="">{allLabel}</option> : null}
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </Field>
  );
}

/** Side drawer: Escape closes, focus moves in and returns to the opener. */
export function Drawer({ open, onClose, title, children, wide = false }: {
  open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; wide?: boolean;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);
  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[1200] flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} aria-hidden />
      <div ref={ref} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined} tabIndex={-1}
        className={`relative flex h-full w-full flex-col border-l border-line bg-surface outline-none ${wide ? "max-w-3xl" : "max-w-xl"}`}>
        <div className="flex items-center justify-between gap-3 border-b border-line px-card py-4">
          <h2 className="text-panel">{title}</h2>
          <button type="button" className="em-btn" onClick={onClose} aria-label={t("common.close")}>✕</button>
        </div>
        <div className="flex-1 overflow-y-auto px-card py-4">{children}</div>
      </div>
    </div>
  );
}

export function EmptyState({ title, hint }: { title?: string; hint?: string }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-ctl border border-dashed border-line px-4 py-8 text-center" data-testid="empty">
      <p className="text-body text-ink-2">{title ?? t("common.empty")}</p>
      {hint ? <p className="mt-1 text-note text-muted">{hint}</p> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  const status = (error as { status?: number })?.status;
  const msg = status === 403 ? t("common.forbidden") : status === 404 ? t("common.not_found") : t("common.error");
  return (
    <div role="alert" className="rounded-ctl border border-bad/60 bg-bad/10 px-4 py-4 text-note" data-testid="error">
      <p className="text-ink"><span aria-hidden>✗ </span>{msg}</p>
      {onRetry ? <button type="button" className="em-btn mt-2" onClick={onRetry}>{t("common.retry")}</button> : null}
    </div>
  );
}

export function Loading() {
  const { t } = useTranslation();
  return <p className="py-6 text-note text-muted" aria-busy="true">{t("common.loading")}</p>;
}

/** Wraps a React Query result: loading, error and empty in one place. */
export function Async<T>({ q, empty, children }: {
  q: { data?: T; isLoading: boolean; error: unknown; refetch: () => unknown };
  empty?: (d: T) => boolean; children: (d: T) => React.ReactNode;
}) {
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.data === undefined) return <Loading />;
  if (empty?.(q.data)) return <EmptyState />;
  return <>{children(q.data)}</>;
}

/** A small muted line under a section (data-source line pattern). */
export function Note({ children }: { children: React.ReactNode }) {
  return <p className="text-note text-muted">{children}</p>;
}
