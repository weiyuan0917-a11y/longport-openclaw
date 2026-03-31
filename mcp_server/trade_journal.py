"""
trade_journal.py - 交易日志与复盘系统
功能：
  - 记录每笔交易的决策理由、市场环境、情绪标签
  - 历史交易查询与过滤
  - 自动生成周/月复盘报告
  - 分析决策质量（盈亏模式识别）
依赖：SQLite（本地持久化）
"""
import sqlite3
import json
import os
from datetime import datetime, date, timedelta
from dataclasses import dataclass, asdict
from typing import Optional, List
from enum import Enum


# ============================================================
# 数据结构
# ============================================================

class EmotionTag(str, Enum):
    """情绪标签"""
    RATIONAL      = "理性决策"
    PANIC_BUY     = "恐慌买入"
    FOMO          = "FOMO追涨"
    GREED_HOLD    = "贪婪持有"
    FEAR_SELL     = "恐惧卖出"
    DISCIPLINED   = "纪律执行"
    REVENGE       = "报复性交易"


@dataclass
class TradeEntry:
    """单笔交易记录"""
    # 基础信息
    trade_id:        str              # 唯一ID（时间戳）
    symbol:          str              # 股票代码
    action:          str              # "buy" | "sell"
    quantity:        int              # 数量
    price:           float            # 成交价
    timestamp:       str              # 交易时间 ISO格式
    
    # 决策信息
    decision_reason: str = ""         # 决策理由（用户填写或Claude生成）
    strategy_used:   str = ""         # 使用的策略（如"MA交叉"）
    emotion_tag:     str = EmotionTag.RATIONAL  # 情绪标签
    
    # 市场环境（交易时快照）
    market_trend:    str = ""         # "上涨" | "下跌" | "震荡"
    market_sentiment: float = 50.0    # Fear & Greed Index (0-100)
    vix_level:       float = 0.0      # VIX指数
    
    # 结果信息（平仓后填写）
    exit_price:      Optional[float] = None
    exit_timestamp:  Optional[str] = None
    pnl:             Optional[float] = None    # 盈亏金额
    pnl_pct:         Optional[float] = None    # 盈亏百分比
    hold_days:       Optional[int] = None
    
    # 复盘信息（事后分析）
    lesson_learned:  str = ""         # 经验教训
    mistake_type:    str = ""         # 错误类型（如"止损不及时"）
    rating:          int = 0          # 决策评分 1-5星


# ============================================================
# 数据库管理
# ============================================================

