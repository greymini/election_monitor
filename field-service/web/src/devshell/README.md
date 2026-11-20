# devshell: DEV-ONLY, replaced at merge

This folder stands in for the Election Monitor's real app shell so the field pages can run on
their own:

- the header (brand, the core's nav groups as inert placeholders, the constituency switcher,
  language, theme, role chip, sign out) and the footer note;
- a **dev sign-in** that takes stub-core tokens from the demo backend (`/demo/api/token`);
- a **"view as" switch** (principal, coordinator, in-charge, worker, treasurer) so scoping can be
  seen.

At merge, delete this folder and mount `src/field/routes.tsx` inside the core's shell. The core's
shell must provide `SessionContext` (src/lib/session.tsx) from its own sign-in, plus `ThemeProvider`
and i18next. See docs/MERGE.md, frontend section.
