import { ArrowUpRight, Minus, X } from "lucide-react";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

export function PanelHeader({
  title,
  meta,
  onDetach,
  onCollapse,
  onClose,
  right,
}: {
  title: string;
  meta?: ReactNode;
  onDetach?: () => void;
  onCollapse?: () => void;
  onClose?: () => void;
  right?: ReactNode;
}) {
  return (
    <div className="h-7 px-2 flex items-center justify-between border-b border-border bg-surface-elev shrink-0">
      <div className="flex items-center gap-3">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </span>
        {meta && <span className="text-[10px] mono text-muted-foreground/80">{meta}</span>}
      </div>
      <div className="flex items-center gap-1">
        {right}
        {onDetach && (
          <button
            onClick={onDetach}
            title="Detach"
            className="size-5 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-white/5 rounded"
          >
            <ArrowUpRight className="size-3" />
          </button>
        )}
        {onCollapse && (
          <button
            onClick={onCollapse}
            title="Collapse"
            className="size-5 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-white/5 rounded"
          >
            <Minus className="size-3" />
          </button>
        )}
        {onClose && (
          <button
            onClick={onClose}
            title="Close"
            className="size-5 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-white/5 rounded"
          >
            <X className="size-3" />
          </button>
        )}
      </div>
    </div>
  );
}

export function GroupBox({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <fieldset
      className={cn(
        "border border-border rounded bg-surface/40 px-3 pt-2 pb-3 space-y-2.5",
        className,
      )}
    >
      <legend className="px-1 text-[9px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {title}
      </legend>
      {children}
    </fieldset>
  );
}

export function Row({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-2 text-[11px]">
      <label className="text-foreground/70" title={hint}>
        {label}
      </label>
      <div className="flex items-center gap-1.5">{children}</div>
    </div>
  );
}

export function MiniSelect({
  value,
  options,
  onChange,
}: {
  value: string;
  options: readonly string[] | string[];
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="bg-surface-elev border border-border rounded px-1.5 h-6 text-[11px] mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

export function MiniInput({
  value,
  onChange,
  width = 56,
  type = "text",
  step,
}: {
  value: string | number;
  onChange: (v: string) => void;
  width?: number;
  type?: string;
  step?: number;
}) {
  return (
    <input
      type={type}
      value={value}
      step={step}
      onChange={(e) => onChange(e.target.value)}
      style={{ width }}
      className="bg-surface-elev border border-border rounded px-1.5 h-6 text-[11px] mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
    />
  );
}

export function MiniButton({
  children,
  onClick,
  variant = "default",
  className,
  disabled,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "ghost";
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "h-7 px-2.5 text-[11px] font-medium rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed",
        variant === "default" &&
          "bg-surface-elev border border-border text-foreground hover:bg-white/5",
        variant === "primary" &&
          "bg-accent/15 border border-accent/40 text-accent hover:bg-accent/25",
        variant === "ghost" && "text-muted-foreground hover:text-foreground hover:bg-white/5",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function MiniCheckbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex items-center gap-1.5 text-[11px] text-foreground/80 cursor-pointer select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="size-3 accent-[color:var(--accent)]"
      />
      {label}
    </label>
  );
}

export function Swatch({ color }: { color: string }) {
  return (
    <span
      className="inline-block size-2.5 rounded-sm border border-white/15"
      style={{ background: color }}
    />
  );
}
