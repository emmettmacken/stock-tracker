"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { logout } from "@/lib/auth";

export function Nav() {
  const path = usePathname();

  async function handleLogout() {
    // Revokes the refresh token + clears cookies on the API, then redirects to /login.
    await logout();
  }

  return (
    <nav className="sticky top-0 z-40 border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur-md supports-[backdrop-filter]:bg-zinc-950/60">
      <div className="max-w-5xl mx-auto px-4 h-14 flex items-center gap-7">
        <span className="flex items-center gap-2 text-sm font-semibold text-zinc-100 tracking-tight">
          <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" aria-hidden />
          Stock Tracker
        </span>
        <div className="flex gap-1 flex-1">
          {([
            ["/portfolio", "Portfolio"],
            ["/watchlist", "Watchlist"],
            ["/strategy-lab", "Strategy Lab"],
            ["/automation", "Automation"],
          ] as const).map(([href, label]) => {
            const active = path === href;
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={`rounded-md px-2.5 py-1.5 text-sm transition-colors duration-150 ease-out-quart ${
                  active
                    ? "bg-zinc-800 text-zinc-100 font-medium"
                    : "text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/60"
                }`}
              >
                {label}
              </Link>
            );
          })}
        </div>
        <button
          onClick={handleLogout}
          className="rounded-md px-2 py-1 text-xs text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/60 transition-colors duration-150 ease-out-quart active:scale-[0.98]"
        >
          Sign out
        </button>
      </div>
    </nav>
  );
}
