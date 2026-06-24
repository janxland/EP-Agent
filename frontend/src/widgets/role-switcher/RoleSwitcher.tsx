"use client";
/**
 * RoleSwitcher — 角色切换组件
 *
 * 功能：
 *   - 展示所有可用角色卡片（从 /api/roles 动态加载）
 *   - 点击切换角色（调用 /api/sessions/{id}/role）
 *   - 切换后推送欢迎语到对话框
 *   - 当前角色在顶栏以徽章形式展示
 *
 * 使用方式：
 *   <RoleSwitcher sessionId={sessionId} onRoleChange={handleRoleChange} />
 */

import { useState, useEffect, useCallback } from "react";

// ── 类型定义 ──────────────────────────────────────────────────────────────────

export interface RoleMeta {
  id: string;
  name: string;
  tagline: string;
  icon: string;
  color: string;           // Tailwind 颜色名
  capabilities: string[];
  greeting: string;
  enabled: boolean;
  domains: string[];
}

interface RoleSwitcherProps {
  sessionId: string;
  currentRoleId?: string;
  onRoleChange?: (role: RoleMeta, greeting: string) => void;
  onClose?: () => void;    // 关闭面板回调（完整模式用，不切换角色时关闭）
  compact?: boolean;       // true = 只显示当前角色徽章（顶栏模式）
}

// ── 颜色映射（Tailwind 安全类名）────────────────────────────────────────────

const COLOR_MAP: Record<string, { bg: string; border: string; text: string; badge: string; ring: string }> = {
  orange: {
    bg:     "bg-orange-50",
    border: "border-orange-200",
    text:   "text-orange-700",
    badge:  "bg-orange-100 text-orange-700",
    ring:   "ring-orange-400",
  },
  blue: {
    bg:     "bg-blue-50",
    border: "border-blue-200",
    text:   "text-blue-700",
    badge:  "bg-blue-100 text-blue-700",
    ring:   "ring-blue-400",
  },
  purple: {
    bg:     "bg-purple-50",
    border: "border-purple-200",
    text:   "text-purple-700",
    badge:  "bg-purple-100 text-purple-700",
    ring:   "ring-purple-400",
  },
  green: {
    bg:     "bg-green-50",
    border: "border-green-200",
    text:   "text-green-700",
    badge:  "bg-green-100 text-green-700",
    ring:   "ring-green-400",
  },
  pink: {
    bg:     "bg-pink-50",
    border: "border-pink-200",
    text:   "text-pink-700",
    badge:  "bg-pink-100 text-pink-700",
    ring:   "ring-pink-400",
  },
  gray: {
    bg:     "bg-gray-50",
    border: "border-gray-200",
    text:   "text-gray-500",
    badge:  "bg-gray-100 text-gray-500",
    ring:   "ring-gray-300",
  },
};

function getColor(color: string) {
  return COLOR_MAP[color] ?? COLOR_MAP.gray;
}

// ── 当前角色徽章（顶栏嵌入用）────────────────────────────────────────────────

export function RoleBadge({
  role,
  onClick,
}: {
  role: RoleMeta | null;
  onClick?: () => void;
}) {
  if (!role) return null;
  const c = getColor(role.color);
  return (
    <button
      onClick={onClick}
      className={`
        inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium
        ${c.badge} border ${c.border}
        hover:opacity-80 transition-opacity cursor-pointer select-none
      `}
      title={`当前角色：${role.name}（点击切换）`}
    >
      <span>{role.icon}</span>
      <span>{role.name}</span>
    </button>
  );
}

// ── 角色卡片 ──────────────────────────────────────────────────────────────────

function RoleCard({
  role,
  isActive,
  isLoading,
  onClick,
}: {
  role: RoleMeta;
  isActive: boolean;
  isLoading: boolean;
  onClick: () => void;
}) {
  const c = getColor(role.color);
  const disabled = !role.enabled || isLoading;

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`
        relative w-full text-left p-4 rounded-xl border-2 transition-all duration-200
        ${isActive
          ? `${c.bg} ${c.border} ring-2 ${c.ring} ring-offset-1`
          : role.enabled
            ? `bg-white border-gray-200 hover:${c.bg} hover:${c.border}`
            : "bg-gray-50 border-gray-100 opacity-50 cursor-not-allowed"
        }
      `}
    >
      {/* 激活指示器 */}
      {isActive && (
        <span className={`absolute top-2 right-2 w-2 h-2 rounded-full ${c.text.replace("text-", "bg-")} animate-pulse`} />
      )}

      {/* 图标 + 名称 */}
      <div className="flex items-center gap-2 mb-1">
        <span className="text-2xl leading-none">{role.icon}</span>
        <div>
          <div className={`font-semibold text-sm ${isActive ? c.text : "text-gray-800"}`}>
            {role.name}
          </div>
          {!role.enabled && (
            <span className="text-xs text-gray-400">即将推出</span>
          )}
        </div>
      </div>

      {/* 简介 */}
      <p className="text-xs text-gray-500 mb-2 leading-relaxed">{role.tagline}</p>

      {/* 能力标签 */}
      <div className="flex flex-wrap gap-1">
        {role.capabilities.map((cap) => (
          <span
            key={cap}
            className={`text-xs px-1.5 py-0.5 rounded-md ${isActive ? c.badge : "bg-gray-100 text-gray-500"}`}
          >
            {cap}
          </span>
        ))}
      </div>

      {/* 加载状态 */}
      {isLoading && isActive && (
        <div className="absolute inset-0 rounded-xl bg-white/60 flex items-center justify-center">
          <div className="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
        </div>
      )}
    </button>
  );
}

