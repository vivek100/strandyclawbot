"use client";

import { useEffect, useMemo, useState } from "react";
import { useCoAgent, useCopilotChatInternal } from "@copilotkit/react-core";

interface CanvasContent {
  type: "code" | "text" | "file" | "output";
  title: string;
  content: string;
  language?: string;
  timestamp?: string;
}

interface AgentState {
  canvas_items?: CanvasContent[];
  active_task?: string;
  last_command?: string;
}

interface CanvasPanelProps {
  isOpen: boolean;
  onClose: () => void;
  showCloseButton?: boolean;
  emptySuggestions?: string[];
}

interface ToolStreamCall {
  id: string;
  name: string;
  args: string;
  result?: string;
  status: "running" | "done";
}

interface SkillItem {
  name: string;
  path: string;
  summary: string;
  preview?: string;
  source?: "local" | "global";
}

interface SkillsResponse {
  workspace_id: string;
  local: SkillItem[];
  global: SkillItem[];
  effective: SkillItem[];
}

function LanguageBadge({ lang }: { lang?: string }) {
  if (!lang) return null;
  const colors: Record<string, string> = {
    python: "#3776ab",
    javascript: "#f7df1e",
    typescript: "#3178c6",
    bash: "#4eaa25",
    sh: "#4eaa25",
    json: "#e05c3a",
    html: "#e34c26",
    css: "#264de4",
  };
  const color = colors[lang.toLowerCase()] || "#8b8b99";
  return (
    <span
      style={{
        background: `${color}22`,
        border: `1px solid ${color}44`,
        color,
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontFamily: "monospace",
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
      }}
    >
      {lang}
    </span>
  );
}

function ContentTypeIcon({ type }: { type: CanvasContent["type"] }) {
  const icons = { code: "⌨", text: "📄", file: "📁", output: "▶" };
  return <span style={{ fontSize: 14 }}>{icons[type] || "📄"}</span>;
}

export function CanvasPanel({
  isOpen,
  onClose,
  showCloseButton = true,
  emptySuggestions = [],
}: CanvasPanelProps) {
  const [activeTab, setActiveTab] = useState<number>(0);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [skills, setSkills] = useState<SkillsResponse | null>(null);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillsError, setSkillsError] = useState<string | null>(null);

  const { state } = useCoAgent<AgentState>({
    name: "clawdbot",
    initialState: { canvas_items: [], active_task: undefined, last_command: undefined },
  });
  const { messages } = useCopilotChatInternal();

  const streamedToolCalls = useMemo<ToolStreamCall[]>(() => {
    const toolResultsById = new Map<string, string>();

    for (const message of messages ?? []) {
      if ((message as any)?.role === "tool" && (message as any)?.toolCallId) {
        const toolCallId = String((message as any).toolCallId);
        const content = String((message as any)?.content ?? "");
        toolResultsById.set(toolCallId, content);
      }
    }

    const calls: ToolStreamCall[] = [];
    for (const message of messages ?? []) {
      if ((message as any)?.role !== "assistant") continue;
      const toolCalls = ((message as any)?.toolCalls ?? []) as any[];
      if (!Array.isArray(toolCalls)) continue;

      for (const tc of toolCalls) {
        const toolCallId = String(tc?.id ?? "");
        const toolName = String(tc?.function?.name ?? "tool");
        const args = String(tc?.function?.arguments ?? "{}");
        const result = toolResultsById.get(toolCallId);
        calls.push({
          id: toolCallId,
          name: toolName,
          args,
          result,
          status: result ? "done" : "running",
        });
      }
    }

    return calls.slice(-12);
  }, [messages]);

  useEffect(() => {
    const all = messages ?? [];
    console.debug("[Canvas] messages snapshot", {
      totalMessages: all.length,
      rolesTail: all.slice(-6).map((m: any) => m?.role),
      toolCallCount: streamedToolCalls.length,
    });
  }, [messages, streamedToolCalls.length]);

  useEffect(() => {
    if (streamedToolCalls.length === 0) return;
    const latest = streamedToolCalls[streamedToolCalls.length - 1];
    console.debug("[Canvas] tool stream", {
      total: streamedToolCalls.length,
      latestTool: latest.name,
      latestStatus: latest.status,
      latestId: latest.id,
    });
  }, [streamedToolCalls]);

  const stateItems = state?.canvas_items ?? [];
  const items = stateItems.slice(-20);
  const activeTask = state?.active_task;

  const loadSkills = async () => {
    setSkillsLoading(true);
    setSkillsError(null);
    try {
      const res = await fetch("/api/skills?thread_id=default", { method: "GET", cache: "no-store" });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const payload = (await res.json()) as SkillsResponse;
      setSkills(payload);
    } catch (err: any) {
      setSkillsError(String(err?.message || err || "unknown error"));
    } finally {
      setSkillsLoading(false);
    }
  };

  useEffect(() => {
    loadSkills();
  }, []);

  const handleCopy = (content: string, idx: number) => {
    navigator.clipboard.writeText(content);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 1500);
  };

  if (!isOpen) return null;

  return (
    <div
      className="canvas-panel fade-in"
      style={{
        width: "100%",
        minWidth: 0,
        maxWidth: "none",
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "14px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 12,
          background: "var(--bg-panel)",
        }}
      >
        <span style={{ fontSize: 16 }}>🦞</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>Canvas</div>
          {activeTask && (
            <div style={{ fontSize: 11, color: "var(--accent)", marginTop: 2, display: "flex", alignItems: "center", gap: 4 }}>
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: "var(--accent)",
                  animation: "pulse-dot 1.4s ease-in-out infinite",
                  display: "inline-block",
                }}
              />
              {activeTask}
            </div>
          )}
        </div>
        {showCloseButton && (
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              color: "var(--text-muted)",
              fontSize: 18,
              lineHeight: 1,
              padding: "4px 6px",
              borderRadius: 4,
              transition: "color 0.15s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "var(--text-primary)")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-muted)")}
            title="Close canvas"
          >
            ×
          </button>
        )}
      </div>

      {items.length > 1 && (
        <div
          style={{
            display: "flex",
            gap: 0,
            overflowX: "auto",
            borderBottom: "1px solid var(--border)",
            background: "var(--bg-panel)",
          }}
        >
          {items.map((item, idx) => (
            <button
              key={idx}
              onClick={() => setActiveTab(idx)}
              style={{
                background: activeTab === idx ? "var(--bg-secondary)" : "none",
                border: "none",
                borderBottom: activeTab === idx ? "2px solid var(--accent)" : "2px solid transparent",
                cursor: "pointer",
                padding: "8px 14px",
                color: activeTab === idx ? "var(--text-primary)" : "var(--text-muted)",
                fontSize: 12,
                whiteSpace: "nowrap",
                display: "flex",
                alignItems: "center",
                gap: 6,
                transition: "all 0.15s",
              }}
            >
              <ContentTypeIcon type={item.type} />
              {item.title.length > 20 ? item.title.slice(0, 20) + "..." : item.title}
            </button>
          ))}
        </div>
      )}

      <div style={{ flex: 1, overflowY: "auto", padding: 16 }} className="canvas-content">
        <SkillsSection
          skills={skills}
          loading={skillsLoading}
          error={skillsError}
          onRefresh={loadSkills}
        />
        <ToolCallsSection calls={streamedToolCalls} />
        {items.length === 0 ? (
          <EmptyState suggestions={emptySuggestions} />
        ) : (
          <CanvasItem
            item={items[activeTab] ?? items[0]}
            idx={activeTab}
            copied={copiedIdx === activeTab}
            onCopy={handleCopy}
          />
        )}
      </div>
    </div>
  );
}

