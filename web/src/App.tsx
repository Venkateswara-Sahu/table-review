import { useEffect, useState } from "react";
import {
  apiRequest,
  clearPrivateState,
  setCsrf,
  errorMessage,
} from "./api/client";
import type { Actor, Document, Session } from "./api/models";
import { SignIn } from "./features/auth/SignIn";
import { ReportList } from "./features/reports/ReportList";
import { ReportPages } from "./features/reports/ReportPages";

export default function App() {
  const [actor, setActor] = useState<Actor | null>(null);
  const [document, setDocument] = useState<Document | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  function leave() {
    return (
      !dirty ||
      window.confirm("Discard unsaved changes before leaving this table?")
    );
  }
  function back() {
    if (leave()) {
      setDocument(null);
      window.history.replaceState(null, "", "/");
    }
  }
  useEffect(() => {
    let live = true;
    const lost = () => {
      clearPrivateState();
      setActor(null);
      setDocument(null);
      setDirty(false);
      window.history.replaceState(null, "", "/");
      setError("");
    };
    window.addEventListener("session-lost", lost);
    apiRequest<Session>("/api/v1/session")
      .then((session) => {
        if (live) {
          setCsrf(session.csrf_token);
          setActor(session.actor);
          const id = new URLSearchParams(window.location.search).get(
            "document",
          );
          if (id)
            void apiRequest<Document>(
              `/api/v1/documents/${encodeURIComponent(id)}/open`,
              { method: "POST" },
            )
              .then((value) => {
                if (live) setDocument(value);
              })
              .catch((error) => {
                if (live) setError(errorMessage(error));
              });
        }
      })
      .catch(() => {})
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
      window.removeEventListener("session-lost", lost);
    };
  }, []);
  async function signOut() {
    if (!leave()) return;
    try {
      await apiRequest<void>("/api/v1/session", { method: "DELETE" });
    } catch (error) {
      setError(errorMessage(error));
      return;
    }
    clearPrivateState();
    setActor(null);
    setDocument(null);
    setDirty(false);
    window.history.replaceState(null, "", "/");
    setError("");
  }
  async function open(report: Document) {
    try {
      const updated = await apiRequest<Document>(
        `/api/v1/documents/${report.id}/open`,
        { method: "POST" },
      );
      setDocument(updated);
      window.history.replaceState(null, "", `/?document=${updated.id}&page=1`);
    } catch (error) {
      setError(errorMessage(error));
    }
  }
  return (
    <>
      <header className="app-header">
        <a
          href="/"
          className="wordmark"
          onClick={(event) => {
            event.preventDefault();
            back();
          }}
        >
          ▦ &nbsp; Table Review
        </a>
        <span className="trial-label">PRIVATE TRIAL</span>
        {actor && (
          <div className="account">
            <span>{actor.username}</span>
            <button onClick={() => void signOut()}>Sign out</button>
          </div>
        )}
      </header>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading ? (
        <main className="loading" role="status">
          Opening workspace…
        </main>
      ) : !actor ? (
        <SignIn
          onSignedIn={(value) => {
            setActor(value);
            setDocument(null);
          }}
        />
      ) : (
        <div key={actor.id}>
          {document ? (
            <ReportPages document={document} onBack={back} onDirty={setDirty} />
          ) : (
            <ReportList onOpen={(report) => void open(report)} />
          )}
        </div>
      )}
      <footer>
        Table Review <span>Source preserved. Human review recorded.</span>
      </footer>
    </>
  );
}
