import { useState, type ReactNode } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";
import { cx } from "./primitives";

export type MenuItem = {
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  tone?: "default" | "danger";
  disabled?: boolean;
};

export function Menu({
  trigger,
  items,
  label,
  align = "end",
  width = 220,
}: {
  trigger: ReactNode;
  items: MenuItem[];
  label: string;
  align?: "start" | "center" | "end";
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent align={align} width={width} className="menu-popover">
        <div className="menu" role="menu" aria-label={label}>
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              className={cx(
                "menu-item",
                item.tone === "danger" && "menu-danger",
              )}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
            >
              {item.icon && <span className="menu-icon">{item.icon}</span>}
              {item.label}
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
