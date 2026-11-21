import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Check, LogIn, UserPlus } from "lucide-react";
import {
  apiRequest,
  clearPrivateState,
  errorMessage,
  setCsrf,
} from "../api/client";
import type { SessionCreated, SetupStatus } from "../api/models";
import { useSession } from "../app/session";
import { queryClient } from "../app/query";
import { Wordmark } from "../ui/Wordmark";
import { Button, Field, Input, InlineNote } from "../ui/primitives";

export function SignInScreen() {
  const { actor, loading } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const from =
    (location.state as { from?: string } | null)?.from &&
    (location.state as { from?: string }).from !== "/signin"
      ? (location.state as { from?: string }).from!
      : "/";
  const setupQuery = useQuery({
    queryKey: ["setup-status"],
    queryFn: () => apiRequest<SetupStatus>("/api/v1/setup/status"),
    retry: false,
    staleTime: 30_000,
  });
  const setupRequired = setupQuery.data?.setup_required === true;
  const isHosted = setupQuery.data?.deployment_mode === "hosted";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (!loading && actor) return <Navigate to={from} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (setupRequired) {
      if (password.length < 12) {
        setError("Password must contain at least 12 characters");
        return;
      }
      if (password !== confirmPassword) {
        setError("Passwords do not match");
        return;
      }
    }
    setBusy(true);
    try {
      clearPrivateState();
      const session = await apiRequest<SessionCreated>(
        setupRequired ? "/api/v1/setup" : "/api/v1/session",
        {
          method: "POST",
          body: JSON.stringify({ username, password }),
        },
      );
      setCsrf(session.csrf_token);
      setPassword("");
      setConfirmPassword("");
      queryClient.setQueryData(["session"], {
        actor: session.actor,
        csrf_token: session.csrf_token,
      });
      if (setupRequired) {
        queryClient.setQueryData<SetupStatus>(["setup-status"], {
          setup_required: false,
          deployment_mode: "local",
          local_onboarding_enabled: true,
        });
      }
      navigate(from, { replace: true });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-art" aria-hidden="true">
        <div className="auth-art-inner">
          <p className="auth-art-copy">
            Every figure,
            <br />
            back to the page.
          </p>
          <div className="auth-sheet">
            <div className="auth-sheet-head">
              <span>Consolidated statement</span>
              <span className="mono">42</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>2024</th>
                  <th>2023</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Revenue</td>
                  <td className="num">1,234.50</td>
                  <td className="num">1,100.00</td>
                </tr>
                <tr className="auth-row-active">
                  <td>Operating profit</td>
                  <td className="num">318.20</td>
                  <td className="num">286.40</td>
                </tr>
                <tr>
                  <td>Finance costs</td>
                  <td className="num">(42.10)</td>
                  <td className="num">(39.80)</td>
                </tr>
                <tr>
                  <td>Profit before tax</td>
                  <td className="num">276.10</td>
                  <td className="num">246.60</td>
                </tr>
              </tbody>
            </table>
            <div className="auth-sheet-cell" />
            <div className="auth-sheet-check">
              <Check size={12} strokeWidth={2.5} />
              Confirmed against source
            </div>
          </div>
          <p className="auth-art-note">
            Local-first · Source preserved · Every decision recorded
          </p>
        </div>
      </section>

      <section className="auth-form-side">
        <div className="auth-form-wrap">
          <Wordmark />
          <div className="auth-heading">
            <h1>{setupRequired ? "Create your local workspace" : "Sign in"}</h1>
            <p className="muted">
              {setupRequired
                ? "First run on this machine — choose a username and password (12+ characters). Everything stays local."
                : "Your reports stay on this machine."}
            </p>
          </div>
          <form className="auth-form" onSubmit={submit}>
            <Field
              label="Username"
              htmlFor={setupRequired ? "setup-username" : "username"}
            >
              <Input
                id={setupRequired ? "setup-username" : "username"}
                name="username"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
                minLength={setupRequired ? 3 : 1}
                maxLength={80}
              />
            </Field>
            <Field
              label="Password"
              htmlFor={setupRequired ? "setup-password" : "password"}
            >
              <Input
                id={setupRequired ? "setup-password" : "password"}
                name="password"
                type="password"
                autoComplete={
                  setupRequired ? "new-password" : "current-password"
                }
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                minLength={setupRequired ? 12 : 1}
                maxLength={1024}
              />
            </Field>
            {setupRequired && (
              <Field label="Confirm password" htmlFor="setup-confirm">
                <Input
                  id="setup-confirm"
                  name="confirmPassword"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  required
                  minLength={12}
                  maxLength={1024}
                />
              </Field>
            )}
            {error && (
              <InlineNote tone="danger">
                <p>{error}</p>
              </InlineNote>
            )}
            <Button
              type="submit"
              variant="primary"
              size="md"
              loading={busy}
              icon={
                setupRequired ? <UserPlus size={15} /> : <LogIn size={15} />
              }
              className="auth-submit"
            >
              {setupRequired ? "Create account & open workspace" : "Sign in"}
            </Button>
          </form>
          {!setupRequired && !isHosted && (
            <details className="auth-first-run">
              <summary>Manage local accounts</summary>
              <p>
                Local accounts are private to this machine. To add another user,
                reset a password, or revoke access, run:
              </p>
              <pre>
                <code>
                  python -m table_review.accounts.admin create|reset|revoke
                  &lt;username&gt;
                </code>
              </pre>
            </details>
          )}
        </div>
        <p className="auth-footer">
          Table Review <span>·</span> Private, local review
        </p>
      </section>
    </main>
  );
}
