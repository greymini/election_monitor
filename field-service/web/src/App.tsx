/** Composition: the dev shell (dev-only) around the field routes (lifted at merge). */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider } from "@/lib/theme";
import { DevSessionProvider } from "@/devshell/DevSession";
import { Shell } from "@/devshell/Shell";
import { SignIn } from "@/devshell/SignIn";
import { FieldRoutes } from "@/field/routes";

const client = new QueryClient({
  defaultOptions: { queries: { staleTime: 15_000, retry: (n, e) => n < 1 && (e as { status?: number })?.status !== 403 } },
});

export function App() {
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <BrowserRouter>
          <DevSessionProvider signInPage={<SignIn />}>
            <Shell>
              <FieldRoutes />
            </Shell>
          </DevSessionProvider>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
