import { NextRequest, NextResponse } from "next/server";

function resolveAgentBaseUrl(): string {
  const agentUrl = process.env.AGENT_URL || "http://127.0.0.1:8000/awp";
  try {
    const url = new URL(agentUrl);
    url.pathname = "";
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return "http://127.0.0.1:8000";
  }
}

export const GET = async (req: NextRequest) => {
  const threadId = req.nextUrl.searchParams.get("thread_id") || "auto";
  const baseUrl = resolveAgentBaseUrl();
  const upstream = `${baseUrl}/skills?thread_id=${encodeURIComponent(threadId)}`;

  try {
    const response = await fetch(upstream, { method: "GET", cache: "no-store" });
    const text = await response.text();
    return new NextResponse(text, {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") || "application/json" },
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: "Failed to fetch skills",
        upstream,
        detail: String(error),
      },
      { status: 502 },
    );
  }
};
