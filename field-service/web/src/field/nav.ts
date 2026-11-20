/** The Field (मैदान) nav group. Pages a member's scope cannot open are not offered (the API would
 * refuse them anyway: authorisation is in the query). */
import { atLeast, type Session } from "@/lib/session";

export interface FieldPage { path: string; key: string; visible: (s: Session) => boolean; section: "spend" | "reports" | "admin" }

const isMember = (s: Session) => s.memberId !== null;
const isAdmin = (s: Session) => s.role === "admin";

export const FIELD_PAGES: FieldPage[] = [
  { path: "/field/spend", key: "nav.field.spend", visible: (s) => atLeast(s, "team"), section: "spend" },
  { path: "/field/ledger", key: "nav.field.ledger", visible: (s) => atLeast(s, "own"), section: "spend" },
  { path: "/field/balances", key: "nav.field.balances", visible: (s) => atLeast(s, "team"), section: "spend" },
  { path: "/field/entry", key: "nav.field.entry", visible: (s) => atLeast(s, "own"), section: "spend" },
  { path: "/field/register", key: "nav.field.register", visible: (s) => atLeast(s, "all"), section: "spend" },
  { path: "/field/reports", key: "nav.field.reports", visible: isMember, section: "reports" },
  { path: "/field/map", key: "nav.field.map", visible: isMember, section: "reports" },
  { path: "/field/issues", key: "nav.field.issues", visible: isMember, section: "reports" },
  { path: "/field/team", key: "nav.field.team", visible: isAdmin, section: "admin" },
  { path: "/field/settings", key: "nav.field.settings", visible: (s) => atLeast(s, "all"), section: "admin" },
];

export function homeFor(s: Session): string {
  return FIELD_PAGES.find((p) => p.visible(s))?.path ?? "/field/reports";
}
