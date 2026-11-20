/** The field pages, mounted inside whatever shell provides SessionContext. Lift this file and
 * src/field, src/components, src/lib, src/i18n into the core app at merge. */
import { Navigate, Route, Routes } from "react-router-dom";
import { useSession } from "@/lib/session";
import { FIELD_PAGES, homeFor } from "./nav";
import { FieldHome } from "./FieldHome";
import { AddEntry } from "./spend/AddEntry";
import { Balances } from "./spend/Balances";
import { Ledger } from "./spend/Ledger";
import { Register } from "./spend/Register";
import { SpendOverview } from "./spend/SpendOverview";
import { Issues } from "./reports/Issues";
import { Reports } from "./reports/Reports";
import { ReportsMap } from "./reports/ReportsMap";
import { Settings } from "./admin/Settings";
import { Team } from "./admin/Team";

export function FieldRoutes() {
  const s = useSession();
  const allowed = (path: string) => FIELD_PAGES.find((p) => p.path === path)?.visible(s) ?? false;
  const page = (path: string, el: React.ReactNode) =>
    <Route key={path} path={path} element={allowed(path) ? el : <Navigate to={homeFor(s)} replace />} />;
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/field" replace />} />
      <Route path="/field" element={<FieldHome />} />
      {page("/field/spend", <SpendOverview />)}
      {page("/field/ledger", <Ledger />)}
      {page("/field/balances", <Balances />)}
      {page("/field/entry", <AddEntry />)}
      {page("/field/register", <Register />)}
      {page("/field/reports", <Reports />)}
      {page("/field/map", <ReportsMap />)}
      {page("/field/issues", <Issues />)}
      {page("/field/team", <Team />)}
      {page("/field/settings", <Settings />)}
      <Route path="*" element={<Navigate to="/field" replace />} />
    </Routes>
  );
}
