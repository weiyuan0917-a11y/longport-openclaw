"""
notification_mcp_tools.py - 飞书/钉钉通知 MCP 工具
"""
import mcp.types as types
from feishu_bot import get_notification_manager
import json


# ============================================================
# MCP 工具定义
# ============================================================

def get_notification_tools() -> list[types.Tool]:
    """返回通知相关的 MCP 工具列表"""
    return [
        types.Tool(
            name="send_notification",
            description="发送消息到飞书/钉钉（支持文本、告警、报告）",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "消息内容"
                    },
                    "msg_type": {
                        "type": "string",
                        "enum": ["text", "alert", "report"],
                        "description": "消息类型：text(普通文本) | alert(告警) | report(报告)"
                    },
                    "alert_type": {
                        "type": "string",
                        "enum": ["error", "warning", "info", "success"],
                        "description": "告警类型（当 msg_type=alert 时有效）"
                    },
                    "details": {
                        "type": "object",
                        "description": "详细信息（JSON对象，用于告警和报告）"
                    }
                },
                "required": ["message", "msg_type"]
            },
        ),
        types.Tool(
            name="test_notification",
            description="测试飞书/钉钉通知是否配置成功",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


# ============================================================
# MCP 工具实现
# ============================================================

async def mcp_send_notification(args: dict) -> list[types.TextContent]:
    """发送通知"""
    try:
        manager = get_notification_manager()
        
        message = args["message"]
        msg_type = args["msg_type"]
        
        results = {}
        
        if msg_type == "text":
            # 发送普通文本
            results = manager.send_text(message)
        
        elif msg_type == "alert":
            # 发送告警
            alert_type = args.get("alert_type", "info")
            details = args.get("details", {})
            results = manager.send_alert(alert_type, message, details)
        
        elif msg_type == "report":
            # 发送报告
            details = args.get("details", {})
            report_data = {
                "summary": message,
                **details
            }
            results = manager.send_daily_report(report_data)
        
        # 统计发送结果
        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)
        
        if success_count == 0:
            response = (
                "❌ 消息发送失败\n\n"
                "可能原因：\n"
                "1. 未配置 Webhook URL（请查看 notification_config.json）\n"
                "2. Webhook URL 或密钥错误\n"
                "3. 网络连接问题\n\n"
                "请检查配置后重试。"
            )
        elif success_count < total_count:
            response = (
                f"⚠️ 部分消息发送成功\n\n"
                f"成功: {success_count}/{total_count}\n\n"
                "请检查失败的机器人配置。"
            )
        else:
            response = (
                f"✅ 消息发送成功\n\n"
                f"已发送到 {success_count} 个机器人\n\n"
                f"消息内容: {message[:50]}..."
            )
        
        return [types.TextContent(type="text", text=response)]
    
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"发送失败: {str(e)}\n\n请检查配置文件和网络连接。"
        )]


async def mcp_test_notification(args: dict) -> list[types.TextContent]:
    """测试通知配置"""
    try:
        manager = get_notification_manager()
        
        # 统计配置的机器人数量
        feishu_count = len(manager.feishu_bots)
        dingtalk_count = len(manager.dingtalk_bots)
        
        if feishu_count == 0 and dingtalk_count == 0:
            response = (
                "❌ 未配置任何机器人\n\n"
                "请在 notification_config.json 中配置飞书或钉钉 Webhook URL\n\n"
                "配置示例：\n"
                "```json\n"
                "{\n"
                '  "feishu_bots": [\n'
                "    {\n"
                '      "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_WEBHOOK",\n'
                '      "secret": "YOUR_SECRET"\n'
                "    }\n"
                "  ]\n"
                "}\n"
                "```\n\n"
                "配置完成后，输入「发送测试消息到飞书」来测试。"
            )
        else:
            # 发送测试消息
            test_message = "🎉 LongPort 交易系统通知测试\n\n系统已成功连接！"
            results = manager.send_text(test_message)
            
            success_count = sum(1 for v in results.values() if v)
            
            response = (
                f"📊 通知配置状态\n\n"
                f"- 飞书机器人: {feishu_count} 个\n"
                f"- 钉钉机器人: {dingtalk_count} 个\n"
                f"- 总计: {feishu_count + dingtalk_count} 个\n\n"
            )
            
            if success_count > 0:
                response += (
                    f"✅ 测试消息发送成功（{success_count}/{len(results)}）\n\n"
                    "请在飞书/钉钉中查看测试消息。"
                )
            else:
                response += (
                    "❌ 测试消息发送失败\n\n"
                    "请检查：\n"
                    "1. Webhook URL 是否正确\n"
                    "2. 密钥是否正确（如果配置了）\n"
                    "3. 网络是否畅通"
                )
        
        return [types.TextContent(type="text", text=response)]
    
    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"测试失败: {str(e)}"
        )]


# ============================================================
# 工具分发映射
# ============================================================

NOTIFICATION_TOOL_DISPATCH = {
    "send_notification":  mcp_send_notification,
    "test_notification":  mcp_test_notification,
}