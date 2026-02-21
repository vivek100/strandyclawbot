import { CopilotRuntime, ExperimentalEmptyAdapter, copilotRuntimeNextJSAppRouterEndpoint } from "@copilotkit/runtime";
import { HttpAgent } from "@ag-ui/client";
import { NextRequest } from "next/server";

const agentUrl = process.env.AGENT_URL || "http://127.0.0.1:8000/awp";

// Runtime and AG-UI client publish slightly different message typings; cast for compatibility.
const clawdbot = new HttpAgent({ url: agentUrl }) as any;

const runtime = new CopilotRuntime({
  agents: { clawdbot },
});

const serviceAdapter = new ExperimentalEmptyAdapter();

export const POST = async (req: NextRequest) => {
  const startedAt = Date.now();
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter,
    endpoint: "/api/copilotkit",
  });
  try {
    const res = await handleRequest(req);
    console.info("[copilotkit-route] request complete", {
      status: res.status,
      durationMs: Date.now() - startedAt,
      agentUrl,
    });
    return res;
  } catch (error) {
    console.error("[copilotkit-route] request failed", {
      durationMs: Date.now() - startedAt,
      agentUrl,
      error,
    });
    throw error;
  }
};
