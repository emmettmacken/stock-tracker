import { Watchlist } from "@/components/Watchlist";

export default function Home() {
  return (
    <main className="min-h-screen bg-zinc-950 text-white">
      <div className="max-w-5xl mx-auto px-4 py-10">
        <header className="mb-10">
          <h1 className="text-3xl font-bold tracking-tight text-white">
            Stock Signal Tracker
          </h1>
          <p className="mt-1 text-zinc-400 text-sm">
            Markov chain buy/sell/hold signals from 90 days of price history
          </p>
        </header>
        <Watchlist />
      </div>
    </main>
  );
}
