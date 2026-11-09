import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Check, LogIn } from "lucide-react";
import {
  apiRequest,
  clearPrivateState,
  errorMessage,
  setCsrf,
} from "../api/client";
import type { SessionCreated } from "../api/models";
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
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (!loading && actor) return <Navigate to={from} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      clearPrivateState();
      const session = await apiRequest<SessionCreated>("/api/v1/session", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setCsrf(session.csrf_token);
      setPassword("");
      queryClient.setQueryData(["session"], {
        actor: session.actor,
        csrf_token: session.csrf_token,
      });
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
            <h1>Sign in</h1>
            <p className="muted">Your reports stay on this machine.</p>
          </div>
          <form className="auth-form" onSubmit={submit}>
            <Field label="Username" htmlFor="username">
              <Input
                id="username"
                name="username"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
                maxLength={80}
              />
            </Field>
            <Field label="Password" htmlFor="password">
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                maxLength={1024}
              />
            </Field>
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
              icon={<LogIn size={15} />}
              className="auth-submit"
            >
              Sign in
            </Button>
          </form>
          <details className="auth-first-run">
            <summary>First run on this machine</summary>
            <p>
              There is no public sign-up. Create the first account from the
              host, then sign in here.
            </p>
            <pre>
              <code>
                docker compose --env-file .cache/local.env -p table-review-local
                -f infra/compose.yaml exec api python -m
                table_review.accounts.admin create your-username
              </code>
            </pre>
          </details>
        </div>
        <p className="auth-footer">
          Table Review <span>·</span> Private, local review
        </p>
      </section>
    </main>
  );
}