function SourceBadge({ source }: { source?: "local" | "global" }) {
  if (!source) return null;
  const isLocal = source === "local";
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        color: isLocal ? "#22c55e" : "#60a5fa",
        border: `1px solid ${isLocal ? "#22c55e44" : "#60a5fa44"}`,
        background: isLocal ? "#22c55e1a" : "#60a5fa1a",
        borderRadius: 999,
        padding: "2px 6px",
      }}
    >
      {source}
    </span>
  );
}

function SkillList({ title, items }: { title: string; items: SkillItem[] }) {
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)" }}>
      <div
        style={{
          padding: "8px 10px",
          borderBottom: "1px solid var(--border-subtle)",
          fontSize: 12,
          color: "var(--text-secondary)",
          fontWeight: 600,
        }}
      >
        {title} ({items.length})
      </div>
      <div style={{ maxHeight: 180, overflowY: "auto" }}>
        {items.length === 0 ? (
          <div style={{ padding: 10, fontSize: 12, color: "var(--text-muted)" }}>No skills</div>
        ) : (
          items.map((skill) => (
            <details
              key={`${skill.source || "effective"}-${skill.name}-${skill.path}`}
              style={{
                padding: "10px 10px",
                borderTop: "1px solid var(--border-subtle)",
                display: "grid",
                gap: 4,
              }}
            >
              <summary style={{ cursor: "pointer", listStyle: "none" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>{skill.name}</span>
                  <SourceBadge source={skill.source} />
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{skill.summary}</div>
              </summary>
              <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "monospace", marginTop: 6 }}>{skill.path}</div>
              {skill.preview && (
                <pre
                  style={{
                    margin: 0,
                    marginTop: 6,
                    padding: "8px 10px",
                    borderRadius: 8,
                    background: "var(--bg-primary)",
                    border: "1px solid var(--border-subtle)",
                    fontSize: 11,
                    whiteSpace: "pre-wrap",
                    color: "var(--text-secondary)",
                    maxHeight: 180,
                    overflowY: "auto",
                  }}
                >
                  <code>{skill.preview}</code>
                </pre>
              )}
            </details>
          ))
        )}
      </div>
    </div>
  );
}

