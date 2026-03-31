"""
market_mcp_tools.py - 市场环境分析 MCP 工具
为 mcp_extensions.py 提供市场分析相关的工具定义和实现
"""
import mcp.types as types
from market_analysis import (
    get_market_sentiment,
    get_macro_indicators,
    get_comprehensive_analysis,
    get_sector_rotation
)
import json


# ============================================================
# MCP 工具定义
# ============================================================

def get_market_analysis_tools() -> list[types.Tool]:
    """返回市场分析相关的 MCP 工具列表"""
    return [
        types.Tool(
            name="get_market_sentiment",
            description="获取市场情绪指数（Fear & Greed Index 0-100），越低越恐慌",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_macro_indicators",
            description="获取宏观指标（VIX波动率、10年期国债收益率、美元指数）",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_market_analysis",
            description="综合市场分析，整合情绪指数、宏观指标，给出市场环境判断和策略建议",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_sector_rotation",
            description="板块轮动分析，查看各行业板块近期表现，识别市场热点和冷门",
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "分析天数，默认5天"
                    }
                },
                "required": []
            },
        ),
    ]


# ============================================================
# MCP 工具实现
# ============================================================

async def mcp_get_market_sentiment(args: dict) -> list[types.TextContent]:
    """获取市场情绪指数"""
    try:
        result = get_market_sentiment()
        
        response = (
            f"# 📊 市场情绪指数\n\n"
            f"**指数值**: {result['value']}/100\n"
            f"**情绪等级**: {result['level']}\n"
            f"**更新时间**: {result['timestamp'][:19]}\n\n"
            f"## 📈 解读\n"
        )
        
        if result['value'] <= 20:
            response += (
                "市场处于**极度恐慌**状态（≤20）。\n"
                "- 投资者情绪极度悲观\n"
                "- 历史上常是买入机会\n"
                "- 建议：逢低布局优质标的，分批建仓\n"
            )
        elif result['value'] <= 40:
            response += (
                "市场处于**恐慌**状态（21-40）。\n"
                "- 投资者担忧情绪明显\n"
                "- 可能接近阶段性底部\n"
                "- 建议：关注超跌反弹机会，谨慎操作\n"
            )
        elif result['value'] <= 60:
            response += (
                "市场处于**中性**状态（41-60）。\n"
                "- 投资者情绪平衡\n"
                "- 市场方向不明朗\n"
                "- 建议：观望为主，等待明确信号\n"
            )
        elif result['value'] <= 80:
            response += (
                "市场处于**贪婪**状态（61-80）。\n"
                "- 投资者情绪乐观\n"
                "- 需警惕过度乐观\n"
                "- 建议：锁定部分利润，设置止损\n"
            )
        else:
            response += (
                "市场处于**极度贪婪**状态（≥81）。\n"
                "- 投资者情绪过热\n"
                "- 历史上常是卖出信号\n"
                "- 建议：减仓降低风险，保持警惕\n"
            )
        
        return [types.TextContent(type="text", text=response)]
    
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"获取市场情绪失败: {str(e)}\n\n请稍后重试或检查网络连接。"
        )]


async def mcp_get_macro_indicators(args: dict) -> list[types.TextContent]:
    """获取宏观指标"""
    try:
        result = get_macro_indicators()
        
        vix = result['vix']
        treasury = result['treasury_10y']
        dollar = result['dollar_index']
        
        response = (
            f"# 📈 宏观指标\n\n"
            f"## 1️⃣ VIX 恐慌指数\n"
            f"- **当前值**: {vix['value']}\n"
            f"- **变化**: {vix['change']:+.2f} ({vix['change_pct']:+.2f}%)\n"
            f"- **解读**: {vix['interpretation']}\n\n"
            f"## 2️⃣ 10年期国债收益率\n"
            f"- **当前值**: {treasury['value']}{treasury['unit']}\n"
            f"- **变化**: {treasury['change']:+.2f}{treasury['unit']} ({treasury['change_pct']:+.2f}%)\n"
            f"- **解读**: {treasury['interpretation']}\n\n"
            f"## 3️⃣ 美元指数 DXY\n"
            f"- **当前值**: {dollar['value']}\n"
            f"- **变化**: {dollar['change']:+.2f} ({dollar['change_pct']:+.2f}%)\n"
            f"- **解读**: {dollar['interpretation']}\n\n"
            f"---\n"
            f"*更新时间: {vix['timestamp'][:19]}*"
        )
        
        return [types.TextContent(type="text", text=response)]
    
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"获取宏观指标失败: {str(e)}\n\n请稍后重试或检查网络连接。"
        )]


