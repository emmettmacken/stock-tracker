"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

export function Nav() {
  const path = usePathname();
  return (
    <nav className="border-b border-zinc-800 bg-zinc-950">
      <div className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-6">
        <span className="text-sm font-semibold text-zinc-300">Stock Tracker</span>
        <div className="flex gap-5">
          {([
            ["/", "Watchlist"],
            ["/portfolio", "Portfolio"],
            ["/automation", "Automation"],
          ] as const).map(([href, label]) => (
            <Link
              key={href}
              href={href}
              className={`text-sm transition-colors ${
                path === href ? "text-white font-medium" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {label}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  );
}