DB_PATH = os.path.join(os.path.dirname(__file__), "trade_journal.db")


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            timestamp TEXT NOT NULL,
            
            decision_reason TEXT,
            strategy_used TEXT,
            emotion_tag TEXT,
            
            market_trend TEXT,
            market_sentiment REAL,
            vix_level REAL,
            
            exit_price REAL,
            exit_timestamp TEXT,
            pnl REAL,
            pnl_pct REAL,
            hold_days INTEGER,
            
            lesson_learned TEXT,
            mistake_type TEXT,
            rating INTEGER,
            
            created_at TEXT NOT NULL
        )
    """)
    # 索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON trades(symbol)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON trades(timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_action ON trades(action)")
    conn.commit()
    conn.close()


# ============================================================
# CRUD 操作
# ============================================================

class TradeJournal:
    def __init__(self):
        init_db()
    
    def save_trade(self, entry: TradeEntry) -> str:
        """保存交易记录"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        data = asdict(entry)
        data['created_at'] = datetime.now().isoformat()
        
        c.execute("""
            INSERT OR REPLACE INTO trades VALUES (
                :trade_id, :symbol, :action, :quantity, :price, :timestamp,
                :decision_reason, :strategy_used, :emotion_tag,
                :market_trend, :market_sentiment, :vix_level,
                :exit_price, :exit_timestamp, :pnl, :pnl_pct, :hold_days,
                :lesson_learned, :mistake_type, :rating,
                :created_at
            )
        """, data)
        conn.commit()
        conn.close()
        return entry.trade_id
    
    def get_trade(self, trade_id: str) -> Optional[TradeEntry]:
        """获取单笔交易"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,))
        row = c.fetchone()
        conn.close()
        
        if not row:
            return None
        return TradeEntry(**dict(row))
    
    def update_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_timestamp: str,
        pnl: float,
        pnl_pct: float,
        hold_days: int,
    ) -> bool:
        """更新平仓信息"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            UPDATE trades SET
                exit_price = ?,
                exit_timestamp = ?,
                pnl = ?,
                pnl_pct = ?,
                hold_days = ?
            WHERE trade_id = ?
        """, (exit_price, exit_timestamp, pnl, pnl_pct, hold_days, trade_id))
        updated = c.rowcount > 0
        conn.commit()
        conn.close()
        return updated
    
    def add_review(
        self,
        trade_id: str,
        lesson_learned: str,
        mistake_type: str = "",
        rating: int = 3,
    ) -> bool:
        """添加复盘信息"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            UPDATE trades SET
                lesson_learned = ?,
                mistake_type = ?,
                rating = ?
            WHERE trade_id = ?
        """, (lesson_learned, mistake_type, rating, trade_id))
        updated = c.rowcount > 0
        conn.commit()
        conn.close()
        return updated
    
    def query_trades(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        action: Optional[str] = None,
        min_pnl_pct: Optional[float] = None,
        max_pnl_pct: Optional[float] = None,
        emotion_tag: Optional[str] = None,
        has_review: Optional[bool] = None,
        limit: int = 100,
    ) -> List[TradeEntry]:
        """查询交易记录"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        conditions = []
        params = []
        
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        if start_date:
            conditions.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("timestamp <= ?")
            params.append(end_date)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if min_pnl_pct is not None:
            conditions.append("pnl_pct >= ?")
            params.append(min_pnl_pct)
        if max_pnl_pct is not None:
            conditions.append("pnl_pct <= ?")
            params.append(max_pnl_pct)
        if emotion_tag:
            conditions.append("emotion_tag = ?")
            params.append(emotion_tag)
        if has_review is not None:
            if has_review:
                conditions.append("lesson_learned != ''")
            else:
                conditions.append("lesson_learned = ''")
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM trades WHERE {where_clause} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        c.execute(query, params)
        rows = c.fetchall()
        conn.close()
        
        return [TradeEntry(**dict(row)) for row in rows]
    
    def get_statistics(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> dict:
        """统计分析"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        conditions = []
        params = []
        if start_date:
            conditions.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("timestamp <= ?")
            params.append(end_date)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # 基础统计
        c.execute(f"""
            SELECT
                COUNT(*) as total_trades,
                COUNT(CASE WHEN pnl > 0 THEN 1 END) as win_trades,
                COUNT(CASE WHEN pnl < 0 THEN 1 END) as loss_trades,
                SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as total_profit,
                SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as total_loss,
                AVG(CASE WHEN pnl > 0 THEN pnl_pct END) as avg_win_pct,
                AVG(CASE WHEN pnl < 0 THEN pnl_pct END) as avg_loss_pct,
                AVG(hold_days) as avg_hold_days
            FROM trades
            WHERE {where_clause} AND pnl IS NOT NULL
        """, params)
        
        row = c.fetchone()
        stats = {
            "total_trades": row[0] or 0,
            "win_trades": row[1] or 0,
            "loss_trades": row[2] or 0,
            "total_profit": round(row[3] or 0, 2),
            "total_loss": round(row[4] or 0, 2),
            "avg_win_pct": round(row[5] or 0, 2),
            "avg_loss_pct": round(row[6] or 0, 2),
            "avg_hold_days": round(row[7] or 0, 1) if row[7] else 0,
        }
        
        # 计算胜率和盈亏比
        if stats["total_trades"] > 0:
            stats["win_rate"] = round(stats["win_trades"] / stats["total_trades"] * 100, 1)
        else:
            stats["win_rate"] = 0.0
        
        if stats["total_loss"] > 0:
            stats["profit_factor"] = round(stats["total_profit"] / stats["total_loss"], 2)
        else:
            stats["profit_factor"] = float('inf') if stats["total_profit"] > 0 else 0
        
        # 情绪分布
        c.execute(f"""
            SELECT emotion_tag, COUNT(*) as cnt
            FROM trades
            WHERE {where_clause}
            GROUP BY emotion_tag
            ORDER BY cnt DESC
        """, params)
        stats["emotion_distribution"] = [
            {"tag": row[0], "count": row[1]} for row in c.fetchall()
        ]
        
        # 策略分布
        c.execute(f"""
            SELECT strategy_used, COUNT(*) as cnt, AVG(pnl_pct) as avg_pnl
            FROM trades
            WHERE {where_clause} AND strategy_used != '' AND pnl IS NOT NULL
            GROUP BY strategy_used
            ORDER BY cnt DESC
        """, params)
        stats["strategy_performance"] = [
            {"strategy": row[0], "count": row[1], "avg_pnl_pct": round(row[2] or 0, 2)}
            for row in c.fetchall()
        ]
        
        conn.close()
        return stats


# ============================================================
# 复盘报告生成
# ============================================================

def generate_review_report(
    journal: TradeJournal,
    period: str = "week",  # "week" | "month" | "quarter"
    symbol: Optional[str] = None,
) -> dict:
    """生成复盘报告"""
    
    # 计算时间范围
    now = datetime.now()
    if period == "week":
        start = (now - timedelta(days=7)).isoformat()
        title = f"近7天交易复盘"
    elif period == "month":
        start = (now - timedelta(days=30)).isoformat()
        title = f"近30天交易复盘"
    elif period == "quarter":
        start = (now - timedelta(days=90)).isoformat()
        title = f"近90天交易复盘"
    else:
        start = (now - timedelta(days=7)).isoformat()
        title = f"近7天交易复盘"
    
    # 获取统计数据
    stats = journal.get_statistics(start_date=start, symbol=symbol)
    
    # 获取最差交易（Top 5 亏损）
    worst_trades = journal.query_trades(
        start_date=start,
        symbol=symbol,
        max_pnl_pct=0,
        limit=5,
    )
    worst_trades.sort(key=lambda x: x.pnl_pct or 0)
    
    # 获取最佳交易（Top 5 盈利）
    best_trades = journal.query_trades(
        start_date=start,
        symbol=symbol,
        min_pnl_pct=0,
        limit=5,
    )
    best_trades.sort(key=lambda x: x.pnl_pct or 0, reverse=True)
    
    # 情绪分析
    emotion_issues = []
    for item in stats["emotion_distribution"]:
        tag = item["tag"]
        count = item["count"]
        if tag in [EmotionTag.PANIC_BUY, EmotionTag.FOMO, EmotionTag.GREED_HOLD, 
                   EmotionTag.FEAR_SELL, EmotionTag.REVENGE] and count > 0:
            emotion_issues.append(f"{tag}: {count}次")
    
    # 生成建议
    recommendations = []
    
    if stats["win_rate"] < 40:
        recommendations.append("⚠️ 胜率偏低（<40%），建议减少交易频率，只做高确定性机会")
    
    if stats["avg_loss_pct"] < -5:
        recommendations.append("⚠️ 平均亏损过大，止损不及时，需严格执行止损纪律")
    
    if stats["avg_hold_days"] < 1:
        recommendations.append("⚠️ 持仓时间过短，可能存在过度交易倾向")
    
    if emotion_issues:
        recommendations.append(f"⚠️ 情绪化交易频繁：{', '.join(emotion_issues)}")
    
    if stats["profit_factor"] < 1.5:
        recommendations.append("⚠️ 盈亏比不足1.5，盈利能力偏弱")
    
    if not recommendations:
        recommendations.append("✅ 整体交易质量良好，继续保持")
    
    # 策略分析
    strategy_insights = []
    for item in stats["strategy_performance"]:
        if item["avg_pnl_pct"] < -2:
            strategy_insights.append(
                f"策略「{item['strategy']}」表现不佳（平均亏损{abs(item['avg_pnl_pct']):.1f}%），建议暂停使用"
            )
        elif item["avg_pnl_pct"] > 3:
            strategy_insights.append(
                f"策略「{item['strategy']}」表现优异（平均盈利{item['avg_pnl_pct']:.1f}%），值得增加仓位"
            )
    
    return {
        "title": title,
        "period": period,
        "symbol": symbol or "全部",
        "start_date": start,
        "end_date": now.isoformat(),
        "statistics": stats,
        "best_trades": [
            {
                "symbol": t.symbol,
                "entry_date": t.timestamp[:10],
                "exit_date": t.exit_timestamp[:10] if t.exit_timestamp else "持仓中",
                "pnl_pct": f"+{t.pnl_pct:.2f}%" if t.pnl_pct else "N/A",
                "strategy": t.strategy_used,
                "reason": t.decision_reason[:50] + "..." if len(t.decision_reason) > 50 else t.decision_reason,
            }
            for t in best_trades[:3]
        ],
        "worst_trades": [
            {
                "symbol": t.symbol,
                "entry_date": t.timestamp[:10],
                "exit_date": t.exit_timestamp[:10] if t.exit_timestamp else "持仓中",
                "pnl_pct": f"{t.pnl_pct:.2f}%" if t.pnl_pct else "N/A",
                "emotion": t.emotion_tag,
                "mistake": t.mistake_type or "待分析",
            }
            for t in worst_trades[:3]
        ],
        "emotion_distribution": stats["emotion_distribution"],
        "strategy_performance": stats["strategy_performance"],
        "recommendations": recommendations,
        "strategy_insights": strategy_insights,
        "generated_at": now.isoformat(),
    }


# ============================================================
# 决策质量分析
# ============================================================

def analyze_decision_quality(journal: TradeJournal, days: int = 30) -> dict:
    """分析决策质量，识别常见错误模式"""
    
    start_date = (datetime.now() - timedelta(days=days)).isoformat()
    trades = journal.query_trades(start_date=start_date, limit=500)
    
    # 按情绪分组计算盈亏
    emotion_pnl = {}
    for t in trades:
        if t.pnl is not None:
            if t.emotion_tag not in emotion_pnl:
                emotion_pnl[t.emotion_tag] = {"trades": [], "total_pnl": 0}
            emotion_pnl[t.emotion_tag]["trades"].append(t)
            emotion_pnl[t.emotion_tag]["total_pnl"] += t.pnl
    
    # 找出最差情绪
    worst_emotion = None
    worst_pnl = 0
    for emotion, data in emotion_pnl.items():
        if data["total_pnl"] < worst_pnl:
            worst_pnl = data["total_pnl"]
            worst_emotion = emotion
    
    # 市场环境分析
    trend_pnl = {"上涨": 0, "下跌": 0, "震荡": 0}
    trend_count = {"上涨": 0, "下跌": 0, "震荡": 0}
    for t in trades:
        if t.pnl is not None and t.market_trend in trend_pnl:
            trend_pnl[t.market_trend] += t.pnl
            trend_count[t.market_trend] += 1
    
    # 持仓时间分析
    short_term = [t for t in trades if t.hold_days and t.hold_days <= 3]
    mid_term = [t for t in trades if t.hold_days and 3 < t.hold_days <= 10]
    long_term = [t for t in trades if t.hold_days and t.hold_days > 10]
    
    def avg_pnl(trade_list):
        pnls = [t.pnl_pct for t in trade_list if t.pnl_pct is not None]
        return sum(pnls) / len(pnls) if pnls else 0
    
    return {
        "period_days": days,
        "total_analyzed": len(trades),
        "emotion_analysis": {
            "worst_emotion": worst_emotion,
            "worst_emotion_loss": round(worst_pnl, 2),
            "distribution": [
                {
                    "emotion": k,
                    "count": len(v["trades"]),
                    "total_pnl": round(v["total_pnl"], 2),
                    "avg_pnl_pct": round(avg_pnl(v["trades"]), 2),
                }
                for k, v in emotion_pnl.items()
            ],
        },
        "market_environment": [
            {
                "trend": k,
                "count": trend_count[k],
                "total_pnl": round(v, 2),
                "avg_pnl": round(v / trend_count[k], 2) if trend_count[k] > 0 else 0,
            }
            for k, v in trend_pnl.items()
        ],
        "holding_period": {
            "short_term_days_1_3": {
                "count": len(short_term),
                "avg_pnl_pct": round(avg_pnl(short_term), 2),
            },
            "mid_term_days_4_10": {
                "count": len(mid_term),
                "avg_pnl_pct": round(avg_pnl(mid_term), 2),
            },
            "long_term_days_10_plus": {
                "count": len(long_term),
                "avg_pnl_pct": round(avg_pnl(long_term), 2),
            },
        },
        "insights": _generate_insights(emotion_pnl, trend_pnl, trend_count, short_term, mid_term, long_term),
    }


def _generate_insights(emotion_pnl, trend_pnl, trend_count, short_term, mid_term, long_term):
    """生成洞察建议"""
    insights = []
    
    # 情绪洞察
    for emotion, data in emotion_pnl.items():
        if emotion in [EmotionTag.PANIC_BUY, EmotionTag.FOMO] and data["total_pnl"] < -500:
            insights.append(f"⚠️ 「{emotion}」导致显著亏损，建议设置冷静期（等待24小时再决策）")
    
    # 市场环境洞察
    if trend_count["下跌"] > 0 and trend_pnl["下跌"] / trend_count["下跌"] < -100:
        insights.append("⚠️ 在下跌市场中平均亏损较大，建议减少逆势抄底")
    
    # 持仓时间洞察
    def avg_pnl(lst):
        pnls = [t.pnl_pct for t in lst if t.pnl_pct]
        return sum(pnls) / len(pnls) if pnls else 0
    
    if short_term and avg_pnl(short_term) < -2:
        insights.append("⚠️ 短线交易（1-3天）普遍亏损，建议延长持仓时间或减少短线操作")
    
    if long_term and avg_pnl(long_term) > 5:
        insights.append("✅ 长线持仓（10天+）效果良好，建议增加长线仓位比例")
    
    if not insights:
        insights.append("✅ 暂未发现明显决策质量问题")
    
    return insights


# ============================================================
# 单例
# ============================================================

_journal = TradeJournal()


def get_journal() -> TradeJournal:
    return _journal