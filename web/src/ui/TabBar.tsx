import type { ReactNode } from "react";
import { cx } from "./primitives";

export type TabItem = {
  id: string;
  label: string;
  badge?: ReactNode;
};

export function TabBar({
  value,
  onChange,
  items,
  label,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  items: TabItem[];
  label: string;
  className?: string;
}) {
  return (
    <div
      className={cx("tab-bar", className)}
      role="tablist"
      aria-label={label}
      onKeyDown={(event) => {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        const tabs = Array.from(
          event.currentTarget.querySelectorAll<HTMLButtonElement>(
            '[role="tab"]',
          ),
        );
        const current = tabs.indexOf(event.target as HTMLButtonElement);
        if (current < 0) return;
        event.preventDefault();
        const next =
          tabs[
            (current + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) %
              tabs.length
          ];
        next?.focus();
        next?.click();
      }}
    >
      {items.map((item) => (
        <button
          key={item.id}
          role="tab"
          type="button"
          aria-selected={value === item.id}
          tabIndex={value === item.id ? 0 : -1}
          className={cx("tab", value === item.id && "tab-active")}
          onClick={() => onChange(item.id)}
        >
          {item.label}
          {item.badge != null && (
            <span className="tab-badge">{item.badge}</span>
          )}
        </button>
      ))}
    </div>
  );
}
