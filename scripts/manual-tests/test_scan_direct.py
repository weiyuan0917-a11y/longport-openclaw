"""直接测试 AutoTrader 扫描"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.main import auto_trader

print("测试 AutoTrader 扫描...")
print(f"配置: {auto_trader.get_config()}")

try:
    result = auto_trader.run_scan_once()
    print(f"扫描成功!")
    print(f"强势股数量: {result.get('strong_count')}")
    print(f"创建信号: {result.get('created_signals')}")
    print(f"执行信号: {result.get('executed_signals')}")
    print(f"失败信号: {result.get('failed_signals')}")
    print(f"强势股列表:")
    for stock in result.get('strong_stocks', []):
        print(f"  {stock['symbol']}: 强度={stock['strength_score']}")
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
