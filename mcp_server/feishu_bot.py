"""
feishu_bot.py - 飞书机器人集成
功能：
  - 接收飞书消息，解析指令并调用 MCP 工具
  - 主动推送告警、报告到飞书
  - 支持富文本消息（卡片、按钮、图表）
依赖：requests
"""
import requests
import json
import time
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
import hmac
import hashlib
import base64


# ============================================================
# 数据结构
# ============================================================

@dataclass
class FeishuConfig:
    """飞书机器人配置"""
    webhook_url: str          # Webhook 地址
    secret: Optional[str] = None  # 签名密钥（可选）
    app_id: Optional[str] = None  # 应用ID（高级功能）
    app_secret: Optional[str] = None  # 应用密钥


@dataclass
class Message:
    """消息结构"""
    msg_type: str  # text | interactive（卡片） | post（富文本）
    content: Dict
    timestamp: Optional[int] = None
    sign: Optional[str] = None


# ============================================================
# 飞书机器人客户端
# ============================================================

class FeishuBot:
    """飞书机器人"""
    
    def __init__(self, webhook_url: str, secret: Optional[str] = None):
        self.webhook_url = webhook_url
        self.secret = secret
    
    def _generate_sign(self, timestamp: int) -> str:
        """生成签名（如果配置了密钥）"""
        if not self.secret:
            return ""
        
        string_to_sign = f"{timestamp}\n{self.secret}"
        # HMAC: key=secret, message=string_to_sign（飞书要求）
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        sign = base64.b64encode(hmac_code).decode('utf-8')
        return sign
    
    def send_text(self, text: str) -> bool:
        """发送纯文本消息"""
        timestamp = int(time.time())
        
        payload = {
            "msg_type": "text",
            "content": {
                "text": text
            }
        }
        
        if self.secret:
            payload["timestamp"] = str(timestamp)
            payload["sign"] = self._generate_sign(timestamp)
        
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            result = response.json()
            return result.get("code") == 0
        except Exception as e:
            print(f"发送飞书消息失败: {e}")
            return False
    
    def send_card(self, title: str, content: str, **kwargs) -> bool:
        """
        发送卡片消息
        kwargs 可选参数：
          - color: 卡片颜色（blue/green/red/orange）
          - fields: 字段列表 [{"name": "字段名", "value": "值"}]
          - actions: 按钮列表 [{"text": "按钮文本", "url": "链接"}]
        """
        timestamp = int(time.time())
        
        # 构建卡片内容
        card_elements = []
        
        # 标题
        card_elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{title}**"
            }
        })
        
        # 分割线
        card_elements.append({"tag": "hr"})
        
        # 正文
        card_elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": content
            }
        })
        
        # 字段（如果有）
        if "fields" in kwargs:
            for field in kwargs["fields"]:
                card_elements.append({
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**{field['name']}**\n{field['value']}"
                            }
                        }
                    ]
                })
        
        # 按钮（如果有）
        if "actions" in kwargs:
            actions_element = {
                "tag": "action",
                "actions": []
            }
            for action in kwargs["actions"]:
                actions_element["actions"].append({
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": action["text"]
                    },
                    "url": action.get("url", ""),
                    "type": "primary" if action.get("primary", False) else "default"
                })
            card_elements.append(actions_element)
        
        # 时间戳
        card_elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                }
            ]
        })
        
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "wide_screen_mode": True
                },
                "header": {
                    "template": kwargs.get("color", "blue"),
                    "title": {
                        "tag": "plain_text",
                        "content": title
                    }
                },
                "elements": card_elements
            }
        }
        
        if self.secret:
            payload["timestamp"] = str(timestamp)
            payload["sign"] = self._generate_sign(timestamp)
        
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            result = response.json()
            return result.get("code") == 0
        except Exception as e:
            print(f"发送飞书卡片失败: {e}")
            return False
    
    def send_alert(self, alert_type: str, message: str, details: Dict = None) -> bool:
        """发送告警消息"""
        
        # 根据告警类型选择颜色
        color_map = {
            "error": "red",
            "warning": "orange",
            "info": "blue",
            "success": "green"
        }
        
        color = color_map.get(alert_type, "blue")
        
        # 表情映射
        emoji_map = {
            "error": "🚨",
            "warning": "⚠️",
            "info": "ℹ️",
            "success": "✅"
        }
        
        emoji = emoji_map.get(alert_type, "📢")
        
        title = f"{emoji} {alert_type.upper()} 告警"
        
        fields = []
        if details:
            for key, value in details.items():
                fields.append({"name": key, "value": str(value)})
        
        return self.send_card(
            title=title,
            content=message,
            color=color,
            fields=fields
        )
    
    def send_daily_report(self, report_data: Dict) -> bool:
        """发送每日报告"""
        
        fields = [
            {"name": "总资产", "value": f"${report_data.get('total_assets', 0):,.2f}"},
            {"name": "当日盈亏", "value": f"{report_data.get('daily_pnl', 0):+.2f}%"},
            {"name": "持仓数量", "value": str(report_data.get('position_count', 0))},
            {"name": "今日交易", "value": f"{report_data.get('trade_count', 0)}笔"},
        ]
        
        content = report_data.get('summary', '无')
        
        return self.send_card(
            title="📊 每日交易报告",
            content=content,
            color="blue",
            fields=fields
        )


