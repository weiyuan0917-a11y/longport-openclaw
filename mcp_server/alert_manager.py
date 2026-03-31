"""
alert_manager.py - 智能告警系统
功能：
  - 价格突破/跌破告警
  - 成交量异常告警（超过N倍日均）
  - 波动率突增告警
  - 告警条件持久化
  - 后台线程定期检查触发条件
依赖：LongPort WebSocket 实时行情订阅
"""
import sqlite3
import json
import os
import threading
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional, List, Callable
from enum import Enum


# ============================================================
# 数据结构
# ============================================================

class AlertType(str, Enum):
    """告警类型"""
    PRICE_BREAK_ABOVE = "价格突破"
    PRICE_BREAK_BELOW = "价格跌破"
    VOLUME_SPIKE      = "成交量异常"
    VOLATILITY_SPIKE  = "波动率突增"
    CUSTOM            = "自定义条件"


class AlertStatus(str, Enum):
    """告警状态"""
    ACTIVE    = "已激活"
    TRIGGERED = "已触发"
    CANCELLED = "已取消"
    EXPIRED   = "已过期"


@dataclass
class Alert:
    """告警配置"""
    alert_id:      str                    # 唯一ID
    symbol:        str                    # 股票代码
    alert_type:    str                    # 告警类型
    status:        str                    # 告警状态
    
    # 条件参数（根据类型不同而异）
    target_price:  Optional[float] = None # 目标价格
    direction:     Optional[str] = None   # "above" | "below"
    volume_threshold: Optional[float] = None  # 成交量倍数（如 1.5 = 1.5倍日均）
    volatility_threshold: Optional[float] = None  # 波动率阈值
    custom_condition: Optional[str] = None  # 自定义Python表达式
    
    # 元数据
    created_at:    str = ""               # 创建时间
    triggered_at:  Optional[str] = None   # 触发时间
    expires_at:    Optional[str] = None   # 过期时间
    message:       str = ""               # 自定义消息
    repeat:        bool = False           # 是否重复触发
    triggered_count: int = 0              # 触发次数
    
    # 触发时快照
    trigger_price: Optional[float] = None
    trigger_volume: Optional[float] = None


# ============================================================
# 数据库管理
# ============================================================

DB_PATH = os.path.join(os.path.dirname(__file__), "alerts.db")


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            alert_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            status TEXT NOT NULL,
            
            target_price REAL,
            direction TEXT,
            volume_threshold REAL,
            volatility_threshold REAL,
            custom_condition TEXT,
            
            created_at TEXT NOT NULL,
            triggered_at TEXT,
            expires_at TEXT,
            message TEXT,
            repeat INTEGER,
            triggered_count INTEGER,
            
            trigger_price REAL,
            trigger_volume REAL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON alerts(symbol)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_status ON alerts(status)")
    conn.commit()
    conn.close()


# ============================================================
# 告警管理器
# ============================================================

