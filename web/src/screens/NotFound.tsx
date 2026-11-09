import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { EmptyState } from "../ui/primitives";

export function NotFoundScreen() {
  return (
    <main className="page page-narrow">
      <EmptyState
        title="This page is not here"
        icon={<span className="mono faint">404</span>}
        actions={
          <Link to="/" className="button button-secondary button-md">
            <ArrowLeft size={15} aria-hidden="true" />
            <span>Back to reports</span>
          </Link>
        }
      >
        <p className="muted">
          The report or table may have been deleted, or the link may be
          incomplete.
        </p>
      </EmptyState>
    </main>
  );
}
