import { lazy, Suspense } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import {
  BrowserRouter,
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { Keyboard } from "lucide-react";
import { queryClient } from "./query";
import { RequireSession, SessionProvider } from "./session";
import { NoticeProvider } from "./notices";
import { ConfirmProvider } from "./confirm";
import { AccountMenu } from "./AccountMenu";
import { ShortcutsProvider, useShortcuts } from "./shortcuts";
import { TooltipProvider } from "../ui/tooltip";
import { BootScreen } from "../ui/BootScreen";
import { IconButton } from "../ui/primitives";
import { Wordmark } from "../ui/Wordmark";

const LibraryScreen = lazy(() =>
  import("../screens/Library").then((m) => ({ default: m.LibraryScreen })),
);
const WorkspaceScreen = lazy(() =>
  import("../screens/Workspace").then((m) => ({ default: m.WorkspaceScreen })),
);
const SignInScreen = lazy(() =>
  import("../screens/SignIn").then((m) => ({ default: m.SignInScreen })),
);
const NotFoundScreen = lazy(() =>
  import("../screens/NotFound").then((m) => ({ default: m.NotFoundScreen })),
);

function AppFrame() {
  const location = useLocation();
  const { open: openShortcuts } = useShortcuts();
  const isWorkspace = /^\/reports\/[^/]+\/pages\/\d+/.test(location.pathname);

  return (
    <div className="app-shell">
      {!isWorkspace && (
        <header className="topbar">
          <Link to="/" className="wordmark-link" aria-label="Table Review home">
            <Wordmark />
          </Link>
          <div className="topbar-actions">
            <IconButton label="Keyboard shortcuts" onClick={openShortcuts}>
              <Keyboard size={16} />
            </IconButton>
            <AccountMenu />
          </div>
        </header>
      )}
      <Outlet />
    </div>
  );
}

function ProtectedFrame() {
  return (
    <RequireSession>
      <AppFrame />
    </RequireSession>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <ConfirmProvider>
          <NoticeProvider>
            <TooltipProvider>
              <ShortcutsProvider>
                <BrowserRouter>
                  <Suspense fallback={<BootScreen />}>
                    <Routes>
                      <Route path="/signin" element={<SignInScreen />} />
                      <Route element={<ProtectedFrame />}>
                        <Route path="/" element={<LibraryScreen />} />
                        <Route
                          path="/reports/:documentId"
                          element={<Navigate to="pages/1" replace />}
                        />
                        <Route
                          path="/reports/:documentId/pages/:pageNo"
                          element={<WorkspaceScreen />}
                        />
                        <Route
                          path="/reports/:documentId/pages/:pageNo/tables/:tableId"
                          element={<WorkspaceScreen />}
                        />
                        <Route path="*" element={<NotFoundScreen />} />
                      </Route>
                    </Routes>
                  </Suspense>
                </BrowserRouter>
              </ShortcutsProvider>
            </TooltipProvider>
          </NoticeProvider>
        </ConfirmProvider>
      </SessionProvider>
    </QueryClientProvider>
  );
}
