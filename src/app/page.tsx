"use client";

import { useEffect, useMemo, useState } from "react";
import { useCoAgent, useCopilotChatInternal } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { Header } from "./components/Header";
import { CanvasPanel } from "./components/CanvasPanel";

const SUGGESTIONS = [
  "What's the current time and list the files in my home directory?",
  "Write a Python script that generates a Fibonacci sequence",
  "Calculate the compound interest on $10,000 at 7% for 20 years",
  "Run 'df -h' and tell me my disk usage",
];
const CHAT_TRY = [
  "List files in my current project directory",
  "Create a Python script that prints Fibonacci numbers",
];

interface CanvasItem {
  type: "code" | "text" | "file" | "output";
  title: string;
  content: string;
  language?: string;
  timestamp?: string;
}

interface AgentState {
  canvas_items?: CanvasItem[];
  active_task?: string;
  last_command?: string;
  thinking_stream?: string;
}

export default function Home() {
  const [canvasOpen, setCanvasOpen] = useState(true);
  const [lastSignalKey, setLastSignalKey] = useState("");
  const [thinkingCollapsed, setThinkingCollapsed] = useState(false);
  const [thinkingLocal, setThinkingLocal] = useState("");

  const { messages, sendMessage, isLoading } = useCopilotChatInternal();
  const { state } = useCoAgent<AgentState>({
    name: "clawdbot",
    initialState: { canvas_items: [], active_task: undefined, last_command: undefined, thinking_stream: "" },
  });
  const thinkingStream = String(state?.thinking_stream || "");

  const toolSignal = useMemo(() => {
    const all = messages ?? [];
    const assistantToolCalls = all
      .filter((m: any) => m?.role === "assistant" && Array.isArray(m?.toolCalls))
      .flatMap((m: any) => m.toolCalls ?? []);
    const toolResults = all.filter((m: any) => m?.role === "tool");

    return {
      toolCallCount: assistantToolCalls.length,
      toolResultCount: toolResults.length,
      canvasItemCount: (state?.canvas_items ?? []).length,
      activeTask: state?.active_task,
    };
  }, [messages, state?.canvas_items, state?.active_task]);

  useEffect(() => {
    const key = `${toolSignal.toolCallCount}:${toolSignal.toolResultCount}:${toolSignal.canvasItemCount}:${toolSignal.activeTask ?? ""}`;
    if (key === lastSignalKey) return;
    setLastSignalKey(key);

    const shouldOpen =
      toolSignal.toolCallCount > 0 ||
      toolSignal.toolResultCount > 0 ||
      toolSignal.canvasItemCount > 0 ||
      Boolean(toolSignal.activeTask);

    if (shouldOpen) {
      setCanvasOpen(true);
    }

    console.info("[Home] tool signal", { ...toolSignal, shouldOpen });
  }, [toolSignal, lastSignalKey]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setCanvasOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const hasRealConversation = (messages ?? []).some((m: any) => {
    const role = (m as any)?.role;
    if (role === "user") return true;
    if (role === "assistant") {
      const content = String((m as any)?.content ?? "").trim();
      const toolCalls = (m as any)?.toolCalls;
      return content.length > 0 || (Array.isArray(toolCalls) && toolCalls.length > 0);
    }
    return false;
  });
  const showWelcome = !hasRealConversation;
  const lastAssistantMessage = useMemo(() => {
    const list = messages ?? [];
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const m: any = list[i];
      if (m?.role === "assistant") {
        return String(m?.content ?? "").trim();
      }
    }
    return "";
  }, [messages]);
  const lastUserMessageKey = useMemo(() => {
    const list = messages ?? [];
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const m: any = list[i];
      if (m?.role === "user") {
        return `${String(m?.id ?? i)}:${String(m?.content ?? "")}`;
      }
    }
    return "";
  }, [messages]);

  useEffect(() => {
    // New user turn: clear previous thinking immediately.
    setThinkingLocal("");
    setThinkingCollapsed(false);
  }, [lastUserMessageKey]);

  useEffect(() => {
    if (!isLoading) {
      setThinkingLocal("");
      return;
    }
    setThinkingLocal(thinkingStream);
  }, [thinkingStream, isLoading]);

  useEffect(() => {
    if (thinkingLocal.trim()) {
      setThinkingCollapsed(false);
      return;
    }
    if (lastAssistantMessage.length > 0) {
      setThinkingCollapsed(true);
    }
  }, [thinkingLocal, lastAssistantMessage]);

  const sendTryMessage = async (text: string) => {
    if (isLoading) return;
    await sendMessage({
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    } as any);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <Header canvasOpen={canvasOpen} onToggleCanvas={() => setCanvasOpen((o) => !o)} showCanvasToggle={true} />

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <main
          style={{
            flex: 1,
            minWidth: 0,
            borderRight: canvasOpen ? "1px solid var(--border)" : "none",
          }}
        >
          <div style={{ position: "relative", height: "100%" }}>
            <CopilotChat
              className="copilotChatMain"
              labels={{
                title: "ClawdBot",
                initial: "",
                placeholder: "Send a message...",
              }}
              suggestions="manual"
              instructions={"You are ClawdBot. Use tools actively and return concise results."}
            />
            {thinkingLocal.trim() && (
              <div
                style={{
                  position: "absolute",
                  left: 16,
                  right: 16,
                  top: 12,
                  zIndex: 6,
                  border: "1px solid var(--border)",
                  borderRadius: 12,
                  background: "var(--bg-card)",
                  boxShadow: "0 6px 20px rgba(0,0,0,0.25)",
                  overflow: "hidden",
                }}
              >
                <button
                  onClick={() => setThinkingCollapsed((v) => !v)}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    padding: "8px 12px",
                    border: "none",
                    background: "var(--bg-panel)",
                    color: "var(--text-primary)",
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: "pointer",
                  }}
                >
                  Thinking {thinkingCollapsed ? "▸" : "▾"}
                </button>
                {!thinkingCollapsed && (
                  <pre
                    style={{
                      margin: 0,
                      padding: "10px 12px",
                      maxHeight: 200,
                      overflowY: "auto",
                      whiteSpace: "pre-wrap",
                      fontSize: 12,
                      color: "var(--text-secondary)",
                      lineHeight: 1.5,
                    }}
                  >
                    <code>{thinkingLocal}</code>
                  </pre>
                )}
              </div>
            )}
            {showWelcome && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  zIndex: 5,
                  pointerEvents: "none",
                  padding: "56px 40px 180px",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div style={{ fontSize: 48, fontWeight: 700, color: "var(--text-primary)", lineHeight: 1.1 }}>
                    Hello there!
                  </div>
                  <div style={{ fontSize: 40, color: "var(--text-secondary)", marginTop: 6, lineHeight: 1.1 }}>
                    How can I help you today?
                  </div>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: 12,
                    pointerEvents: "auto",
                  }}
                >
                  {CHAT_TRY.map((text) => (
                    <button
                      key={text}
                      onClick={() => sendTryMessage(text)}
                      style={{
                        background: "rgba(17,17,20,0.95)",
                        border: "1px solid var(--border)",
                        borderRadius: 18,
                        color: "var(--text-primary)",
                        textAlign: "left",
                        padding: "18px 20px",
                        fontSize: 18,
                        lineHeight: 1.35,
                        cursor: "pointer",
                      }}
                    >
                      {text}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </main>

        {canvasOpen && (
          <aside
            style={{
              width: "46vw",
              minWidth: 420,
              maxWidth: 760,
              display: "flex",
              overflow: "hidden",
            }}
          >
            <CanvasPanel
              isOpen={true}
              onClose={() => setCanvasOpen(false)}
              showCloseButton={true}
              emptySuggestions={SUGGESTIONS}
            />
          </aside>
        )}
      </div>
    </div>
  );
}
