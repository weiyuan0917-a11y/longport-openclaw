"""
MCP 连接诊断脚本
快速检查 MCP Server 是否能正常启动
"""
import sys
import os

print("=" * 60)
print("MCP Server 诊断工具 v1.0")
print("=" * 60)

# 1. 检查文件是否存在
print("\n[1/5] 检查文件完整性...")
mcp_dir = r"D:\longport-openclaw\mcp_server"
required_files = [
    "longport_mcp_server.py",
    "trade_journal.py",
    "alert_manager.py",
    "mcp_extensions.py",
    "backtest_engine.py",
    "strategies.py",
    "risk_manager.py",
]

missing = []
for f in required_files:
    path = os.path.join(mcp_dir, f)
    if os.path.exists(path):
        print(f"  ✅ {f}")
    else:
        print(f"  ❌ {f} (缺失)")
        missing.append(f)

if missing:
    print(f"\n⚠️  缺少 {len(missing)} 个文件，请先复制到 mcp_server 目录")
    sys.exit(1)

# 2. 检查 Python 导入
print("\n[2/5] 检查 Python 模块导入...")
sys.path.insert(0, mcp_dir)

try:
    from risk_manager import get_manager
    print("  ✅ risk_manager")
except Exception as e:
    print(f"  ❌ risk_manager: {e}")

try:
    from backtest_engine import BacktestEngine
    print("  ✅ backtest_engine")
except Exception as e:
    print(f"  ❌ backtest_engine: {e}")

try:
    from strategies import get_strategy
    print("  ✅ strategies")
except Exception as e:
    print(f"  ❌ strategies: {e}")

try:
    from trade_journal import get_journal
    print("  ✅ trade_journal")
except Exception as e:
    print(f"  ❌ trade_journal: {e}")

try:
    from alert_manager import get_alert_manager
    print("  ✅ alert_manager")
except Exception as e:
    print(f"  ❌ alert_manager: {e}")

try:
    from mcp_extensions import TOOL_DISPATCH
    print(f"  ✅ mcp_extensions ({len(TOOL_DISPATCH)} 个工具)")
except Exception as e:
    print(f"  ❌ mcp_extensions: {e}")

# 3. 检查 MCP 模块
print("\n[3/5] 检查 MCP 依赖...")
try:
    import mcp.server
    import mcp.server.stdio
    import mcp.types as types
    print("  ✅ mcp 库已安装")
except ImportError as e:
    print(f"  ❌ mcp 库未安装: {e}")
    print("     请运行: pip install mcp")

# 4. 检查 LongPort 配置
print("\n[4/5] 检查 LongPort 凭证...")
env_keys = ["LONGPORT_APP_KEY", "LONGPORT_APP_SECRET", "LONGPORT_ACCESS_TOKEN"]
env_ok = all(os.getenv(k) for k in env_keys)

if env_ok:
    print("  ✅ 环境变量已配置")
else:
    print("  ⚠️  环境变量未配置，尝试从 live_settings.py 加载...")
    try:
        sys.path.insert(0, r"D:\longport-openclaw")
        from config.live_settings import live_settings
        if hasattr(live_settings, 'LONGPORT_APP_KEY'):
            print("  ✅ live_settings.py 配置已找到")
        else:
            print("  ❌ live_settings.py 配置不完整")
    except Exception as e:
        print(f"  ❌ 无法加载配置: {e}")

# 5. 模拟启动测试
print("\n[5/5] 模拟启动测试...")
print("  尝试导入 longport_mcp_server.py...")

try:
    # 不实际运行，只检查语法
    import ast
    with open(os.path.join(mcp_dir, "longport_mcp_server.py"), "r", encoding="utf-8") as f:
        code = f.read()
    ast.parse(code)
    print("  ✅ Python 语法检查通过")
except SyntaxError as e:
    print(f"  ❌ 语法错误: {e}")
    sys.exit(1)

# 总结
print("\n" + "=" * 60)
print("诊断完成！")
print("=" * 60)

print("\n📋 下一步操作：")
print("1. 用修复后的文件替换原 longport_mcp_server.py")
print("2. 重启 Claude Desktop")
print("3. 如果仍无法连接，查看日志中的具体错误信息")

print("\n💡 常见问题：")
print("- 如果看到 'Field required' 错误 → 使用修复版本（已修复）")
print("- 如果看到 'not valid JSON' 错误 → 检查是否有 print/stderr 输出")
print("- 如果模块导入失败 → 确保所有文件都在 mcp_server 目录")

print("\n✅ 修复版本文件：longport_mcp_server_v4.1_FIXED.py")