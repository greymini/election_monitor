/** DEV-ONLY. Signs in with a stub-core token from the demo backend and provides SessionContext.
 * The "view as" switch re-signs as another demo user. Replaced by the core's sign-in at merge. */
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { SessionContext, type Session } from "@/lib/session";

const STORE = "em-dev-user";
const AC = 32;

export interface DevUser { user_id: string; name: string; persona: string; finance_scope: string }

interface DevCtx {
  users: DevUser[];
  current: DevUser | null;
  signIn: (userId: string) => Promise<void>;
  signOut: () => void;
}
const Ctx = createContext<DevCtx>({ users: [], current: null, signIn: async () => undefined, signOut: () => undefined });
export const useDev = () => useContext(Ctx);

interface Me {
  user: { user_id: string; display_name: string; role: string };
  ac: { ac_number: number; name_hi: string; name_en: string };
  member: { member_id: string; display_name: string; is_principal: boolean; is_coordinator: boolean;
    finance_scope: Session["financeScope"] } | null;
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json() as Promise<T>;
}

export function DevSessionProvider({ children, signInPage }: { children: React.ReactNode; signInPage: React.ReactNode }) {
  const [users, setUsers] = useState<DevUser[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [current, setCurrent] = useState<DevUser | null>(null);
  const [ready, setReady] = useState(false);
  const qc = useQueryClient();

  const signIn = useCallback(async (userId: string) => {
    const { token } = await json<{ token: string }>(await fetch("/demo/api/token", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId }),
    }));
    const me = await json<Me>(await fetch(`/acs/${AC}/field/me`, { headers: { Authorization: `Bearer ${token}` } }));
    qc.clear();                                  // nothing cached for one user is shown to the next
    setSession({
      token, acNumber: me.ac.ac_number, acName: { name_hi: me.ac.name_hi, name_en: me.ac.name_en }, userId: me.user.user_id,
      userName: me.member?.display_name ?? me.user.display_name, role: me.user.role,
      financeScope: me.member?.finance_scope ?? null, memberId: me.member?.member_id ?? null,
      isPrincipal: !!me.member?.is_principal, isCoordinator: !!me.member?.is_coordinator,
    });
    try { sessionStorage.setItem(STORE, userId); } catch { /* private mode */ }
  }, [qc]);

  const signOut = useCallback(() => {
    qc.clear();
    setSession(null);
    try { sessionStorage.removeItem(STORE); } catch { /* private mode */ }
  }, [qc]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const list = (await json<{ users: DevUser[] }>(await fetch("/demo/api/dev-users"))).users;
        if (!live) return;
        setUsers(list);
        const saved = sessionStorage.getItem(STORE);
        if (saved && list.some((u) => u.user_id === saved)) await signIn(saved);
      } catch {
        /* backend not reachable: the sign-in page says so */
      } finally {
        if (live) setReady(true);
      }
    })();
    return () => { live = false; };
  }, [signIn]);

  useEffect(() => {
    setCurrent(session ? users.find((u) => u.user_id === session.userId) ?? null : null);
  }, [session, users]);

  if (!ready) return null;
  return (
    <Ctx.Provider value={{ users, current, signIn, signOut }}>
      {session ? <SessionContext.Provider value={session}>{children}</SessionContext.Provider> : signInPage}
    </Ctx.Provider>
  );
}
