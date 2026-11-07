export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className={`wordmark ${compact ? "wordmark-compact" : ""}`}>
      <svg
        className="wordmark-mark"
        viewBox="0 0 18 18"
        aria-hidden="true"
        fill="none"
      >
        <rect
          x="1"
          y="1"
          width="16"
          height="16"
          rx="2"
          stroke="currentColor"
          strokeWidth="1.2"
        />
        <path
          d="M1 6.33h16M1 11.67h16M6.33 1v16M11.67 1v16"
          stroke="currentColor"
          strokeWidth="1"
          opacity=".42"
        />
        <rect
          x="6.33"
          y="6.33"
          width="5.34"
          height="5.34"
          fill="var(--accent)"
        />
      </svg>
      {!compact && <span>Table Review</span>}
    </span>
  );
}
