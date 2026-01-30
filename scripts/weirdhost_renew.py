#!/usr/bin/env python3
"""
WeirdHost 自动续期 v13 - API 直接调用版
"""

import os
import sys
import json
import time
import base64
import platform
import urllib.parse
import urllib.request
import ssl
import re
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.error import HTTPError, URLError

# ==================== 配置 ====================

BASE_URL = "https://hub.weirdhost.xyz"
API_BASE = f"{BASE_URL}/api/client"

# ==================== 工具函数 ====================

def mask_string(s: str, show: int = 4) -> str:
    if not s:
        return "***"
    if len(s) <= show * 2:
        return "*" * len(s)
    return f"{s[:show]}****{s[-show:]}"


def parse_cookie(cookie_str: str) -> tuple:
    cookie_str = urllib.parse.unquote(cookie_str.strip())
    if "=" in cookie_str:
        parts = cookie_str.split("=", 1)
        return (parts[0].strip(), parts[1].strip())
    return ("remember_web", cookie_str.strip())


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


def extract_server_id(url: str) -> Optional[str]:
    match = re.search(r'/server/([a-f0-9-]{36})', url)
    if match:
        return match.group(1)
    if re.match(r'^[a-f0-9-]{36}$', url):
        return url
    return None


# ==================== HTTP 客户端 ====================

class APIClient:
    def __init__(self, cookie_str: str, proxy: Optional[str] = None):
        self.cookie_name, self.cookie_value = parse_cookie(cookie_str)
        self.proxy = proxy
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        handlers = [urllib.request.HTTPSHandler(context=self.ssl_context)]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
        
        self.opener = urllib.request.build_opener(*handlers)
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Cookie": f"{self.cookie_name}={self.cookie_value}",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/",
            "X-Requested-With": "XMLHttpRequest",
        }
    
    def request(self, method: str, url: str, data: Any = None, timeout: int = 30) -> Dict:
        result = {"success": False, "status": 0, "data": None, "error": None}
        
        try:
            headers = self._get_headers()
            body = json.dumps(data).encode('utf-8') if data is not None else None
            
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            
            with self.opener.open(req, timeout=timeout) as resp:
                result["status"] = resp.status
                body = resp.read().decode('utf-8')
                try:
                    result["data"] = json.loads(body)
                except:
                    result["data"] = body
                result["success"] = resp.status in (200, 201, 204)
                
        except HTTPError as e:
            result["status"] = e.code
            try:
                result["data"] = json.loads(e.read().decode('utf-8'))
            except:
                result["data"] = str(e)
            result["error"] = f"HTTP {e.code}"
        except URLError as e:
            result["error"] = f"连接错误: {e.reason}"
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    def get(self, url: str, timeout: int = 30) -> Dict:
        return self.request("GET", url, timeout=timeout)
    
    def post(self, url: str, data: Any = None, timeout: int = 30) -> Dict:
        return self.request("POST", url, data=data, timeout=timeout)


# ==================== Telegram 通知 ====================

def notify_telegram(message: str, proxy: Optional[str] = None):
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }).encode()
        
        req = urllib.request.Request(url, data=data)
        
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
            )
            opener.open(req, timeout=30)
        else:
            urllib.request.urlopen(req, timeout=30)
        
        print("[TG] ✓ 通知已发送")
    except Exception as e:
        print(f"[TG] ✗ 发送失败: {e}")


# ==================== GitHub Secret 更新 ====================

