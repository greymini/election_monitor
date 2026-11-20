/** Data table as in the core's Booth table: raised header, optional per-column filter inputs, click a
 * heading to sort, shift-click to add a second sort, numbers right-aligned in tabular figures. */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

export interface Column<R> {
  key: string;
  header: string;
  render: (r: R) => React.ReactNode;
  /** value used for sorting; omit to make the column unsortable */
  sort?: (r: R) => string | number | null | undefined;
  /** text matched by the column's filter input; omit for no filter */
  filter?: (r: R) => string | null | undefined;
  align?: "left" | "right";
  width?: string;
}

type SortKey = { key: string; dir: 1 | -1 };

export function sortRows<R>(rows: R[], columns: Column<R>[], keys: SortKey[]): R[] {
  if (!keys.length) return rows;
  const cols = keys.map((k) => ({ ...k, col: columns.find((c) => c.key === k.key)! })).filter((k) => k.col?.sort);
  return [...rows].sort((a, b) => {
    for (const { col, dir } of cols) {
      const x = col.sort!(a), y = col.sort!(b);
      if (x === y) continue;
      if (x === null || x === undefined) return 1;           // missing values sort last either way
      if (y === null || y === undefined) return -1;
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "hi");
      if (c) return c * dir;
    }
    return 0;
  });
}

export function DataTable<R>({ columns, rows, rowKey, onRowClick, initialSort = [], filters = true, caption,
  maxHeight, testId }: {
  columns: Column<R>[]; rows: R[]; rowKey: (r: R) => string; onRowClick?: (r: R) => void; initialSort?: SortKey[];
  filters?: boolean; caption?: string; maxHeight?: string; testId?: string;
}) {
  const { t } = useTranslation();
  const [sort, setSort] = useState<SortKey[]>(initialSort);
  const [query, setQuery] = useState<Record<string, string>>({});
  const filtered = useMemo(() => rows.filter((r) => columns.every((c) => {
    const q = query[c.key]?.trim().toLowerCase();
    return !q || !c.filter || (c.filter(r) ?? "").toLowerCase().includes(q);
  })), [rows, columns, query]);
  const shown = useMemo(() => sortRows(filtered, columns, sort), [filtered, columns, sort]);

  function clickHeader(e: React.MouseEvent, key: string) {
    setSort((cur) => {
      const at = cur.find((k) => k.key === key);
      const flipped: SortKey = { key, dir: at ? (at.dir === 1 ? -1 : 1) : 1 };
      if (e.shiftKey) return at ? cur.map((k) => (k.key === key ? flipped : k)) : [...cur, flipped];
      return [flipped];
    });
  }
  const hasFilters = filters && columns.some((c) => c.filter);

  return (
    <div data-testid={testId}>
      <p className="mb-2 text-note text-muted">
        {t("table.count", { shown: shown.length, total: rows.length })} · {t("table.sort_hint")}
      </p>
      <div className="em-card overflow-auto" style={{ maxHeight }}>
        <table className="w-full border-collapse text-body">
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead className="sticky top-0 z-10 bg-raised">
            <tr>
              {columns.map((c) => {
                const s = sort.findIndex((k) => k.key === c.key);
                const dir = s >= 0 ? sort[s].dir : 0;
                return (
                  <th key={c.key} scope="col" style={{ width: c.width }}
                    aria-sort={dir === 1 ? "ascending" : dir === -1 ? "descending" : undefined}
                    className={`px-4 py-2.5 text-note font-normal text-muted ${c.align === "right" ? "text-right" : "text-left"}`}>
                    {c.sort ? (
                      <button type="button" onClick={(e) => clickHeader(e, c.key)} className="inline-flex items-center gap-1 hover:text-ink">
                        {c.header}
                        <span aria-hidden className="text-label">{dir === 1 ? "▲" : dir === -1 ? "▼" : "↕"}</span>
                        {sort.length > 1 && s >= 0 ? <span className="text-label">{s + 1}</span> : null}
                      </button>
                    ) : c.header}
                  </th>
                );
              })}
            </tr>
            {hasFilters ? (
              <tr>
                {columns.map((c) => (
                  <th key={c.key} className="px-4 pb-2.5 font-normal">
                    {c.filter ? (
                      <input className="em-input w-full font-semibold" placeholder={t("table.filter")} aria-label={`${t("table.filter")}: ${c.header}`}
                        value={query[c.key] ?? ""} onChange={(e) => setQuery((q) => ({ ...q, [c.key]: e.target.value }))} />
                    ) : null}
                  </th>
                ))}
              </tr>
            ) : null}
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={rowKey(r)} onClick={onRowClick ? () => onRowClick(r) : undefined}
                onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(r); } : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                className={`border-t border-divider ${onRowClick ? "cursor-pointer hover:bg-raised" : ""}`}>
                {columns.map((c) => (
                  <td key={c.key} className={`px-4 py-2.5 align-top ${c.align === "right" ? "num" : ""}`}>{c.render(r)}</td>
                ))}
              </tr>
            ))}
            {!shown.length ? (
              <tr><td colSpan={columns.length} className="px-4 py-6 text-center text-note text-muted">{t("common.empty")}</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-note text-muted">{t("table.dash_note")}</p>
    </div>
  );
}