# ============================================================
# 钉钉机器人客户端
# ============================================================

class DingTalkBot:
    """钉钉机器人"""
    
    def __init__(self, webhook_url: str, secret: Optional[str] = None):
        self.webhook_url = webhook_url
        self.secret = secret
    
    def _generate_sign(self, timestamp: int) -> str:
        """生成签名"""
        if not self.secret:
            return ""
        
        secret_enc = self.secret.encode('utf-8')
        string_to_sign = f"{timestamp}\n{self.secret}"
        string_to_sign_enc = string_to_sign.encode('utf-8')
        
        hmac_code = hmac.new(
            secret_enc,
            string_to_sign_enc,
            digestmod=hashlib.sha256
        ).digest()
        sign = base64.b64encode(hmac_code).decode('utf-8')
        return sign
    
    def send_text(self, text: str, at_mobiles: List[str] = None, at_all: bool = False) -> bool:
        """发送文本消息"""
        timestamp = int(time.time() * 1000)
        
        payload = {
            "msgtype": "text",
            "text": {
                "content": text
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all
            }
        }
        
        url = self.webhook_url
        if self.secret:
            sign = self._generate_sign(timestamp)
            url = f"{url}&timestamp={timestamp}&sign={sign}"
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            result = response.json()
            return result.get("errcode") == 0
        except Exception as e:
            print(f"发送钉钉消息失败: {e}")
            return False
    
    def send_markdown(self, title: str, text: str, at_mobiles: List[str] = None) -> bool:
        """发送 Markdown 消息"""
        timestamp = int(time.time() * 1000)
        
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "isAtAll": False
            }
        }
        
        url = self.webhook_url
        if self.secret:
            sign = self._generate_sign(timestamp)
            url = f"{url}&timestamp={timestamp}&sign={sign}"
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            result = response.json()
            return result.get("errcode") == 0
        except Exception as e:
            print(f"发送钉钉Markdown失败: {e}")
            return False
    
    def send_alert(self, alert_type: str, message: str, details: Dict = None) -> bool:
        """发送告警消息"""
        
        emoji_map = {
            "error": "🚨",
            "warning": "⚠️",
            "info": "ℹ️",
            "success": "✅"
        }
        
        emoji = emoji_map.get(alert_type, "📢")
        title = f"{emoji} {alert_type.upper()} 告警"
        
        markdown_text = f"### {title}\n\n{message}\n\n"
        
        if details:
            markdown_text += "#### 详细信息\n\n"
            for key, value in details.items():
                markdown_text += f"- **{key}**: {value}\n"
        
        markdown_text += f"\n> 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return self.send_markdown(title, markdown_text)
    
    def send_daily_report(self, report_data: Dict) -> bool:
        """发送每日报告"""
        
        title = "📊 每日交易报告"
        
        markdown_text = f"### {title}\n\n"
        markdown_text += f"{report_data.get('summary', '无')}\n\n"
        markdown_text += "#### 关键指标\n\n"
        markdown_text += f"- **总资产**: ${report_data.get('total_assets', 0):,.2f}\n"
        markdown_text += f"- **当日盈亏**: {report_data.get('daily_pnl', 0):+.2f}%\n"
        markdown_text += f"- **持仓数量**: {report_data.get('position_count', 0)}\n"
        markdown_text += f"- **今日交易**: {report_data.get('trade_count', 0)}笔\n"
        markdown_text += f"\n> 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return self.send_markdown(title, markdown_text)


# ============================================================
# 统一接口
# ============================================================

