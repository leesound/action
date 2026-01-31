#!/usr/bin/env python3
"""
WeirdHost 自动续期 v35
- 修复成功判断逻辑：必须日期变化才算成功
- 正确等待 Turnstile 验证完成
"""

import os
import sys
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional, Dict
from base64 import b64encode, b64decode

# ==================== 配置 ====================

BASE_URL = "https://hub.weirdhost.xyz"
SCREENSHOT_PATH = "debug_result.png"

# ==================== 工具函数 ====================

def calculate_remaining_time(expiry_str: str) -> str:
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return "无法解析"
      
        diff = expiry_dt - datetime.now()
        if diff.total_seconds() < 0:
            return "已过期"
      
        days = diff.days
        hours = diff.seconds // 3600
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        return " ".join(parts) if parts else "不到1小时"
    except:
        return "计算失败"


def screenshot(sb, name: str) -> str:
    path = f"./{name}.png"
    try:
        sb.save_screenshot(path)
        print(f"[截图] {path}")
    except Exception as e:
        print(f"[截图] 失败: {e}")
    return path


# ==================== GitHub API ====================

def update_github_secret(secret_name: str, secret_value: str) -> bool:
    token = os.environ.get("REPO_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
  
    if not token or not repo:
        return False
  
    try:
        key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        req = urllib.request.Request(key_url)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "WeirdHost-Renew")
      
        with urllib.request.urlopen(req, timeout=30) as resp:
            key_data = json.loads(resp.read().decode('utf-8'))
      
        from nacl import encoding, public
        public_key_bytes = b64decode(key_data["key"])
        sealed_box = public.SealedBox(public.PublicKey(public_key_bytes))
        encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
        encrypted_value = b64encode(encrypted).decode('utf-8')
      
        secret_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        data = json.dumps({
            "encrypted_value": encrypted_value,
            "key_id": key_data["key_id"]
        }).encode('utf-8')
      
        req = urllib.request.Request(secret_url, data=data, method="PUT")
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "WeirdHost-Renew")
      
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (201, 204):
                print("[GitHub] ✓ Secret 已更新")
                return True
        return False
    except Exception as e:
        print(f"[GitHub] ✗ 更新失败: {e}")
        return False


# ==================== Telegram 通知 ====================

def send_telegram_photo(photo_path: str, caption: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
  
    if not os.path.exists(photo_path):
        return False
  
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
      
        with open(photo_path, "rb") as f:
            photo_data = f.read()
      
        body = []
        body.append(f"--{boundary}".encode())
        body.append(b'Content-Disposition: form-data; name="chat_id"')
        body.append(b"")
        body.append(chat_id.encode())
      
        body.append(f"--{boundary}".encode())
        body.append(b'Content-Disposition: form-data; name="caption"')
        body.append(b"")
        body.append(caption.encode('utf-8'))
      
        body.append(f"--{boundary}".encode())
        body.append(b'Content-Disposition: form-data; name="parse_mode"')
        body.append(b"")
        body.append(b"HTML")
      
        body.append(f"--{boundary}".encode())
        body.append(b'Content-Disposition: form-data; name="photo"; filename="screenshot.png"')
        body.append(b"Content-Type: image/png")
        body.append(b"")
        body.append(photo_data)
      
        body.append(f"--{boundary}--".encode())
        body.append(b"")
      
        body_bytes = b"\r\n".join(body)
      
        req = urllib.request.Request(url, data=body_bytes, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
      
        urllib.request.urlopen(req, timeout=60)
        print("[TG] ✓ 图片通知已发送")
        return True
    except Exception as e:
        print(f"[TG] ✗ 图片发送失败: {e}")
        return False


def send_telegram_text(message: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
  
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }).encode('utf-8')
      
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
      
        urllib.request.urlopen(req, timeout