function SkillsSection({
  skills,
  loading,
  error,
  onRefresh,
}: {
  skills: SkillsResponse | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div
      style={{
        marginBottom: 16,
        border: "1px solid var(--border)",
        background: "var(--bg-panel)",
        borderRadius: 10,
        padding: 10,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", marginBottom: 8, gap: 8 }}>
        <button
          onClick={() => setCollapsed((v) => !v)}
          style={{
            border: "none",
            background: "transparent",
            color: "var(--text-primary)",
            fontSize: 12,
            fontWeight: 700,
            padding: 0,
            cursor: "pointer",
          }}
          title={collapsed ? "Expand skills" : "Collapse skills"}
        >
          Skills {collapsed ? "▸" : "▾"}
        </button>
        {skills?.workspace_id && (
          <div style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "monospace" }}>
            workspace: {skills.workspace_id}
          </div>
        )}
        <button
          onClick={onRefresh}
          style={{
            marginLeft: "auto",
            border: "1px solid var(--border)",
            background: "var(--bg-card)",
            color: "var(--text-secondary)",
            borderRadius: 6,
            padding: "2px 8px",
            fontSize: 11,
            cursor: "pointer",
          }}
        >
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>
      {error && <div style={{ fontSize: 11, color: "#f87171", marginBottom: 8 }}>Error: {error}</div>}
      {!collapsed && (
        <div style={{ display: "grid", gap: 8 }}>
          <SkillList title="Effective (local overrides global)" items={skills?.effective ?? []} />
          <SkillList title="Local Skills" items={skills?.local ?? []} />
          <SkillList title="Global Skills" items={skills?.global ?? []} />
        </div>
      )}
    </div>
  );
}

function ToolCallsSection({ calls }: { calls: ToolStreamCall[] }) {
  if (calls.length === 0) return null;

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 8 }}>Live Tool Calls</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {calls.map((call) => (
          <details
            key={call.id}
            open={call.status === "running"}
            style={{
              border: "1px solid var(--border)",
              borderRadius: 8,
              background: "var(--bg-card)",
              padding: "8px 10px",
            }}
          >
            <summary
              style={{
                cursor: "pointer",
                color: "var(--text-primary)",
                fontSize: 12,
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              {call.name}
              <span style={{ color: call.status === "done" ? "#22c55e" : "var(--accent)", fontWeight: 500 }}>
                {call.status === "done" ? "done" : "running"}
              </span>
            </summary>
            <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
              <div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>Input</div>
                <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap", color: "var(--text-primary)" }}>
                  <code>{call.args || "{}"}</code>
                </pre>
              </div>
              <div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>Output</div>
                <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap", color: "var(--text-primary)" }}>
                  <code>{call.result || "(streaming...)"}</code>
                </pre>
              </div>
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}

function CanvasItem({
  item,
  idx,
  copied,
  onCopy,
}: {
  item: CanvasContent;
  idx: number;
  copied: boolean;
  onCopy: (c: string, i: number) => void;
}) {
  return (
    <div className="fade-in">
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <ContentTypeIcon type={item.type} />
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", flex: 1 }}>{item.title}</span>
        <LanguageBadge lang={item.language} />
        {item.timestamp && <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{item.timestamp}</span>}
        <button
          onClick={() => onCopy(item.content, idx)}
          style={{
            background: copied ? "var(--accent-dim)" : "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            color: copied ? "var(--accent)" : "var(--text-muted)",
            cursor: "pointer",
            fontSize: 11,
            padding: "3px 10px",
            transition: "all 0.15s",
          }}
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>

      {item.type === "code" || item.type === "output" ? (
        <pre
          style={{
            background: "var(--bg-primary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "14px 16px",
            overflowX: "auto",
            fontSize: 13,
            lineHeight: 1.65,
            color: "var(--text-primary)",
          }}
        >
          <code>{item.content}</code>
        </pre>
      ) : (
        <div
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 8,
            padding: 16,
            fontSize: 13,
            lineHeight: 1.7,
            color: "var(--text-primary)",
            whiteSpace: "pre-wrap",
          }}
        >
          {item.content}
        </div>
      )}
    </div>
  );
}

function EmptyState({ suggestions }: { suggestions: string[] }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        gap: 12,
        color: "var(--text-muted)",
        textAlign: "center",
        padding: 24,
      }}
    >
      <div style={{ fontSize: 44, opacity: 0.35 }}>🦞</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: "var(--text-primary)" }}>Canvas</div>
      <div style={{ fontSize: 13, maxWidth: 560 }}>
        Ask ClawdBot to run commands, write files, or inspect code. Tool outputs will appear here.
      </div>

      {suggestions.length > 0 && (
        <div
          style={{
            marginTop: 10,
            width: "100%",
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 10,
            textAlign: "left",
          }}
        >
          {suggestions.map((s, i) => (
            <div
              key={i}
              style={{
                background: "var(--bg-card)",
                border: "1px solid var(--border-subtle)",
                borderRadius: 10,
                padding: "11px 14px",
                fontSize: 12,
                color: "var(--text-secondary)",
                lineHeight: 1.5,
              }}
            >
              {s}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