class AlertManager:
    def __init__(self):
        init_db()
        self._callbacks: List[Callable] = []  # 触发回调函数列表
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._quote_cache = {}  # 缓存最新行情 {symbol: {"price": x, "volume": y, "timestamp": t}}
    
    # ─── CRUD 操作 ────────────────────────────────────────
    
    def create_alert(self, alert: Alert) -> str:
        """创建告警"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        data = asdict(alert)
        data['repeat'] = 1 if alert.repeat else 0
        
        c.execute("""
            INSERT INTO alerts VALUES (
                :alert_id, :symbol, :alert_type, :status,
                :target_price, :direction, :volume_threshold, :volatility_threshold, :custom_condition,
                :created_at, :triggered_at, :expires_at, :message, :repeat, :triggered_count,
                :trigger_price, :trigger_volume
            )
        """, data)
        conn.commit()
        conn.close()
        return alert.alert_id
    
    def get_alert(self, alert_id: str) -> Optional[Alert]:
        """获取单个告警"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,))
        row = c.fetchone()
        conn.close()
        
        if not row:
            return None
        data = dict(row)
        data['repeat'] = bool(data['repeat'])
        return Alert(**data)
    
    def list_alerts(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        alert_type: Optional[str] = None,
    ) -> List[Alert]:
        """查询告警列表"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        conditions = []
        params = []
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if alert_type:
            conditions.append("alert_type = ?")
            params.append(alert_type)
        
        where = " AND ".join(conditions) if conditions else "1=1"
        c.execute(f"SELECT * FROM alerts WHERE {where} ORDER BY created_at DESC", params)
        rows = c.fetchall()
        conn.close()
        
        alerts = []
        for row in rows:
            data = dict(row)
            data['repeat'] = bool(data['repeat'])
            alerts.append(Alert(**data))
        return alerts
    
    def update_status(self, alert_id: str, status: str) -> bool:
        """更新告警状态"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE alerts SET status = ? WHERE alert_id = ?", (status, alert_id))
        updated = c.rowcount > 0
        conn.commit()
        conn.close()
        return updated
    
    def delete_alert(self, alert_id: str) -> bool:
        """删除告警"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM alerts WHERE alert_id = ?", (alert_id,))
        deleted = c.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    def mark_triggered(
        self,
        alert_id: str,
        trigger_price: float,
        trigger_volume: Optional[float] = None,
    ) -> bool:
        """标记为已触发"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        now = datetime.now().isoformat()
        
        # 获取当前告警
        c.execute("SELECT repeat, triggered_count FROM alerts WHERE alert_id = ?", (alert_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return False
        
        repeat, triggered_count = row[0], row[1]
        
        # 如果可重复触发，保持ACTIVE状态；否则改为TRIGGERED
        new_status = AlertStatus.ACTIVE if repeat else AlertStatus.TRIGGERED
        
        c.execute("""
            UPDATE alerts SET
                status = ?,
                triggered_at = ?,
                trigger_price = ?,
                trigger_volume = ?,
                triggered_count = ?
            WHERE alert_id = ?
        """, (new_status, now, trigger_price, trigger_volume, triggered_count + 1, alert_id))
        
        conn.commit()
        conn.close()
        return True
    
    # ─── 便捷创建方法 ─────────────────────────────────────
    
    def create_price_alert(
        self,
        symbol: str,
        target_price: float,
        direction: str,  # "above" | "below"
        message: str = "",
        expires_in_days: Optional[int] = None,
        repeat: bool = False,
    ) -> str:
        """创建价格告警"""
        alert_id = f"alert_{symbol}_{int(datetime.now().timestamp() * 1000)}"
        
        alert_type = AlertType.PRICE_BREAK_ABOVE if direction == "above" else AlertType.PRICE_BREAK_BELOW
        
        expires_at = None
        if expires_in_days:
            expires_at = (datetime.now() + timedelta(days=expires_in_days)).isoformat()
        
        if not message:
            message = f"{symbol} {'突破' if direction == 'above' else '跌破'} ${target_price:.2f}"
        
        alert = Alert(
            alert_id=alert_id,
            symbol=symbol,
            alert_type=alert_type,
            status=AlertStatus.ACTIVE,
            target_price=target_price,
            direction=direction,
            created_at=datetime.now().isoformat(),
            expires_at=expires_at,
            message=message,
            repeat=repeat,
        )
        
        return self.create_alert(alert)
    
    def create_volume_alert(
        self,
        symbol: str,
        threshold_multiplier: float,  # 如 1.5 表示1.5倍日均成交量
        message: str = "",
        expires_in_days: Optional[int] = None,
    ) -> str:
        """创建成交量告警"""
        alert_id = f"alert_{symbol}_{int(datetime.now().timestamp() * 1000)}"
        
        expires_at = None
        if expires_in_days:
            expires_at = (datetime.now() + timedelta(days=expires_in_days)).isoformat()
        
        if not message:
            message = f"{symbol} 成交量异常放大（>{threshold_multiplier}倍日均）"
        
        alert = Alert(
            alert_id=alert_id,
            symbol=symbol,
            alert_type=AlertType.VOLUME_SPIKE,
            status=AlertStatus.ACTIVE,
            volume_threshold=threshold_multiplier,
            created_at=datetime.now().isoformat(),
            expires_at=expires_at,
            message=message,
            repeat=True,  # 成交量告警默认可重复
        )
        
        return self.create_alert(alert)
    
    # ─── 行情更新与检查 ──────────────────────────────────
    
    def update_quote(self, symbol: str, price: float, volume: float):
        """更新行情缓存（由WebSocket回调）"""
        self._quote_cache[symbol] = {
            "price": price,
            "volume": volume,
            "timestamp": datetime.now().isoformat(),
        }
    
    def check_alerts(self):
        """检查所有活跃告警，触发时调用回调"""
        # 获取所有活跃告警
        active_alerts = self.list_alerts(status=AlertStatus.ACTIVE)
        
        for alert in active_alerts:
            # 检查是否过期
            if alert.expires_at:
                if datetime.now() > datetime.fromisoformat(alert.expires_at):
                    self.update_status(alert.alert_id, AlertStatus.EXPIRED)
                    continue
            
            # 获取最新行情
            quote = self._quote_cache.get(alert.symbol)
            if not quote:
                continue  # 暂无行情数据，跳过
            
            price = quote["price"]
            volume = quote["volume"]
            
            # 检查触发条件
            triggered = False
            
            if alert.alert_type in [AlertType.PRICE_BREAK_ABOVE, AlertType.PRICE_BREAK_BELOW]:
                if alert.direction == "above" and price >= alert.target_price:
                    triggered = True
                elif alert.direction == "below" and price <= alert.target_price:
                    triggered = True
            
            elif alert.alert_type == AlertType.VOLUME_SPIKE:
                # 成交量检查需要历史数据计算日均，这里简化为绝对值检查
                # 实际应用中需接入历史成交量数据
                if volume > alert.volume_threshold * 1_000_000:  # 简化示例
                    triggered = True
            
            # 触发告警
            if triggered:
                self.mark_triggered(alert.alert_id, price, volume)
                self._fire_callbacks(alert, price, volume)
    
    def _fire_callbacks(self, alert: Alert, price: float, volume: float):
        """触发所有回调函数"""
        for callback in self._callbacks:
            try:
                callback(alert, price, volume)
            except Exception as e:
                print(f"Alert callback error: {e}")
    
    def register_callback(self, callback: Callable):
        """注册告警触发回调"""
        self._callbacks.append(callback)
    
    # ─── 后台监控线程 ─────────────────────────────────────
    
    def start_monitoring(self, interval: int = 5):
        """启动后台监控线程（每N秒检查一次）"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            print("Monitor thread already running")
            return
        
        self._stop_flag.clear()
        
        def monitor_loop():
            while not self._stop_flag.is_set():
                try:
                    self.check_alerts()
                except Exception as e:
                    print(f"Monitor error: {e}")
                time.sleep(interval)
        
        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()
        print(f"Alert monitor started (interval={interval}s)")
    
    def stop_monitoring(self):
        """停止后台监控"""
        if self._monitor_thread:
            self._stop_flag.set()
            self._monitor_thread.join(timeout=10)
            print("Alert monitor stopped")
    
    # ─── 统计与报告 ───────────────────────────────────────
    
    def get_statistics(self) -> dict:
        """获取告警统计"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute("SELECT status, COUNT(*) FROM alerts GROUP BY status")
        status_counts = {row[0]: row[1] for row in c.fetchall()}
        
        c.execute("SELECT alert_type, COUNT(*) FROM alerts GROUP BY alert_type")
        type_counts = {row[0]: row[1] for row in c.fetchall()}
        
        c.execute("""
            SELECT symbol, COUNT(*) as cnt
            FROM alerts
            WHERE status = ?
            GROUP BY symbol
            ORDER BY cnt DESC
            LIMIT 5
        """, (AlertStatus.ACTIVE,))
        top_symbols = [{"symbol": row[0], "count": row[1]} for row in c.fetchall()]
        
        c.execute("""
            SELECT COUNT(*) as cnt, SUM(triggered_count) as total_triggers
            FROM alerts
            WHERE status = ?
        """, (AlertStatus.TRIGGERED,))
        row = c.fetchone()
        triggered_alerts = row[0] if row[0] else 0
        total_triggers = row[1] if row[1] else 0
        
        conn.close()
        
        return {
            "status_distribution": status_counts,
            "type_distribution": type_counts,
            "top_monitored_symbols": top_symbols,
            "triggered_alerts": triggered_alerts,
            "total_triggers": total_triggers,
            "cache_size": len(self._quote_cache),
            "monitor_running": self._monitor_thread.is_alive() if self._monitor_thread else False,
        }


# ============================================================
# 快捷函数
# ============================================================

def format_alert_message(alert: Alert, price: float, volume: Optional[float] = None) -> str:
    """格式化告警消息"""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔔 告警触发",
        f"标的：{alert.symbol}",
        f"类型：{alert.alert_type}",
        f"当前价格：${price:.2f}",
    ]
    
    if alert.target_price:
        lines.append(f"目标价格：${alert.target_price:.2f}")
    
    if volume is not None:
        lines.append(f"当前成交量：{volume:,.0f}")
    
    if alert.message:
        lines.append(f"备注：{alert.message}")
    
    lines.append(f"触发时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"触发次数：{alert.triggered_count + 1}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    return "\n".join(lines)


# ============================================================
# 单例
# ============================================================

_manager = AlertManager()


def get_alert_manager() -> AlertManager:
    return _manager


# ============================================================
# 示例用法
# ============================================================

if __name__ == "__main__":
    # 初始化
    manager = get_alert_manager()
    
    # 注册回调（打印到控制台）
    def on_alert_triggered(alert: Alert, price: float, volume: float):
        msg = format_alert_message(alert, price, volume)
        print(msg)
    
    manager.register_callback(on_alert_triggered)
    
    # 创建告警
    alert_id = manager.create_price_alert(
        symbol="AAPL.US",
        target_price=150.0,
        direction="above",
        message="苹果突破150，考虑减仓",
        expires_in_days=7,
    )
    print(f"Created alert: {alert_id}")
    
    # 模拟行情更新
    manager.update_quote("AAPL.US", 149.5, 50_000_000)
    manager.check_alerts()  # 不会触发
    
    manager.update_quote("AAPL.US", 150.1, 55_000_000)
    manager.check_alerts()  # 触发！
    
    # 启动后台监控（实际使用中配合WebSocket）
    # manager.start_monitoring(interval=5)
    
    # 统计
    stats = manager.get_statistics()
    print(json.dumps(stats, indent=2, ensure_ascii=False))

    def setup_notification_callback():
        """设置通知回调"""
        from feishu_bot import get_notification_manager
        
        manager = get_alert_manager()
        notification = get_notification_manager()
    
    def on_alert_triggered(alert, price, volume):
        # 发送告警到飞书/钉钉
        details = {
            "标的": alert.symbol,
            "当前价": f"${price:.2f}",
            "目标价": f"${alert.target_price:.2f}" if alert.target_price else "N/A",
            "触发次数": alert.triggered_count + 1,
        }
        
        notification.send_alert(
            alert_type="warning",
            message=alert.message or f"{alert.alert_type}触发",
            details=details
        )
    
    manager.register_callback(on_alert_triggered)

    # 在模块加载时自动设置
    setup_notification_callback()