async def mcp_get_market_analysis(args: dict) -> list[types.TextContent]:
    """综合市场分析"""
    try:
        result = get_comprehensive_analysis()
        
        indicators = result['indicators']
        fg = indicators['fear_greed_index']
        vix = indicators['vix']
        
        response = (
            f"# 🌍 综合市场分析\n\n"
            f"## 📊 市场环境判断\n"
            f"**{result['market_environment']}**\n\n"
            f"**综合评分**: {result['score']}/5 "
            f"({'看多' if result['score'] > 0 else '看空' if result['score'] < 0 else '中性'})\n\n"
            f"## 💡 策略建议\n"
            f"{result['strategy_recommendation']}\n\n"
            f"## 📈 核心指标\n"
            f"- **情绪指数**: {fg['value']}/100 ({fg['level']})\n"
            f"- **VIX**: {vix['value']} "
            f"({'高波动' if vix['value'] > 25 else '低波动' if vix['value'] < 15 else '正常'})\n"
            f"- **10Y国债**: {indicators['treasury_10y']['value']}%\n"
            f"- **美元指数**: {indicators['dollar_index']['value']}\n\n"
            f"---\n"
            f"*分析时间: {result['analysis_time'][:19]}*"
        )
        
        return [types.TextContent(type="text", text=response)]
    
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"综合分析失败: {str(e)}\n\n请稍后重试或检查网络连接。"
        )]


async def mcp_get_sector_rotation(args: dict) -> list[types.TextContent]:
    """板块轮动分析"""
    try:
        days = args.get("days", 5)
        if not isinstance(days, int) or days < 1 or days > 60:
            days = 5
        result = get_sector_rotation(days=days)
        
        if 'error' in result:
            return [types.TextContent(
                type="text",
                text=f"❌ {result['error']}\n\n请稍后重试或检查网络连接。"
            )]
        
        top = result['top_performers']
        bottom = result['bottom_performers']
        
        response = (
            f"# 🔄 板块轮动分析\n\n"
            f"## 📊 轮动特征\n"
            f"**{result['rotation_phase']}**\n\n"
            f"## 🚀 强势板块（近{days}日）\n"
        )
        
        for i, sector in enumerate(top, 1):
            emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
            response += (
                f"{emoji} **{sector['name']}** ({sector['symbol']})\n"
                f"   涨幅: {sector['change_pct']:+.2f}% | 最新价: ${sector['latest_price']}\n\n"
            )
        
        response += f"## 📉 弱势板块（近{days}日）\n"
        
        for sector in bottom:
            response += (
                f"• **{sector['name']}** ({sector['symbol']})\n"
                f"   涨幅: {sector['change_pct']:+.2f}% | 最新价: ${sector['latest_price']}\n\n"
            )
        
        response += (
            f"---\n"
            f"*分析时间: {result['analysis_time'][:19]}*\n\n"
            f"💡 **投资建议**: "
        )
        
        # 根据轮动特征给建议
        if "成长" in result['rotation_phase']:
            response += "市场风险偏好高，可关注科技、消费等成长股"
        elif "防御" in result['rotation_phase']:
            response += "市场避险情绪浓，建议持有防御性板块，控制风险"
        elif "周期" in result['rotation_phase']:
            response += "经济复苏预期强，可关注工业、金融等周期股"
        elif "能源" in result['rotation_phase']:
            response += "通胀或地缘政治风险，能源板块受益"
        else:
            response += "板块轮动不明显，建议均衡配置"
        
        return [types.TextContent(type="text", text=response)]
    
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"板块分析失败: {str(e)}\n\n请稍后重试或检查网络连接。"
        )]


# ============================================================
# 工具分发映射
# ============================================================

MARKET_TOOL_DISPATCH = {
    "get_market_sentiment":  mcp_get_market_sentiment,
    "get_macro_indicators":  mcp_get_macro_indicators,
    "get_market_analysis":   mcp_get_market_analysis,
    "get_sector_rotation":   mcp_get_sector_rotation,
}