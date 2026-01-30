#!/usr/bin/env python3
"""
WeirdHost 自动续期 v15
"""

import os
import sys
import json
import time
import urllib.parse
import urllib.request
import ssl
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.error import HTTPError, URLError

# ==================== 配置 ====================

BASE_URL = "https://hub.weirdhost.xyz"
API_BASE = f"{BASE_URL}/api/client"

# ==================== 工具函数 ====================

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


# ==================== HTTP 客户端 ====================

class APIClient:
    def __init__(self, cookie_str: str, proxy: Optional[str] = None):
        self.cookie_name, self.cookie_value = parse_cookie(cookie_str)
        self.proxy = proxy
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        
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
            "Content-Type": "application/json",
            "Cookie": f"{self.cookie_name}={self.cookie_value}",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/",
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


# ==================== GitHub API ====================

def update_github_secret(secret_name: str, secret_value: str) -> bool:
    """更新 GitHub Secret"""
    token = os.environ.get("REPO_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    
    if not token or not repo:
        print("[GitHub] 未配置 REPO_TOKEN 或 GITHUB_REPOSITORY")
        return False
    
    try:
        # 获取公钥
        key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        req = urllib.request.Request(key_url)
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            key_data = json.loads(resp.read().decode('utf-8'))
        
        public_key = key_data["key"]
        key_id = key_data["key_id"]
        
        # 加密 secret
        from base64 import b64encode, b64decode
        try:
            from nacl import encoding, public
            
            public_key_bytes = b64decode(public_key)
            sealed_box = public.SealedBox(public.PublicKey(public_key_bytes))
            encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
            encrypted_value = b64encode(encrypted).decode('utf-8')
        except ImportError:
            print("[GitHub] 需要 PyNaCl 库来加密 Secret")
            return False
        
        # 更新 secret
        secret_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        data = json.dumps({
            "encrypted_value": encrypted_value,
            "key_id": key_id
        }).encode('utf-8')
        
        req = urllib.request.Request(secret_url, data=data, method="PUT")
        req.add_header("Authorization", f"token {token}")
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("Content-Type", "application/json")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (201, 204):
                print(f"[GitHub] ✓ Secret {secret_name} 已更新")
                return True
        
        return False
        
    except Exception as e:
        print(f"[GitHub] ✗ 更新失败: {e}")
        return False


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
    cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_id = os.environ.get("WEIRDHOST_ID", "").strip()
    proxy = os.environ.get("HTTP_PROXY", "").strip()
    
    if not cookie:
        print("❌ 请设置 WEIRDHOST_COOKIE")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\nCookie 未设置")
        sys.exit(1)
    
    if not server_id:
        print("❌ 请设置 WEIRDHOST_ID")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\nServer ID 未设置")
        sys.exit(1)
    
    print("=" * 50)
    print("WeirdHost 自动续期 v15")
    print("=" * 50)
    print(f"服务器 ID: {server_id}")
    print(f"代理: {proxy if proxy else '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    client = APIClient(cookie, proxy if proxy else None)
    
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
