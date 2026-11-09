import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { LogOut } from "lucide-react";
import { apiRequest, clearPrivateState } from "../api/client";
import { queryClient } from "./query";
import { useSession } from "./session";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { Spinner } from "../ui/primitives";

export function AccountMenu() {
  const { actor } = useSession();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

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
      <PopoverContent width={248} className="account-popover">
        <div className="account-card">
          <p className="account-card-name">{actor.username}</p>
          <p className="account-card-meta">Private workspace</p>
          <div className="hairline" />
          <button
            className="menu-item"
            disabled={busy}
            onClick={() => void signOut()}
          >
            {busy ? <Spinner size={14} /> : <LogOut size={15} />}
            Sign out
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
