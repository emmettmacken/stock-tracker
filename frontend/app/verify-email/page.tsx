"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { BASE } from "@/lib/auth";

type Status = "pending" | "success" | "error";

function VerifyEmailInner() {
  const params = useSearchParams();
  const token = params.get("token");
  const [status, setStatus] = useState<Status>("pending");
  const [message, setMessage] = useState("Verifying your email…");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setMessage("Missing verification token.");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${BASE}/auth/verify-email?token=${encodeURIComponent(token)}`);
        const data = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (res.ok) {
          setStatus("success");
          setMessage(data.message ?? "Email verified. You can now log in.");
        } else {
          setStatus("error");
          setMessage(data.detail ?? "Invalid or expired token.");
        }
      } catch {
        if (!cancelled) {
          setStatus("error");
          setMessage("Something went wrong — try again.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const tone =
    status === "success" ? "text-emerald-400" : status === "error" ? "text-red-400" : "text-zinc-400";

  return (
    <div className="w-full max-w-sm">
      <h1 className="flex items-center justify-center gap-2 text-2xl font-semibold tracking-tight text-white mb-1">
        <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" aria-hidden />
        Stock Tracker
      </h1>
      <p className="text-zinc-500 text-sm text-center mb-8">Email verification</p>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6 text-center">
        <p className={`text-sm font-medium ${tone}`}>{message}</p>
        {status !== "pending" && (
          <Link
            href="/login"
            className="inline-block mt-5 text-zinc-300 hover:text-white underline underline-offset-2 text-sm"
          >
            Go to sign in
          </Link>
        )}
      </div>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <main className="min-h-screen bg-zinc-950 flex items-center justify-center px-4">
      <Suspense fallback={<p className="text-zinc-500 text-sm">Loading…</p>}>
        <VerifyEmailInner />
      </Suspense>
    </main>
  );
}
