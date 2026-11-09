import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "./primitives";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  title,
  description,
  children,
  footer,
  size = "md",
  className,
  side,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  className?: string;
  side?: "right";
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="dialog-overlay" />
      <DialogPrimitive.Content
        className={cx(
          side === "right" ? "sheet-content" : "dialog-content",
          side !== "right" && `dialog-${size}`,
          className,
        )}
      >
        <header className="dialog-head">
          <div>
            <DialogPrimitive.Title className="dialog-title">
              {title}
            </DialogPrimitive.Title>
            {description && (
              <DialogPrimitive.Description className="dialog-description">
                {description}
              </DialogPrimitive.Description>
            )}
          </div>
          <DialogPrimitive.Close className="button button-ghost button-sm button-icon-only dialog-close">
            <X size={16} aria-hidden="true" />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        </header>
        <div className="dialog-body">{children}</div>
        {footer && <footer className="dialog-footer">{footer}</footer>}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
