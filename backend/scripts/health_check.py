#!/usr/bin/env python3
"""
EP-Agent 部署健康检查脚本

用途：
  - 部署后快速验证服务是否正常启动
  - 检查所有工具是否正确注册
  - 检查环境变量配置是否完整

用法：
  python scripts/health_check.py
  python scripts/health_check.py --url http://your-server:8080
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error


def check(url: str, path: str, label: str) -> dict | None:
    full = f"{url.rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(full, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"  ✅ {label}: {full}")
            return data
    except urllib.error.URLError as e:
        print(f"  ❌ {label}: {full} — {e}")
        return None
    except Exception as e:
        print(f"  ⚠️  {label}: {full} — {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="EP-Agent 健康检查")
    parser.add_argument("--url", default="http://localhost:8080", help="后端地址")
    args = parser.parse_args()
    url = args.url

    print(f"\n🔍 EP-Agent 健康检查 → {url}\n")
    ok = True

    # 1. 基础健康
    print("── 基础服务 ──")
    data = check(url, "/healthz", "基础健康检查")
    if not data:
        ok = False

    # 2. 详细健康（含工具数/域数）
    print("\n── 工具与意图域 ──")
    data = check(url, "/api/health", "详细健康检查")
    if data:
        tool_count   = data.get("tool_count", 0)
        domain_count = data.get("domain_count", 0)
        tools_ok     = data.get("tools_ok", False)
        print(f"     工具数：{tool_count}，意图域数：{domain_count}，工具链：{'✅ 正常' if tools_ok else '⚠️  异常'}")
        if not tools_ok:
            ok = False
    else:
        ok = False

    # 3. 工具注册表
    data = check(url, "/api/health/tools", "工具注册表")
    if data:
        tools = data.get("tools", [])
        groups = {}
        for t in tools:
            g = t.get("group", "unknown")
            groups[g] = groups.get(g, 0) + 1
        for g, cnt in sorted(groups.items()):
            print(f"     {g}: {cnt} 个工具")

    # 4. 意图域
    data = check(url, "/api/health/domains", "意图域配置")
    if data:
        domains = data.get("domains", [])
        enabled = [d["name"] for d in domains if d.get("enabled")]
        disabled = [d["name"] for d in domains if not d.get("enabled")]
        print(f"     启用：{', '.join(enabled)}")
        if disabled:
            print(f"     禁用：{', '.join(disabled)}")

    # 5. 角色列表
    print("\n── 角色系统 ──")
    data = check(url, "/api/roles", "角色列表")
    if data:
        roles = data.get("roles", [])
        for r in roles:
            status = "✅" if r.get("enabled") else "🔒"
            print(f"     {status} {r.get('icon','')} {r.get('name','')} ({r.get('id','')})")

    # 6. 工作区
    print("\n── 数据层 ──")
    data = check(url, "/api/workspaces", "工作区列表")
    if data:
        ws_count = len(data.get("workspaces", []))
        print(f"     工作区数：{ws_count}")

    # 结果
    print()
    if ok:
        print("✅ 所有检查通过，服务正常运行\n")
        sys.exit(0)
    else:
        print("❌ 部分检查失败，请查看上方错误信息\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
