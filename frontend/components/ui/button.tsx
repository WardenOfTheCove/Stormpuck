"use client";

import * as React from "react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "secondary" | "outline" | "ghost" | "destructive";
  size?: "default" | "sm" | "lg" | "icon";
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = "", variant = "default", size = "default", ...props }, ref) => {
    const base = "inline-flex items-center justify-center rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-400 disabled:pointer-events-none disabled:opacity-50 select-none";

    const variants = {
      default: "bg-zinc-100 text-zinc-900 shadow hover:bg-zinc-200 active:scale-[0.98]",
      secondary: "bg-zinc-800 text-zinc-100 hover:bg-zinc-700 active:scale-[0.98]",
      outline: "border border-zinc-800 bg-transparent text-zinc-200 hover:bg-zinc-900 hover:text-zinc-50",
      ghost: "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100",
      destructive: "bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30",
    };

    const sizes = {
      default: "h-9 px-4 py-2",
      sm: "h-8 px-3 text-xs",
      lg: "h-11 px-8 text-base",
      icon: "h-9 w-9",
    };

    return (
      <button
        ref={ref}
        className={`${base} ${variants[variant]} ${sizes[size]} ${className}`}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";