def update_github_secret(secret_name: str, secret_value: str) -> bool:
    try:
        from nacl import encoding, public
    except ImportError:
        return False
    
    repo_token = os.environ.get("REPO_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    
    if not repo_token or not repository:
        return False
    
    try:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {repo_token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Python"
        }
        
        pk_url = f"https://api.github.com/repos/{repository}/actions/secrets/public-key"
        req = urllib.request.Request(pk_url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            pk_data = json.loads(resp.read().decode())
        
        pk = public.PublicKey(pk_data["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(pk)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        encrypted_value = base64.b64encode(encrypted).decode("utf-8")
        
        secret_url = f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}"
        payload = json.dumps({
            "encrypted_value": encrypted_value,
            "key_id": pk_data["key_id"]
        }).encode()
        
        req = urllib.request.Request(secret_url, data=payload, headers=headers, method="PUT")
        req.add_header("Content-Type", "application/json")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (201, 204):
                print(f"[GitHub] ✓ Secret {secret_name} 已更新")
                return True
        return False
    except Exception as e:
        print(f"[GitHub] ✗ 更新失败: {e}")
        return False


# ==================== 主要功能 ====================

def get_server_info(client: APIClient, server_id: str) -> Dict:
    print("[API] 获取服务器信息...")
    
    url = f"{API_BASE}/notfreeservers/{server_id}"
    resp = client.get(url)
    
    if resp["success"] and resp["data"]:
        server = resp["data"].get("attributes", resp["data"])
        return {
            "success": True,
            "name": server.get("name", "Unknown"),
            "expiry": server.get("renewal_date") or server.get("expiry_date"),
            "data": server
        }
    
    return {"success": False, "error": resp.get("error", "无法获取")}


def renew_server(client: APIClient, server_id: str) -> Dict:
    print("[API] 发送续期请求...")
    
    url = f"{API_BASE}/notfreeservers/{server_id}/renew"
    resp = client.post(url, data={})
    
    result = {"success": False, "is_cooldown": False, "message": ""}
    
    if resp["success"]:
        result["success"] = True
        result["message"] = "续期成功"
        print(f"[API] ✓ {result['message']}")
        
    elif resp["status"] == 400:
        data = resp["data"]
        if isinstance(data, dict):
            errors = data.get("errors", [])
            if errors:
                detail = errors[0].get("detail", "")
                if "아직" in detail or "갱신할 수 없습니다" in detail:
                    result["is_cooldown"] = True
                    result["message"] = "冷却期内"
                    print(f"[API] ⏳ {result['message']}")
                else:
                    result["message"] = detail
                    print(f"[API] ✗ {result['message']}")
        
    elif resp["status"] in (401, 403):
        result["message"] = "Cookie 已失效"
        print(f"[API] ✗ {result['message']}")
        
    elif resp["status"] == 404:
        result["message"] = "服务器不存在"
        print(f"[API] ✗ {result['message']}")
        
    else:
        result["message"] = resp.get("error", f"HTTP {resp['status']}")
        print(f"[API] ✗ {result['message']}")
    
    return result


# ==================== 主函数 ====================

def main():
    cookie_str = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_url = os.environ.get("WEIRDHOST_SERVER_URL", "").strip()
    proxy = os.environ.get("HTTP_PROXY", "").strip()
    
    if not cookie_str:
        print("❌ 请设置 WEIRDHOST_COOKIE")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\nCookie 未设置")
        sys.exit(1)
    
    if not server_url:
        print("❌ 请设置 WEIRDHOST_SERVER_URL")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\nServer URL 未设置")
        sys.exit(1)
    
    server_id = extract_server_id(server_url)
    if not server_id:
        print(f"❌ 无法提取服务器 ID")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\n无法提取服务器 ID")
        sys.exit(1)
    
    print("=" * 60)
    print("WeirdHost 自动续期 v13 (API)")
    print("=" * 60)
    print(f"代理: {proxy if proxy else '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    client = APIClient(cookie_str, proxy if proxy else None)
    
    result = {"success": False, "message": "", "expiry": "", "is_cooldown": False}
    
    try:
        # 获取服务器信息
        info = get_server_info(client, server_id)
        if info.get("success"):
            result["expiry"] = info.get("expiry", "")
            if result["expiry"]:
                remaining = calculate_remaining_time(result["expiry"])
                print(f"[服务器] 到期: {result['expiry']} ({remaining})")
        
        # 续期
        renew = renew_server(client, server_id)
        result["success"] = renew["success"]
        result["is_cooldown"] = renew["is_cooldown"]
        result["message"] = renew["message"]
        
        # 获取新到期时间
        if renew["success"]:
            time.sleep(2)
            new_info = get_server_info(client, server_id)
            if new_info.get("success") and new_info.get("expiry"):
                result["expiry"] = new_info["expiry"]
                remaining = calculate_remaining_time(result["expiry"])
                print(f"[续期] 新到期: {result['expiry']} ({remaining})")
    
    except Exception as e:
        print(f"[异常] {e}")
        result["message"] = str(e)
    
    # 通知
    if result["success"]:
        remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
        msg = f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, proxy)
        sys.exit(0)
    elif result["is_cooldown"]:
        remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
        msg = f"ℹ️ <b>WeirdHost 冷却期</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, proxy)
        sys.exit(0)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n❗ {result['message']}", proxy)
        sys.exit(1)


if __name__ == "__main__":
    main()
