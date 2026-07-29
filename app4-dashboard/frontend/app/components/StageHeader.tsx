"use client";

import type { ReactNode } from "react";

interface StageHeaderProps {
  eyebrow: string;
  title: string;
  description: string;
  icon: ReactNode;
  iconClassName: string;
  status?: ReactNode;
}

export default function StageHeader({
  eyebrow,
  title,
  description,
  icon,
  iconClassName,
  status,
}: StageHeaderProps) {
  return (
    <section className="stage-header">
      <div className="min-w-0 max-w-3xl">
        <div className="eyebrow">{eyebrow}</div>
        <div className="mt-3 flex items-start gap-3.5">
          <span
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl ${iconClassName}`}
          >
            {icon}
          </span>
          <div className="min-w-0">
            <h2 className="text-2xl font-bold tracking-[-0.04em] text-[#17201e] sm:text-[1.7rem]">
              {title}
            </h2>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-[#697772]">
              {description}
            </p>
          </div>
        </div>
      </div>
      {status && <div className="shrink-0">{status}</div>}
    </section>
  );
}
