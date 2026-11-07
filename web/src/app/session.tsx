import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { Navigate, useLocation } from "react-router-dom";
import { apiRequest, clearPrivateState, setCsrf } from "../api/client";
import type { Actor, Session } from "../api/models";
import { queryClient } from "./query";
import { BootScreen } from "../ui/BootScreen";

type SessionValue = {
  actor: Actor | null;
  loading: boolean;
};

const SessionContext = createContext<SessionValue>({
  actor: null,
  loading: true,
});

export function SessionProvider({ children }: { children: ReactNode }) {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => apiRequest<Session>("/api/v1/session"),
    retry: false,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (session.data) setCsrf(session.data.csrf_token);
  }, [session.data]);

  useEffect(() => {
    const lost = () => {
      clearPrivateState();
      // Drop every private cache entry but leave the session probe itself in
      // place: clearing it while its own request is in flight would restart
      // the 401 probe forever and trap the app on the boot screen.
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== "session",
      });
      queryClient.setQueryData<Session | null>(["session"], null);
    };
    window.addEventListener("session-lost", lost);
    return () => window.removeEventListener("session-lost", lost);
  }, []);

  const value = useMemo<SessionValue>(
    () => ({
      actor: session.data?.actor ?? null,
      loading: session.isPending,
    }),
    [session.data, session.isPending],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession() {
  return useContext(SessionContext);
}

export function RequireSession({ children }: { children: ReactNode }) {
  const { actor, loading } = useSession();
  const location = useLocation();
  if (loading) return <BootScreen />;
  if (!actor) {
    return (
      <Navigate
        to="/signin"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    );
  }
  return <>{children}</>;
}
