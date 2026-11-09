import { Wordmark } from "./Wordmark";

export function BootScreen() {
  return (
    <main className="boot-screen" role="status">
      <Wordmark />
      <span className="boot-loader" aria-hidden="true" />
      <span className="sr-only">Opening workspace</span>
    </main>
  );
}
