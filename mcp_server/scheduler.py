"""
scheduler.py - 定时任务调度器
功能：
  - 每日交易报告自动推送（美股收盘后）
  - 市场情绪监控（每小时）
  - 持仓止损扫描（实时）
依赖：asyncio, schedule（可选）
"""
import asyncio
from datetime import datetime, time as dt_time, timedelta
from typing import Callable, Optional
import json


# ============================================================
# 定时任务管理器
# ============================================================

class TaskScheduler:
    """异步定时任务调度器"""
    
    def __init__(self):
        self.tasks = []
        self.running = False
    
    async def daily_at(self, hour: int, minute: int, func: Callable, *args, **kwargs):
        """每天固定时间执行任务"""
        while self.running:
            now = datetime.now()
            target_time = dt_time(hour, minute, 0)
            
            # 计算距离下次执行的时间
            if now.time() >= target_time:
                # 今天已经过了执行时间，等到明天
                tomorrow = now + timedelta(days=1)
                next_run = datetime.combine(tomorrow.date(), target_time)
            else:
                # 今天还没到执行时间
                next_run = datetime.combine(now.date(), target_time)
            
            # 等待到执行时间
            wait_seconds = (next_run - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            
            # 执行任务
            if self.running:
                try:
                    if asyncio.iscoroutinefunction(func):
                        await func(*args, **kwargs)
                    else:
                        func(*args, **kwargs)
                except Exception as e:
                    print(f"Task error: {e}")
    
    async def every(self, seconds: int, func: Callable, *args, **kwargs):
        """每隔N秒执行任务"""
        while self.running:
            try:
                if asyncio.iscoroutinefunction(func):
                    await func(*args, **kwargs)
                else:
                    func(*args, **kwargs)
            except Exception as e:
                print(f"Task error: {e}")
            
            await asyncio.sleep(seconds)
    
    def start(self):
        """启动调度器"""
        self.running = True
    
    def stop(self):
        """停止调度器"""
        self.running = False


# ============================================================
# 每日报告生成器
# ============================================================

class DailyReportGenerator:
    """每日交易报告生成器"""
    
    @staticmethod
    async def generate_report() -> dict:
        """生成每日报告"""
        from trade_journal import get_journal
        from datetime import date
        
        journal = get_journal()
        
        # 获取今日交易统计
        today = date.today().isoformat()
        stats = journal.get_statistics(
            start_date=today,
            end_date=today
        )
        
        # 获取今日交易列表
        trades = journal.query_trades(
            start_date=today,
            end_date=today,
            limit=20
        )
        
        # 计算总资产（需要从 MCP 获取）
        total_assets = 0
        try:
            # 这里需要调用 get_account_info
            # 由于在后台任务中，我们简化处理
            pass
        except:
            pass
        
        # 生成摘要
        summary_parts = []
        
        if stats["total_trades"] == 0:
            summary_parts.append("今日无交易")
        else:
            # 盈亏情况
            total_pnl = stats["total_profit"] - stats["total_loss"]
            if total_pnl > 0:
                summary_parts.append(f"今日盈利 ${total_pnl:.2f}")
            elif total_pnl < 0:
                summary_parts.append(f"今日亏损 ${abs(total_pnl):.2f}")
            else:
                summary_parts.append("今日盈亏平衡")
            
            # 胜率
            if stats["win_rate"] >= 60:
                summary_parts.append(f"胜率 {stats['win_rate']:.1f}%（表现优异）")
            elif stats["win_rate"] >= 40:
                summary_parts.append(f"胜率 {stats['win_rate']:.1f}%（正常水平）")
            else:
                summary_parts.append(f"胜率 {stats['win_rate']:.1f}%（需要改进）")
            
            # 交易次数
            summary_parts.append(f"完成 {stats['total_trades']} 笔交易")
        
        # 情绪分析
        emotion_issues = []
        for item in stats.get("emotion_distribution", []):
            tag = item["tag"]
            count = item["count"]
            if tag in ["恐慌买入", "FOMO追涨", "贪婪持有", "恐惧卖出", "报复性交易"] and count > 0:
                emotion_issues.append(f"{tag} {count}次")
        
        if emotion_issues:
            summary_parts.append("⚠️ 情绪化交易：" + "、".join(emotion_issues))
        
        summary = "；".join(summary_parts)
        
        return {
            "total_assets": total_assets,
            "daily_pnl": total_pnl if stats["total_trades"] > 0 else 0,
            "daily_pnl_pct": (total_pnl / total_assets * 100) if total_assets > 0 else 0,
            "position_count": 0,  # 需要从持仓获取
            "trade_count": stats["total_trades"],
            "win_rate": stats["win_rate"],
            "summary": summary,
            "trades": [
                {
                    "symbol": t.symbol,
                    "action": t.action,
                    "quantity": t.quantity,
                    "price": f"${t.price:.2f}",
                    "pnl": f"{t.pnl_pct:+.2f}%" if t.pnl_pct else "持仓中",
                }
                for t in trades[:5]  # 只显示前5笔
            ]
        }
    
    @staticmethod
    async def send_daily_report():
        """生成并发送每日报告"""
        try:
            # 生成报告
            report = await DailyReportGenerator.generate_report()
            
            # 发送到飞书/钉钉
            from feishu_bot import get_notification_manager
            notification = get_notification_manager()
            
            results = notification.send_daily_report(report)
            
            success_count = sum(1 for v in results.values() if v)
            if success_count > 0:
                print(f"[OK] 每日报告已发送到 {success_count} 个机器人")
            else:
                print("[ERR] 每日报告发送失败，请检查配置")
        
        except Exception as e:
            print(f"生成每日报告失败: {e}")


# ============================================================
# 市场情绪监控
# ============================================================

class SentimentMonitor:
    """市场情绪监控"""
    
    def __init__(self):
        self.last_alert_time = None
        self.alert_cooldown = 3600 * 4  # 4小时冷却期，避免频繁推送
    
    async def check_sentiment(self):
        """检查市场情绪"""
        try:
            from market_analysis import get_market_sentiment
            from feishu_bot import get_notification_manager
            from datetime import datetime
            
            sentiment = get_market_sentiment()
            notification = get_notification_manager()
            
            # 检查是否需要发送告警
            now = datetime.now()
            
            # 如果在冷却期内，不发送
            if self.last_alert_time:
                elapsed = (now - self.last_alert_time).total_seconds()
                if elapsed < self.alert_cooldown:
                    return
            
            # 极度恐慌（< 20）
            if sentiment['value'] <= 20:
                notification.send_alert(
                    alert_type="warning",
                    message=f"市场情绪极度恐慌（{sentiment['value']}/100）",
                    details={
                        "情绪等级": sentiment['level'],
                        "建议": "历史上常是买入机会，可关注优质标的分批建仓",
                        "风险提示": "仍需设置止损，控制仓位"
                    }
                )
                self.last_alert_time = now
            
            # 极度贪婪（> 80）
            elif sentiment['value'] >= 80:
                notification.send_alert(
                    alert_type="warning",
                    message=f"市场情绪极度贪婪（{sentiment['value']}/100）",
                    details={
                        "情绪等级": sentiment['level'],
                        "建议": "历史上常是卖出信号，建议锁定利润",
                        "风险提示": "警惕市场回调，降低仓位"
                    }
                )
                self.last_alert_time = now
        
        except Exception as e:
            print(f"市场情绪监控失败: {e}")


# ============================================================
# 持仓止损扫描
# ============================================================

class StopLossScanner:
    """持仓止损扫描"""
    
    async def scan_positions(self):
        """扫描持仓止损"""
        try:
            from risk_manager import get_manager
            from feishu_bot import get_notification_manager
            
            # 这里需要获取持仓数据
            # 由于在后台任务中，我们简化处理
            # 实际应该调用 get_positions
            
            notification = get_notification_manager()
            
            # 示例：检测到止损触发时推送
            # 实际实现需要完整的持仓数据
            
        except Exception as e:
            print(f"止损扫描失败: {e}")


# ============================================================
# 全局调度器
# ============================================================

_scheduler = None
_sentiment_monitor = None

def get_scheduler() -> TaskScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler

def get_sentiment_monitor() -> SentimentMonitor:
    global _sentiment_monitor
    if _sentiment_monitor is None:
        _sentiment_monitor = SentimentMonitor()
    return _sentiment_monitor


# ============================================================
# 启动所有定时任务
# ============================================================

async def start_all_tasks():
    """启动所有定时任务"""
    scheduler = get_scheduler()
    sentiment_monitor = get_sentiment_monitor()
    
    scheduler.start()
    
    print("=" * 60)
    print("定时任务调度器已启动")
    print("=" * 60)
    
    # 任务1：每天下午4点推送每日报告（美股收盘后）
    print("[OK] 每日报告推送：每天 16:00")
    task1 = asyncio.create_task(
        scheduler.daily_at(16, 0, DailyReportGenerator.send_daily_report)
    )
    
    # 任务2：每小时检查市场情绪
    print("[OK] 市场情绪监控：每小时")
    task2 = asyncio.create_task(
        scheduler.every(3600, sentiment_monitor.check_sentiment)
    )
    
    # 任务3：每小时推送市场分析 + RXRX.US / HOOD.US 股票分析到飞书
    async def _hourly_market_stocks_to_feishu():
        try:
            from hourly_market_stocks_to_feishu import run_once
            await asyncio.to_thread(run_once)
        except Exception as e:
            print(f"每小时市场/股票推送失败: {e}")
    print("[OK] 市场分析+RXRX/HOOD推送到飞书：每小时")
    task3 = asyncio.create_task(
        scheduler.every(3600, _hourly_market_stocks_to_feishu)
    )
    
    # 任务4：每5分钟扫描持仓止损（可选）
    # scanner = StopLossScanner()
    # task4 = asyncio.create_task(
    #     scheduler.every(300, scanner.scan_positions)
    # )
    
    print("=" * 60)
    print()
    
    # 保持任务运行
    await asyncio.gather(task1, task2, task3)


# ============================================================
# 测试代码
# ============================================================

if __name__ == "__main__":
    print("定时任务调度器测试")
    print()
    print("配置项：")
    print("- 每日报告：每天 16:00")
    print("- 市场情绪：每小时")
    print()
    print("按 Ctrl+C 停止")
    print()
    
    try:
        asyncio.run(start_all_tasks())
    except KeyboardInterrupt:
        print("\n调度器已停止")