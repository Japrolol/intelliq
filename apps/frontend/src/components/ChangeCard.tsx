import type { ReactNode } from "react";

export function ChangeCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <article className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5">
      <h3 className="text-base font-medium leading-6">{title}</h3>
      {children}
    </article>
  );
}
