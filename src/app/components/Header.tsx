"use client";

interface HeaderProps {
  canvasOpen: boolean;
  onToggleCanvas: () => void;
  showCanvasToggle?: boolean;
}

export function Header({ canvasOpen, onToggleCanvas, showCanvasToggle = true }: HeaderProps) {
  return (
    <header
      style={{
        height: 52,
        background: "var(--bg-panel)",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        alignItems: "center",
        padding: "0 20px",
        gap: 12,
        flexShrink: 0,
        zIndex: 10,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 20 }}>🦞</span>
        <span
          style={{
            fontSize: 15,
            fontWeight: 700,
            color: "var(--text-primary)",
            letterSpacing: "-0.02em",
          }}
        >
          Clawd<span style={{ color: "var(--accent)" }}>Bot</span>
        </span>
      </div>

      <div
        style={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: "#22c55e",
          boxShadow: "0 0 6px #22c55e88",
          marginLeft: 2,
        }}
        title="Agent online"
      />

      <div style={{ flex: 1 }} />

      {showCanvasToggle && (
        <button
          onClick={onToggleCanvas}
          title={canvasOpen ? "Close canvas" : "Open canvas"}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 7,
            background: canvasOpen ? "var(--accent-dim)" : "var(--bg-card)",
            border: `1px solid ${canvasOpen ? "rgba(224,92,58,0.35)" : "var(--border)"}`,
            borderRadius: 7,
            color: canvasOpen ? "var(--accent)" : "var(--text-secondary)",
            cursor: "pointer",
            fontSize: 12,
            fontWeight: 500,
            padding: "6px 12px",
            transition: "all 0.15s",
          }}
          onMouseEnter={(e) => {
            if (!canvasOpen) {
              e.currentTarget.style.borderColor = "var(--accent)";
              e.currentTarget.style.color = "var(--text-primary)";
            }
          }}
          onMouseLeave={(e) => {
            if (!canvasOpen) {
              e.currentTarget.style.borderColor = "var(--border)";
              e.currentTarget.style.color = "var(--text-secondary)";
            }
          }}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <rect x="1" y="1" width="6" height="14" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
            <rect x="9" y="1" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
            <rect x="9" y="9" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
          </svg>
          Canvas
        </button>
      )}

      {showCanvasToggle && (
        <kbd
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            color: "var(--text-muted)",
            fontSize: 10,
            padding: "2px 6px",
            fontFamily: "monospace",
          }}
        >
          Ctrl+K
        </kbd>
      )}
    </header>
  );
}
