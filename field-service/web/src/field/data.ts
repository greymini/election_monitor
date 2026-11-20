/** Field API types and React Query hooks shared by the spend and report screens. Every list the API
 * returns is already narrowed to what the signed-in member may see (authorisation is in the query). */
import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { useApi } from "@/lib/api";
import { addDays, todayIst } from "@/lib/format";
import { useSession } from "@/lib/session";

export type Money = string;           // decimal string from the API

export interface Named { name_hi: string; name_en: string }
export interface Bucket extends Named {
  bucket_id: number; parent_id: number | null; code: string; sensitive: boolean; ask_reach: boolean; sort: number;
  statutory_head: string | null; subheads: Bucket[];
}
export interface Area extends Named { area_id: string; block_id: string; kind: string; electors: number | null }
export interface Block extends Named { block_id: string }

export interface Totals {
  spend_total: Money; entries: number; unclassified_pct: Money | null; tail_count_pct: Money | null;
  tail_value_pct: Money | null; top_decile_value_pct: Money | null; in_transit: Money; disputed: Money;
}
export interface Ceiling { window_id: number; starts_on: string; ends_on: string; ceiling_amount: Money | null;
  counted: Money; ceiling_used_pct: Money | null }
export interface Group { [k: string]: string | number | null; spend: Money; entries: number; share_pct: Money | null }
export interface Summary { group_by: string[]; totals: Totals; groups: Group[]; ceiling: Ceiling[] }

export interface BalanceRow {
  member_id: string; display_name: string; parent_member_id: string | null; is_principal: boolean; depth: number;
  finance_scope: string; received: Money; spent: Money; passed_on: Money; returned: Money; adjusted_out: Money;
  balance: Money | null; in_transit: Money; disputed: Money; last_entry_date: string | null;
  logging_lag_days: Money | null; overdue_acks: number;
}

export interface Txn {
  txn_id: string; txn_no: string; kind: "expense" | "advance" | "return" | "inflow" | "adjustment"; amount: Money;
  txn_date: string; status: "posted" | "pending_ack" | "disputed" | "void"; flagged: string[];
  from_member_id: string | null; to_member_id: string | null; from_name: string | null; to_name: string | null;
  created_by: string; created_by_name: string | null; created_via: string; created_at: string;
  bucket_id: number | null; bucket_code: string | null; bucket_name_hi: string | null;
  top_bucket_code: string | null; top_bucket_name_hi: string | null; custom_label: string | null;
  block_id: string | null; area_id: string | null; fund_source: string | null; pay_mode: string | null;
  payee_kind: string | null; payee_label: string | null; reach_count: number | null; reach_unit: string | null;
  remark: string | null; receipt_asset_id: string | null; in_election_window: boolean;
  counts_to_ceiling: boolean | null; suggest_source: string | null; suggest_conf: Money | null;
  void_reason: string | null; voided_at: string | null; report_id: string | null; issue_id: string | null;
}

/** name_hi / name_en by the current language. */
export function useNamer() {
  const { i18n } = useTranslation();
  const hi = i18n.language === "hi";
  return useCallback((o: Named | null | undefined, fallback = "") => (o ? (hi ? o.name_hi : o.name_en) || o.name_hi : fallback), [hi]);
}

export function useBuckets() {
  const api = useApi();
  const s = useSession();
  return useQuery({ queryKey: ["buckets", s.userId], queryFn: () => api.get<Bucket[]>("/buckets"), staleTime: 300_000,
    enabled: s.financeScope !== null && s.financeScope !== "none" });
}

/** Buckets by id and by code (sub-heads included), with their top-level parent. */
export function useBucketIndex() {
  const q = useBuckets();
  return useMemo(() => {
    const byId = new Map<number, Bucket & { top: Bucket; sortIndex: number }>();
    const byCode = new Map<string, Bucket>();
    for (const top of q.data ?? []) {
      byId.set(top.bucket_id, { ...top, top, sortIndex: -1 });
      byCode.set(top.code, top);
      top.subheads.forEach((s, i) => { byId.set(s.bucket_id, { ...s, top, sortIndex: i }); byCode.set(s.code, s); });
    }
    return { tops: q.data ?? [], byId, byCode, ready: !!q.data };
  }, [q.data]);
}

export function usePlaces() {
  const api = useApi();
  const s = useSession();
  return useQuery({ queryKey: ["places", s.acNumber], staleTime: 600_000,
    queryFn: () => api.get<{ blocks: Block[]; areas: Area[]; map: { tile_url: string; attribution: string } }>("/places") });
}

export function useAreaIndex() {
  const q = usePlaces();
  return useMemo(() => ({
    areas: q.data?.areas ?? [], blocks: q.data?.blocks ?? [],
    area: new Map((q.data?.areas ?? []).map((a) => [a.area_id, a])),
    block: new Map((q.data?.blocks ?? []).map((b) => [b.block_id, b])),
  }), [q.data]);
}

export function useBalances(enabled = true) {
  const api = useApi();
  const s = useSession();
  return useQuery({ queryKey: ["balances", s.userId], enabled,
    queryFn: () => api.get<{ members: BalanceRow[]; team_position: { holds: string; owed: string } }>("/balances") });
}

// ---- page filters in the URL ----------------------------------------------------------------

export type Period = "7d" | "30d" | "month" | "all" | "custom";
export interface SpendFilters {
  period: Period; from: string | null; to: string | null;
  block: string; area: string; member: string; bucket: string;
}

export function periodRange(period: Period, from: string | null, to: string | null, today = todayIst()) {
  switch (period) {
    case "7d": return { from: addDays(today, -6), to: today };
    case "30d": return { from: addDays(today, -29), to: today };
    case "month": return { from: `${today.slice(0, 8)}01`, to: today };
    case "custom": return { from, to };
    default: return { from: null, to: null };
  }
}

export function useSpendFilters() {
  const [sp, setSp] = useSearchParams();
  const f: SpendFilters = {
    period: (sp.get("period") as Period) || "30d",
    from: sp.get("from"), to: sp.get("to"),
    block: sp.get("block") ?? "", area: sp.get("area") ?? "", member: sp.get("member") ?? "",
    bucket: sp.get("bucket") ?? "",
  };
  const set = useCallback((patch: Partial<SpendFilters>) => {
    setSp((cur) => {
      const next = new URLSearchParams(cur);
      for (const [k, v] of Object.entries(patch)) {
        if (v === "" || v === null || v === undefined) next.delete(k);
        else next.set(k, String(v));
      }
      if (patch.block !== undefined && patch.area === undefined) next.delete("area");    // an area belongs to a block
      return next;
    }, { replace: true });
  }, [setSp]);
  const clear = useCallback(() => setSp(new URLSearchParams(), { replace: true }), [setSp]);
  const range = periodRange(f.period, f.from, f.to);
  /** query params for the spend API */
  const params = {
    date_from: range.from, date_to: range.to, block_id: f.block || null, area_id: f.area || null,
    member_id: f.member || null, bucket_id: f.bucket || null,
  };
  return { f, set, clear, params, range };
}
