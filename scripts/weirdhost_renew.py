#!/usr/bin/env python3
"""
WeirdHost 自动续期 v29
- 修复 SeleniumBase UC Mode API
"""

import os
import sys
import json
import time
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional, Dict
from base64 import b64encode, b64decode

# ==================== 配置 ====================

BASE_URL = "https://hub.weirdhost.xyz"
SCREENSHOT_PATH = "debug_result.png"
MAX_WAIT_API = 15

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
                print(f"[GitHub] ✓ Secret 已更新")
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
        
        urllib.request.urlopen(req, timeout=30)
        print("[TG] ✓ 文本通知已发送")
        return True
    except Exception as e:
        print(f"[TG] ✗ 发送失败: {e}")
        return False


def notify_telegram(message: str, photo_path: Optional[str] = None):
    if photo_path and os.path.exists(photo_path):
        if send_telegram_photo(photo_path, message):
            return
    send_telegram_text(message)


# ==================== 浏览器自动化 ====================

def parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    cookie_str = urllib.parse.unquote(cookie_str.strip())
    cookies = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def run_browser_renew(cookie_str: str, server_id: str, socks_proxy: Optional[str] = None) -> Dict:
    from seleniumbase import SB
    
    result = {
        "success": False,
        "is_cooldown": False,
        "cookie_expired": False,
        "message": "",
        "expiry": "",
        "new_cookie": None,
        "screenshot": None
    }
    
    cookies = parse_cookie_string(cookie_str)
    server_url = f"{BASE_URL}/server/{server_id}"
    
    # 构建代理参数
    proxy_arg = None
    if socks_proxy:
        proxy_addr = socks_proxy.replace("socks5://", "").replace("socks5h://", "")
        proxy_arg = f"socks5://{proxy_addr}"
        print(f"[浏览器] 使用 SOCKS5 代理: {proxy_addr}")
    
    try:
        print("[浏览器] 启动 Chrome (UC Mode)...")
        
        with SB(uc=True, headless=True, proxy=proxy_arg) as sb:
            # 访问主页设置 Cookie
            print(f"[浏览器] 访问 {BASE_URL}")
            sb.uc_open_with_reconnect(BASE_URL, reconnect_time=5)
            
            # 处理 CF 验证
            try:
                if sb.is_text_visible("Just a moment", timeout=3):
                    print("[浏览器] 检测到 CF 验证，尝试绕过...")
                    sb.uc_gui_click_captcha()
                    sb.sleep(3)
            except:
                pass
            
            # 添加 Cookie
            for name, value in cookies.items():
                try:
                    sb.add_cookie({"name": name, "value": value, "domain": "hub.weirdhost.xyz"})
                except:
                    pass
            
            # 访问服务器页面
            print(f"[浏览器] 访问 {server_url}")
            sb.uc_open_with_reconnect(server_url, reconnect_time=5)
            
            # 处理 CF 验证
            try:
                if sb.is_text_visible("Just a moment", timeout=3):
                    print("[浏览器] 检测到 CF 验证，尝试绕过...")
                    sb.uc_gui_click_captcha()
                    sb.sleep(3)
            except:
                pass
            
            # 检查登录状态
            if "/login" in sb.get_current_url():
                result["cookie_expired"] = True
                result["message"] = "Cookie 已失效"
                sb.save_screenshot(SCREENSHOT_PATH)
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            # 等待页面加载
            print("[浏览器] 等待页面加载...")
            sb.sleep(5)
            sb.save_screenshot("debug_before.png")
            
            # 滚动页面
            sb.execute_script("window.scrollTo(0, document.body.scrollHeight)")
            sb.sleep(2)
            
            # 获取到期时间
            page_source = sb.get_page_source()
            expiry_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', page_source)
            if expiry_match:
                result["expiry"] = expiry_match.group(1)
                print(f"[浏览器] 到期时间: {result['expiry']}")
            
            # 查找续期按钮
            print("[浏览器] 查找续期按钮...")
            button_xpath = "//button[contains(text(), '시간추가')]"
            
            try:
                if sb.is_element_present(button_xpath):
                    print("[浏览器] 找到续期按钮")
                    
                    # 滚动到按钮
                    sb.execute_script(
                        "document.evaluate(arguments[0], document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue.scrollIntoView({block: 'center'});",
                        button_xpath
                    )
                    sb.sleep(1)
                    
                    # 点击按钮
                    print("[浏览器] 点击续期按钮...")
                    sb.uc_click(button_xpath)
                else:
                    result["message"] = "未找到续期按钮"
                    sb.save_screenshot(SCREENSHOT_PATH)
                    result["screenshot"] = SCREENSHOT_PATH
                    return result
            except Exception as e:
                print(f"[浏览器] 按钮操作异常: {e}")
                # 尝试备用方法
                try:
                    sb.click(button_xpath)
                except:
                    result["message"] = f"点击按钮失败: {e}"
                    sb.save_screenshot(SCREENSHOT_PATH)
                    result["screenshot"] = SCREENSHOT_PATH
                    return result
            
            # 等待结果
            print("[浏览器] 等待操作结果...")
            
            cooldown_keywords = [
                "아직 서버를 갱신할 수 없습니다",
                "남은 시간이 더 줄어들 때까지",
                "갱신할 수 없습니다",
                "기다려주세요",
            ]
            
            success_keywords = [
                "갱신되었습니다",
                "연장되었습니다",
                "추가되었습니다",
                "시간이 추가",
            ]
            
            for i in range(MAX_WAIT_API):
                sb.sleep(1)
                
                try:
                    current_source = sb.get_page_source()
                    
                    # 优先检查冷却期
                    for kw in cooldown_keywords:
                        if kw in current_source:
                            result["is_cooldown"] = True
                            result["message"] = "冷却期内，请稍后再试"
                            print(f"[浏览器] 检测到冷却期: '{kw}' ({i+1}秒)")
                            break
                    
                    if result["is_cooldown"]:
                        break
                    
                    # 检查成功
                    for kw in success_keywords:
                        if kw in current_source:
                            result["success"] = True
                            result["message"] = "续期成功"
                            print(f"[浏览器] 检测到成功: '{kw}' ({i+1}秒)")
                            break
                    
                    if result["success"]:
                        break
                    
                    # 检查到期时间变化
                    new_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', current_source)
                    if new_match:
                        new_expiry = new_match.group(1)
                        if result["expiry"] and new_expiry != result["expiry"]:
                            result["success"] = True
                            result["expiry"] = new_expiry
                            result["message"] = "续期成功"
                            print(f"[浏览器] 到期时间已更新: {new_expiry} ({i+1}秒)")
                            break
                    
                except Exception as e:
                    print(f"[浏览器] 检测异常: {e}")
                
                if i % 5 == 4:
                    print(f"[浏览器] 等待中... ({i+1}秒)")
            
            # 保存截图
            sb.sleep(2)
            sb.save_screenshot(SCREENSHOT_PATH)
            result["screenshot"] = SCREENSHOT_PATH
            print("[浏览器] 已保存截图")
            
            if result["success"]:
                print("[浏览器] ✓ 续期成功")
                sb.refresh()
                sb.sleep(3)
                new_source = sb.get_page_source()
                new_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', new_source)
                if new_match:
                    result["expiry"] = new_match.group(1)
                sb.save_screenshot(SCREENSHOT_PATH)
            elif result["is_cooldown"]:
                print("[浏览器] ⏳ 冷却期内")
            else:
                result["message"] = "未检测到明确结果"
                print("[浏览器] ✗ 未检测到明确结果")
            
            # 获取新 Cookie
            try:
                for cookie in sb.get_cookies():
                    if cookie["name"].startswith("remember_web"):
                        new_cookie_str = f"{cookie['name']}={cookie['value']}"
                        if new_cookie_str != cookie_str:
                            result["new_cookie"] = new_cookie_str
                        break
            except:
                pass
        
    except Exception as e:
        result["message"] = str(e)
        print(f"[浏览器] ✗ 异常: {e}")
        import traceback
        traceback.print_exc()
    
    return result


