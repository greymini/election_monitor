/** The spend screens' filter row: period, block, area, member. One row above the charts. */
import { useTranslation } from "react-i18next";
import { Field, FilterRow, Select } from "@/components/Layout";
import { useAreaIndex, useBalances, useNamer, useSpendFilters, type Period } from "../data";
import { atLeast, useSession } from "@/lib/session";

const PERIODS: Period[] = ["7d", "30d", "month", "all", "custom"];

export function SpendFilterRow({ withMember = true }: { withMember?: boolean }) {
  const { t } = useTranslation();
  const s = useSession();
  const { f, set, clear } = useSpendFilters();
  const name = useNamer();
  const places = useAreaIndex();
  const members = useBalances(withMember && atLeast(s, "team"));
  const areas = places.areas.filter((a) => !f.block || a.block_id === f.block);
  return (
    <FilterRow onClear={clear}>
      <Select label={t("filter.period")} value={f.period} testId="filter-period"
        onChange={(v) => set({ period: (v || "30d") as Period })}
        options={PERIODS.map((p) => ({ value: p, label: t(`period.${p}`) }))} />
      {f.period === "custom" ? (
        <>
          <Field label={t("filter.from")}>
            <input type="date" className="em-input" value={f.from ?? ""} onChange={(e) => set({ from: e.target.value })} />
          </Field>
          <Field label={t("filter.to")}>
            <input type="date" className="em-input" value={f.to ?? ""} onChange={(e) => set({ to: e.target.value })} />
          </Field>
        </>
      ) : null}
      <Select label={t("filter.block")} value={f.block} allLabel={t("common.all")} testId="filter-block"
        onChange={(v) => set({ block: v })} options={places.blocks.map((b) => ({ value: b.block_id, label: name(b) }))} />
      <Select label={t("filter.area")} value={f.area} allLabel={t("common.all")} testId="filter-area"
        onChange={(v) => set({ area: v })} options={areas.map((a) => ({ value: a.area_id, label: name(a) }))} />
      {withMember && members.data ? (
        <Select label={t("filter.member")} value={f.member} allLabel={t("common.all")} testId="filter-member"
          onChange={(v) => set({ member: v })}
          options={members.data.members.map((m) => ({ value: m.member_id, label: m.display_name }))} />
      ) : null}
    </FilterRow>
  );
}
