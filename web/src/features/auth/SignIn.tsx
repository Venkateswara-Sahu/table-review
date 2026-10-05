import { useState, type FormEvent } from "react";
import {
  apiRequest,
  clearPrivateState,
  errorMessage,
  setCsrf,
} from "../../api/client";
import type { Actor, Session } from "../../api/models";

export function SignIn({ onSignedIn }: { onSignedIn: (actor: Actor) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      clearPrivateState();
      const session = await apiRequest<Session>("/api/v1/session", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setCsrf(session.csrf_token);
      setPassword("");
      onSignedIn(session.actor);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="sign-in">
      <div className="intro">
        <p className="eyebrow">TABLE REVIEW / PRIVATE WORKSPACE</p>
        <h1>
          Keep the source.
          <br />
          Check the table.
        </h1>
        <p>
          Turn annual-report tables into a spreadsheet you have reviewed, with
          the source and your corrections alongside it.
        </p>
        <div className="workflow">
          <span>01 &nbsp; Select</span>
          <span>02 &nbsp; Check</span>
          <span>03 &nbsp; Export</span>
        </div>
      </div>
      <form onSubmit={submit} className="sign-in-form">
        <h2>Sign in to your workspace</h2>
        <p className="muted">Use your individual trial account.</p>
        <label>
          Username
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            maxLength={80}
          />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            maxLength={1024}
          />
        </label>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <button className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="fine-print">
          Your reports are private to your account. Automatic extraction
          supports English digital PDFs; every table needs review.
        </p>
      </form>
    </main>
  );
}