# ==================== 主函数 ====================

def main():
    cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_id = os.environ.get("WEIRDHOST_ID", "").strip()
    socks_proxy = os.environ.get("SOCKS_PROXY", "").strip()
    
    if not cookie or not server_id:
        print("❌ 请设置 WEIRDHOST_COOKIE 和 WEIRDHOST_ID")
        sys.exit(1)
    
    print("=" * 50)
    print("WeirdHost 自动续期 v29 (SeleniumBase UC Mode)")
    print("=" * 50)
    print(f"服务器 ID: {server_id}")
    print(f"SOCKS5 代理: {socks_proxy if socks_proxy else '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    result = run_browser_renew(cookie, server_id, socks_proxy if socks_proxy else None)
    
    if result.get("new_cookie"):
        update_github_secret("WEIRDHOST_COOKIE", result["new_cookie"])
    
    remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
    screenshot = result.get("screenshot")
    
    if result["success"]:
        msg = f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, screenshot)
        sys.exit(0)
    elif result["is_cooldown"]:
        print(f"[结果] 冷却期内，跳过通知")
        print(f"[信息] 到期: {result['expiry']}，剩余: {remaining}")
        sys.exit(0)
    elif result["cookie_expired"]:
        notify_telegram("❌ <b>WeirdHost Cookie 已失效</b>\n\n请手动更新", screenshot)
        sys.exit(1)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n{result['message']}", screenshot)
        sys.exit(1)


if __name__ == "__main__":
    main()
