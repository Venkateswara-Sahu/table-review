import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { LogOut, Monitor, Moon, Sun } from "lucide-react";
import { apiRequest, clearPrivateState } from "../api/client";
import { useCapabilities } from "../api/hooks";
import { queryClient } from "./query";
import { useSession } from "./session";
import { useTheme, type ThemePreference } from "./theme";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { Spinner } from "../ui/primitives";

export function AccountMenu() {
  const { actor } = useSession();
  const capabilities = useCapabilities(!!actor);
  const { preference, systemScheme, setPreference } = useTheme();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const localAutoLogin = capabilities.data?.local_auto_login === true;

  if (!actor) return null;

  async function signOut() {
    setBusy(true);
    try {
      await apiRequest<void>("/api/v1/session", { method: "DELETE" });
    } catch {
      /* Clear the local session even if the server call is lost. */
    } finally {
      clearPrivateState();
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== "session",
      });
      queryClient.setQueryData(["session"], null);
      setBusy(false);
      setOpen(false);
      navigate("/signin", { replace: true });
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button className="account-trigger" aria-label="Account menu">
          <span className="avatar" aria-hidden="true">
            {actor.username.slice(0, 2).toUpperCase()}
          </span>
          <span className="account-name">{actor.username}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent width={260} className="account-popover">
        <div className="account-card">
          <p className="account-card-name">{actor.username}</p>
          <p className="account-card-meta">
            {localAutoLogin ? "Local workbench" : "Private workspace"}
          </p>
          <div className="hairline" />
          <div className="theme-control" role="group" aria-label="Appearance">
            <span className="theme-control-label">
              Appearance
              {preference === "system" && (
                <span className="muted"> (System · {systemScheme})</span>
              )}
            </span>
            <div className="theme-segmented">
              {(
                [
                  {
                    value: "system" as ThemePreference,
                    label: "System",
                    title: `System (currently ${systemScheme})`,
                    icon: <Monitor size={13} aria-hidden="true" />,
                  },
                  {
                    value: "light" as ThemePreference,
                    label: "Light",
                    title: "Light theme",
                    icon: <Sun size={13} aria-hidden="true" />,
                  },
                  {
                    value: "dark" as ThemePreference,
                    label: "Dark",
                    title: "Dark theme",
                    icon: <Moon size={13} aria-hidden="true" />,
                  },
                ] as const
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={
                    preference === option.value
                      ? "theme-option is-active"
                      : "theme-option"
                  }
                  aria-pressed={preference === option.value}
                  title={option.title}
                  onClick={() => setPreference(option.value)}
                >
                  {option.icon}
                  <span>{option.label}</span>
                </button>
              ))}
            </div>
          </div>
          {!localAutoLogin && (
            <>
              <div className="hairline" />
              <button
                className="menu-item"
                disabled={busy}
                onClick={() => void signOut()}
              >
                {busy ? <Spinner size={14} /> : <LogOut size={15} />}
                Sign out
              </button>
            </>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
