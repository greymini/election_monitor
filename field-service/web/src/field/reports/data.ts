/** Report and issue types and hooks. Visibility (restricted categories, subtree) is applied by the API. */
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Named } from "../data";

export interface ReportRow {
  report_id: string; report_no: string; title: string | null; title_source: "auto" | "person" | null;
  status: string; urgency: "normal" | "urgent"; restricted: boolean; category_id: number | null;
  category_code: string | null; category_name_hi: string | null; block_id: string | null; area_id: string | null;
  reporter_member_id: string; reporter_name: string; assigned_to: string | null; filed_at: string;
  ack_at: string | null; resolved_at: string | null; escalation_level: number; note_text: string | null;
  lat: string | null; lon: string | null; issue_id: string | null; transcript_status: string;
  thumb_url: string | null; voice_count: number;
}

export interface Category extends Named { category_id: number; code: string; restricted: boolean }

export interface ReportSummary {
  by_area: { area_id: string | null; n: number; name_hi: string | null; name_en: string | null }[];
  by_category: { category_id: number | null; n: number }[];
  by_status: { status: string; n: number }[];
  by_week: { week: string; n: number }[];
  by_week_category: { week: string; category_id: number | null; n: number }[];
  open: { urgent: number; unsorted: number; total: number };
  medians: { ack_minutes: string | null; resolve_days: string | null };
  categories: Category[];
}

export interface ReportDetail {
  report: ReportRow & { transcript_hi: string | null; transcript_human: string | null; issue_id: string | null;
    duplicate_of: string | null; resolution_note: string | null; occurred_at: string | null; ac_id: string };
  media: { seq: number; role: string; asset_id: string; kind: string; mime: string | null; status: string;
    duration_s: number | null; url?: string }[];
  events: { at: string; kind: string; note: string | null; data: Record<string, unknown> | null; actor_name: string | null }[];
  duplicate_suggestions: { report_id: string; report_no: string; status: string; reason: string }[];
  transcript: { status: string; text: string | null; source: "machine" | "human" | null; label: string | null;
    engine: string | null; confidence: string | null };
  suggestion: { category_id: number | null; urgency: string | null; source: string } | null;
  issue: { issue_id: string; issue_no: string; title: string; status: string } | null;
  linked_spend?: { total: string; entries: { txn_id: string; txn_no: string; amount: string; txn_date: string;
    bucket_name_hi?: string | null }[]; scope: string };
  can: { triage: boolean; work: boolean; reopen: boolean; correct: boolean; manage_issues: boolean };
}

export interface Issue {
  issue_id: string; issue_no: string; title: string; status: "open" | "closed"; category_id: number | null;
  block_id: string | null; area_id: string; created_at: string; closed_at: string | null; close_note: string | null;
  report_count: number;
}

export function useReportSummary(params: Record<string, string | null> = {}) {
  const api = useApi();
  const s = useSession();
  return useQuery({ queryKey: ["report-summary", s.userId, params], queryFn: () => api.get<ReportSummary>("/reports/summary", params) });
}

export function useCategories() {
  const q = useReportSummary();
  return useMemo(() => new Map((q.data?.categories ?? []).map((c) => [c.category_id, c])), [q.data]);
}

export function useReports(params: Record<string, string | number | boolean | null>) {
  const api = useApi();
  const s = useSession();
  return useQuery({ queryKey: ["reports", s.userId, params],
    queryFn: () => api.get<{ items: ReportRow[]; total: number; without_location?: number }>("/reports", { limit: 200, ...params }) });
}

export function useIssues() {
  const api = useApi();
  const s = useSession();
  return useQuery({ queryKey: ["issues", s.userId], queryFn: () => api.get<{ items: Issue[]; total: number }>("/issues", { limit: 200 }) });
}

/** Age of a report in the largest sensible unit. */
export function age(iso: string, t: (k: string, o?: Record<string, unknown>) => string, now = Date.now()): string {
  const mins = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (mins < 60) return t("age.minutes", { n: mins });
  const hours = Math.round(mins / 60);
  if (hours < 48) return t("age.hours", { n: hours });
  return t("age.days", { n: Math.round(hours / 24) });
}