// ── 角色切换面板（弹出层）────────────────────────────────────────────────────

function RolePanel({
  roles,
  currentRoleId,
  switchingId,
  onSelect,
  onClose,
}: {
  roles: RoleMeta[];
  currentRoleId: string;
  switchingId: string | null;
  onSelect: (role: RoleMeta) => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4">
      {/* 遮罩 */}
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* 面板 */}
      <div className="relative z-10 w-full max-w-lg bg-white rounded-2xl shadow-2xl overflow-hidden">
        {/* 头部 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h2 className="font-semibold text-gray-900">切换专家角色</h2>
            <p className="text-xs text-gray-400 mt-0.5">选择不同专家，获得专精能力</p>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-full bg-gray-100 flex items-center justify-center text-gray-500 hover:bg-gray-200 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* 角色网格 */}
        <div className="p-4 grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[60vh] overflow-y-auto">
          {roles.map((role) => (
            <RoleCard
              key={role.id}
              role={role}
              isActive={role.id === currentRoleId}
              isLoading={switchingId === role.id}
              onClick={() => role.enabled && onSelect(role)}
            />
          ))}
        </div>

        {/* 底部提示 */}
        <div className="px-5 py-3 bg-gray-50 border-t border-gray-100">
          <p className="text-xs text-gray-400 text-center">
            切换角色不会丢失对话记录，仅改变后续处理方式
          </p>
        </div>
      </div>
    </div>
  );
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export function RoleSwitcher({
  sessionId,
  currentRoleId: externalRoleId,
  onRoleChange,
  onClose,
  compact = false,
}: RoleSwitcherProps) {
  const [roles, setRoles]               = useState<RoleMeta[]>([]);
  const [currentRoleId, setCurrentRoleId] = useState<string>(externalRoleId ?? "abc_expert");
  const [switchingId, setSwitchingId]   = useState<string | null>(null);
  const [showPanel, setShowPanel]       = useState(false);
  const [error, setError]               = useState<string | null>(null);

  // 加载角色列表
  useEffect(() => {
    fetch("/api/roles")
      .then((r) => r.json())
      .then((data) => setRoles(data.roles ?? []))
      .catch(() => setError("角色列表加载失败"));
  }, []);

  // 从 session 恢复当前角色
  useEffect(() => {
    if (!sessionId || externalRoleId) return;
    fetch(`/api/sessions/${sessionId}/role`)
      .then((r) => r.json())
      .then((data) => { if (data.role_id) setCurrentRoleId(data.role_id); })
      .catch(() => {});
  }, [sessionId, externalRoleId]);

  // 同步外部 roleId
  useEffect(() => {
    if (externalRoleId) setCurrentRoleId(externalRoleId);
  }, [externalRoleId]);

  const handleSelect = useCallback(async (role: RoleMeta) => {
    if (role.id === currentRoleId) { setShowPanel(false); return; }
    if (!role.enabled) return;
    setSwitchingId(role.id);
    setError(null);
    try {
      const res = await fetch(`/api/sessions/${sessionId}/role`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ role_id: role.id }),
      });
      if (!res.ok) {
        let errMsg = `HTTP ${res.status}`;
        try {
          const errData = await res.json();
          errMsg = errData.detail ?? errData.message ?? errMsg;
        } catch {
          errMsg = (await res.text()) || errMsg;
        }
        throw new Error(errMsg);
      }
      const data = await res.json();
      setCurrentRoleId(role.id);
      setShowPanel(false);
      onRoleChange?.(role, data.greeting ?? "");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(`切换失败：${msg}`);
    } finally {
      setSwitchingId(null);
    }
  }, [sessionId, currentRoleId, onRoleChange]);

  const currentRole = roles.find((r) => r.id === currentRoleId) ?? null;

  // ── compact 模式：只显示徽章 ──────────────────────────────────────────────
  if (compact) {
    return (
      <>
        <RoleBadge role={currentRole} onClick={() => setShowPanel(true)} />
        {showPanel && (
          <RolePanel
            roles={roles}
            currentRoleId={currentRoleId}
            switchingId={switchingId}
            onSelect={handleSelect}
            onClose={() => setShowPanel(false)}
          />
        )}
      </>
    );
  }

  // ── 完整模式：弹出面板（ChatPanel 使用此模式）──────────────────────────
  return (
    <div className="w-full">
      {/* 错误提示 */}
      {error && (
        <div className="mb-3 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600 flex items-start gap-2">
          <span className="flex-1">{error}</span>
          <button
            onClick={() => setError(null)}
            className="shrink-0 text-red-400 hover:text-red-600"
          >✕</button>
        </div>
      )}
      {/* 弹出面板 */}
      <RolePanel
        roles={roles}
        currentRoleId={currentRoleId}
        switchingId={switchingId}
        onSelect={handleSelect}
        onClose={() => onClose?.()}
      />
    </div>
  );
}

export default RoleSwitcher;
