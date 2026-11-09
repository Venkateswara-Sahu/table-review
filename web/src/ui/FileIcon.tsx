export function FileIcon() {
  return (
    <span className="file-icon" aria-hidden="true">
      <svg viewBox="0 0 30 38" fill="none">
        <path
          d="M2.5 1.5h16l9 9v26h-25z"
          fill="var(--surface-raised)"
          stroke="var(--line-strong)"
          strokeWidth="1"
        />
        <path
          d="M18.5 1.5v9h9"
          fill="var(--surface-2)"
          stroke="var(--line-strong)"
          strokeWidth="1"
        />
        <path
          d="M8 17h8M8 21h12M8 25h10M8 29h6"
          stroke="var(--line-strong)"
          strokeWidth="1.2"
        />
        <rect x="8" y="15" width="4" height="3" fill="var(--accent)" />
      </svg>
      <span>PDF</span>
    </span>
  );
}
