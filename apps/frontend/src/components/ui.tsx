import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  ComponentProps,
} from "react";
import { Icon, type IconName } from "./Icon";
import { cn } from "../lib/utils";
import { Button as ShadcnButton } from "./ui/button";
import { Card as ShadcnCard } from "./ui/card";
import { Badge as ShadcnBadge } from "./ui/badge";
import { Input } from "./ui/input";
import { Skeleton as ShadcnSkeleton } from "./ui/skeleton";
import { Alert, AlertTitle, AlertDescription } from "./ui/alert";

const buttonVariants = {
  primary: "default",
  secondary: "outline",
  quiet: "ghost",
  danger: "destructive",
} as const;

export function Button({
  variant = "primary",
  size = "md",
  icon,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "quiet" | "danger";
  size?: "sm" | "md";
  icon?: IconName;
}) {
  return (
    <ShadcnButton
      variant={buttonVariants[variant]}
      size={size === "md" ? "default" : size}
      className={cn("min-h-10", className)}
      {...props}
    >
      {icon && <Icon name={icon} size={16} />}
      {children}
    </ShadcnButton>
  );
}

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: "neutral" | "success" | "warning" | "danger" | "fixture" | "live";
  children: ReactNode;
  className?: string;
}) {
  return (
    <ShadcnBadge
      variant="secondary"
      className={cn(
        "font-medium",
        ["success", "live"].includes(tone) && "bg-emerald-50 text-emerald-700",
        ["warning", "fixture"].includes(tone) && "bg-amber-50 text-amber-700",
        tone === "danger" && "bg-red-50 text-red-700",
        className,
      )}
    >
      {children}
    </ShadcnBadge>
  );
}

export function SourceBadge({ mode }: { mode?: "live" | "fixture" }) {
  return mode === "fixture" ? <Badge tone="fixture">Demo data</Badge> : null;
}

export function Card({
  children,
  className = "",
  ...props
}: ComponentProps<typeof ShadcnCard>) {
  return (
    <ShadcnCard {...props} className={cn("gap-5 p-6 shadow-none", className)}>
      {children}
    </ShadcnCard>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  icon?: IconName;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="space-y-2 py-8 text-center text-muted-foreground">
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function ErrorPanel({
  title = "Something went wrong",
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <Alert variant="destructive" className="gap-y-3 p-6 sm:p-8">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="gap-4 leading-6">
        <p>{message}</p>
        {onRetry && (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Try again
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <ShadcnSkeleton
      className={cn("min-h-24 animate-pulse rounded-lg bg-muted", className)}
      role="status"
      aria-label="Loading"
    />
  );
}

export function TextInput({
  label,
  hint,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
      <span>{label}</span>
      <Input {...props} />
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function StatusDot({
  tone = "neutral",
}: {
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  return (
    <span
      className={cn(
        "inline-block size-2 rounded-full bg-muted-foreground",
        tone === "success" && "bg-emerald-600",
        tone === "warning" && "bg-amber-600",
        tone === "danger" && "bg-red-600",
      )}
      aria-hidden="true"
    />
  );
}
