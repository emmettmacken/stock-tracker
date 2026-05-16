import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const { password } = await request.json();

  const correctPassword = process.env.LOGIN_PASSWORD;
  const secret = process.env.SESSION_SECRET;

  if (!correctPassword || !secret) {
    return NextResponse.json({ error: "Auth not configured on server" }, { status: 500 });
  }

  if (password !== correctPassword) {
    return NextResponse.json({ error: "Invalid password" }, { status: 401 });
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set("session", secret, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 30, // 30 days
    path: "/",
  });
  return response;
}
