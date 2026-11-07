import { useEffect, useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import {
  BrowserRouter,
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
} from "react-router-dom";
import { Keyboard } from "lucide-react";
import { queryClient } from "./query";
import { RequireSession, SessionProvider } from "./session";
import { NoticeProvider } from "./notices";
import { ConfirmProvider } from "./confirm";
import { AccountMenu } from "./AccountMenu";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { TooltipProvider } from "../ui/tooltip";
import { IconButton } from "../ui/primitives";
import { Wordmark } from "../ui/Wordmark";
import { LibraryScreen } from "../screens/Library";
import { WorkspaceScreen } from "../screens/Workspace";
import { SignInScreen } from "../screens/SignIn";
import { StudyScreen } from "../screens/Study";
import { NotFoundScreen } from "../screens/NotFound";
import { StudyIndicator } from "../features/study/StudyIndicator";

function isTyping(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
  );
}

function AppFrame() {
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "?" && !isTyping(event.target)) {
        event.preventDefault();
        setShortcutsOpen(true);
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/" className="wordmark-link" aria-label="Table Review home">
          <Wordmark />
        </Link>
        <div className="topbar-actions">
          <StudyIndicator />
          <IconButton
            label="Keyboard shortcuts"
            onClick={() => setShortcutsOpen(true)}
          >
            <Keyboard size={16} />
          </IconButton>
          <AccountMenu />
        </div>
      </header>
      <Outlet />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
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
              <BrowserRouter>
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
                    <Route path="/study" element={<StudyScreen />} />
                    <Route path="*" element={<NotFoundScreen />} />
                  </Route>
                </Routes>
              </BrowserRouter>
            </TooltipProvider>
          </NoticeProvider>
        </ConfirmProvider>
      </SessionProvider>
    </QueryClientProvider>
  );
}
