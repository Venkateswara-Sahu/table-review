export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className={`wordmark ${compact ? "wordmark-compact" : ""}`}>
      <svg
        className="wordmark-mark"
        viewBox="0 0 20 20"
        aria-hidden="true"
        fill="none"
      >
        <rect
          x="1.25"
          y="1.25"
          width="17.5"
          height="17.5"
          rx="2.5"
          stroke="currentColor"
          strokeWidth="1.25"
        />
        <path
          d="M1.5 6.5h17M1.5 13h17M7.25 6.5v12"
          stroke="currentColor"
          strokeWidth="1"
          opacity=".42"
        />
        <path
          d="M7.25 14.5h11.25"
          stroke="var(--brass)"
          strokeWidth="1"
          opacity=".85"
        />
        <rect
          x="7.75"
          y="7"
          width="5.5"
          height="5.5"
          rx="0.6"
          fill="var(--accent)"
        />
      </svg>
      {!compact && <span className="wordmark-name">Table Review</span>}
    </span>
  );
}
