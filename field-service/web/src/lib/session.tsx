/** What every field page needs from the app shell: a bearer token, the AC, and who is signed in.
 *
 * The field pages only call useSession(). Here the provider is the dev shell (src/devshell); at merge
 * the core's shell provides the same context from its own sign-in (docs/MERGE.md, frontend).
 */
import { createContext, useContext } from "react";
import i18n from "i18next";

export type Role = "admin" | "rep" | string;

export interface Session {
  token: string;
  acNumber: number;
  /** the constituency name in both languages; show it with acLabel() */
  acName: { name_hi: string; name_en: string };
  userId: string;
  userName: string;
  role: Role;
  /** Finance scope of the signed-in member (none | own | team | all); null when not a team member. */
  financeScope: "none" | "own" | "team" | "all" | null;
  memberId: string | null;
  isPrincipal: boolean;
  isCoordinator: boolean;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside a signed-in shell");
  return s;
}

const RANK = { none: 0, own: 1, team: 2, all: 3 } as const;

/** Mirrors pulse/api/ledger_auth.at_least: what the API will allow, so pages hide what would 403. */
export function atLeast(s: Session, min: keyof typeof RANK): boolean {
  return s.financeScope !== null && RANK[s.financeScope] >= RANK[min];
}

/** The constituency name in the current language. */
export function acLabel(s: Session): string {
  return (i18n.language === "en" ? s.acName.name_en : s.acName.name_hi) || s.acName.name_hi;
}
