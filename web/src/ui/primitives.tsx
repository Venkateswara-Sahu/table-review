import {
  forwardRef,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { LoaderCircle } from "lucide-react";

export function cx(
  ...values: Array<string | false | null | undefined>
): string {
  return values.filter(Boolean).join(" ");
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "quiet";
type ButtonSize = "sm" | "md";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      className,
      variant = "secondary",
      size = "md",
      loading = false,
      icon,
      children,
      disabled,
      type = "button",
      ...props
    },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type={type}
        className={cx(
          "button",
          `button-${variant}`,
          `button-${size}`,
          !children && !!icon && "button-icon-only",
          className,
        )}
        disabled={disabled || loading}
        {...props}
      >
        {loading ? (
          <LoaderCircle className="button-spin" size={15} aria-hidden="true" />
        ) : (
          icon
        )}
        {children && <span>{children}</span>}
      </button>
    );
  },
);

export type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  label: string;
  loading?: boolean;
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton(
    {
      className,
      variant = "ghost",
      size = "sm",
      label,
      loading = false,
      disabled,
      children,
      type = "button",
      ...props
    },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type={type}
        aria-label={label}
        title={label}
        className={cx(
          "button",
          "button-icon-only",
          `button-${variant}`,
          `button-${size}`,
          className,
        )}
        disabled={disabled || loading}
        {...props}
      >
        {loading ? (
          <LoaderCircle className="button-spin" size={15} aria-hidden="true" />
        ) : (
          children
        )}
      </button>
    );
  },
);

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }
>(function Input({ className, invalid, ...props }, ref) {
  return (
    <input
      ref={ref}
      className={cx("input", invalid && "input-invalid", className)}
      {...props}
    />
  );
});

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement> & { invalid?: boolean }
>(function Textarea({ className, invalid, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      className={cx("input", "textarea", invalid && "input-invalid", className)}
      {...props}
    />
  );
});

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement>
>(function Select({ className, ...props }, ref) {
  return (
    <select ref={ref} className={cx("input", "select", className)} {...props} />
  );
});

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
  className,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  htmlFor?: string;
  className?: string;
}) {
  return (
    <label className={cx("field", className)} htmlFor={htmlFor}>
      <span className="field-label">{label}</span>
      {children}
      {error ? (
        <span className="field-error" role="alert">
          {error}
        </span>
      ) : hint ? (
        <span className="field-hint">{hint}</span>
      ) : null}
    </label>
  );
}

export function useFieldId(prefix: string) {
  const id = useId();
  return `${prefix}-${id}`;
}

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

export function Badge({
  tone = "neutral",
  children,
  dot = false,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span className={cx("badge", `badge-${tone}`, className)}>
      {dot && <span className="badge-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

export function Progress({
  value,
  max = 100,
  label,
  tone = "accent",
  showValue = false,
  hideLabel = false,
}: {
  value: number;
  max?: number;
  label?: string;
  tone?: "accent" | "success" | "warning";
  showValue?: boolean;
  hideLabel?: boolean;
}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className={cx("progress", `progress-${tone}`)}>
      {label && !hideLabel && (
        <div className="progress-meta">
          <span>{label}</span>
          {showValue && (
            <span className="num">
              {value} / {max}
            </span>
          )}
        </div>
      )}
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-label={label ?? "Progress"}
      >
        <span className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <LoaderCircle
      className="button-spin"
      size={size}
      role="status"
      aria-label="Loading"
    />
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

export function InlineNote({
  tone = "neutral",
  icon,
  title,
  children,
  action,
}: {
  tone?: Tone;
  icon?: ReactNode;
  title?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={cx("inline-note", `inline-note-${tone}`)}>
      {icon && <span className="inline-note-icon">{icon}</span>}
      <div className="inline-note-body">
        {title && <p className="inline-note-title">{title}</p>}
        {children}
      </div>
      {action && <div className="inline-note-action">{action}</div>}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  actions,
  compact = false,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={cx("empty-state", compact && "empty-state-compact")}>
      {icon && <div className="empty-state-icon">{icon}</div>}
      <h3>{title}</h3>
      {children && <div className="empty-state-body">{children}</div>}
      {actions && <div className="empty-state-actions">{actions}</div>}
    </div>
  );
}
