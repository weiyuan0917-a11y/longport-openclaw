"""
飞书/钉钉发送诊断：打印每个机器人的请求与响应，便于排查「发送失败」原因。
不暴露 secret，只打印状态码与 API 返回的 code/msg。
"""
import sys
import os
import json
import time
import hmac
import hashlib
import base64
import requests

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

NOTIFICATION_CONFIG_PATH = os.path.join(_dir, "notification_config.json")


def feishu_send_with_debug(webhook_url: str, secret: str | None, text: str) -> None:
    """飞书：发一条文本并打印响应"""
    timestamp = int(time.time())
    payload = {"msg_type": "text", "content": {"text": text}}
    if secret:
        string_to_sign = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        payload["timestamp"] = str(timestamp)
        payload["sign"] = sign
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        print("[Feishu] status=%s  body=%s" % (r.status_code, json.dumps(body, ensure_ascii=False)))
        if isinstance(body, dict) and body.get("code") != 0:
            print("  -> 失败原因: code=%s msg=%s" % (body.get("code"), body.get("msg", "")))
    except Exception as e:
        print("[Feishu] 请求异常: %s" % e)


def dingtalk_send_with_debug(webhook_url: str, secret: str | None, text: str) -> None:
    """钉钉：发一条文本并打印响应"""
    timestamp = int(time.time() * 1000)
    payload = {"msgtype": "text", "text": {"content": text}, "at": {"atMobiles": [], "isAtAll": False}}
    url = webhook_url
    if secret:
        string_to_sign = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        url = f"{url}&timestamp={timestamp}&sign={sign}"
    try:
        r = requests.post(url, json=payload, timeout=10)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        print("[DingTalk] status=%s  body=%s" % (r.status_code, json.dumps(body, ensure_ascii=False)))
        if isinstance(body, dict) and body.get("errcode") != 0:
            print("  -> 失败原因: errcode=%s errmsg=%s" % (body.get("errcode"), body.get("errmsg", "")))
    except Exception as e:
        print("[DingTalk] 请求异常: %s" % e)


def main():
    if not os.path.exists(NOTIFICATION_CONFIG_PATH):
        print("notification_config.json 不存在")
        return
    with open(NOTIFICATION_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    print("=== 飞书 ===")
    for i, c in enumerate(config.get("feishu_bots", [])):
        url = c.get("webhook_url", "")
        secret = c.get("secret")
        if not url:
            print("[Feishu %d] 未配置 webhook_url" % i)
            continue
        print("[Feishu %d] 发送测试..." % i)
        feishu_send_with_debug(url, secret, "test_notification_debug")
    print("\n=== 钉钉 ===")
    for i, c in enumerate(config.get("dingtalk_bots", [])):
        url = c.get("webhook_url", "")
        secret = c.get("secret")
        if not url:
            print("[DingTalk %d] 未配置 webhook_url" % i)
            continue
        print("[DingTalk %d] 发送测试..." % i)
        dingtalk_send_with_debug(url, secret, "test_notification_debug")
    print("\n完成。若 code/errcode 非 0，请根据 msg/errmsg 排查（如签名错误、关键词、IP 白名单等）。")


if __name__ == "__main__":
    main()