class NotificationManager:
    """通知管理器（统一接口）"""
    
    def __init__(self):
        self.feishu_bots: List[FeishuBot] = []
        self.dingtalk_bots: List[DingTalkBot] = []
    
    def add_feishu(self, webhook_url: str, secret: Optional[str] = None):
        """添加飞书机器人"""
        self.feishu_bots.append(FeishuBot(webhook_url, secret))
    
    def add_dingtalk(self, webhook_url: str, secret: Optional[str] = None):
        """添加钉钉机器人"""
        self.dingtalk_bots.append(DingTalkBot(webhook_url, secret))
    
    def send_text(self, text: str) -> Dict[str, bool]:
        """发送文本消息到所有平台"""
        results = {}
        
        for i, bot in enumerate(self.feishu_bots):
            results[f"feishu_{i}"] = bot.send_text(text)
        
        for i, bot in enumerate(self.dingtalk_bots):
            results[f"dingtalk_{i}"] = bot.send_text(text)
        
        return results
    
    def send_alert(self, alert_type: str, message: str, details: Dict = None) -> Dict[str, bool]:
        """发送告警到所有平台"""
        results = {}
        
        for i, bot in enumerate(self.feishu_bots):
            results[f"feishu_{i}"] = bot.send_alert(alert_type, message, details)
        
        for i, bot in enumerate(self.dingtalk_bots):
            results[f"dingtalk_{i}"] = bot.send_alert(alert_type, message, details)
        
        return results
    
    def send_daily_report(self, report_data: Dict) -> Dict[str, bool]:
        """发送每日报告到所有平台"""
        results = {}
        
        for i, bot in enumerate(self.feishu_bots):
            results[f"feishu_{i}"] = bot.send_daily_report(report_data)
        
        for i, bot in enumerate(self.dingtalk_bots):
            results[f"dingtalk_{i}"] = bot.send_daily_report(report_data)
        
        return results


# ============================================================
# 配置管理
# ============================================================

import os

NOTIFICATION_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "notification_config.json"
)

def load_notification_config() -> NotificationManager:
    """从配置文件加载通知配置"""
    manager = NotificationManager()
    
    if not os.path.exists(NOTIFICATION_CONFIG_PATH):
        # 创建默认配置文件
        default_config = {
            "feishu_bots": [
                # {
                #     "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_WEBHOOK",
                #     "secret": "YOUR_SECRET"
                # }
            ],
            "dingtalk_bots": [
                # {
                #     "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN",
                #     "secret": "YOUR_SECRET"
                # }
            ]
        }
        with open(NOTIFICATION_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)
        return manager
    
    try:
        with open(NOTIFICATION_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        for bot_config in config.get("feishu_bots", []):
            manager.add_feishu(
                webhook_url=bot_config["webhook_url"],
                secret=bot_config.get("secret")
            )
        
        for bot_config in config.get("dingtalk_bots", []):
            manager.add_dingtalk(
                webhook_url=bot_config["webhook_url"],
                secret=bot_config.get("secret")
            )
    
    except Exception as e:
        print(f"加载通知配置失败: {e}")
    
    return manager


# ============================================================
# 单例
# ============================================================

_notification_manager = None

def get_notification_manager() -> NotificationManager:
    global _notification_manager
    if _notification_manager is None:
        _notification_manager = load_notification_config()
    return _notification_manager


# ============================================================
# 测试代码
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("飞书/钉钉机器人测试")
    print("=" * 60)
    
    # 创建通知管理器
    manager = get_notification_manager()
    
    print("\n请在 notification_config.json 中配置 Webhook URL 后再测试")
    print("\n配置示例：")
    print(json.dumps({
        "feishu_bots": [
            {
                "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_WEBHOOK",
                "secret": "YOUR_SECRET"
            }
        ],
        "dingtalk_bots": [
            {
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN",
                "secret": "YOUR_SECRET"
            }
        ]
    }, indent=2, ensure_ascii=False))
    
    # 测试发送（需要配置）
    # print("\n发送测试消息...")
    # manager.send_text("这是一条测试消息")
    
    # print("\n发送告警...")
    # manager.send_alert("warning", "TSLA 跌破 $200", {"当前价": "$195.50", "跌幅": "-5.2%"})
    
    # print("\n发送每日报告...")
    # manager.send_daily_report({
    #     "total_assets": 100000,
    #     "daily_pnl": 2.5,
    #     "position_count": 5,
    #     "trade_count": 3,
    #     "summary": "今日盈利 $2,500，科技股表现强势"
    # })