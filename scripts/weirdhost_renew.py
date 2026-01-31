#!/usr/bin/env python3
"""
WeirdHost 自动续期 v33
- 使用非 headless 模式 + Xvfb
- 正确处理 Turnstile 验证
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
    """保存截图"""
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


# ==================== Turnstile 处理 ====================

def check_turnstile_present(sb) -> bool:
    """检查是否存在 Turnstile 验证"""
    try:
        page_source = sb.get_page_source()
        return any([
            "cf-turnstile" in page_source,
            "Verify you are human" in page_source,
            "challenges.cloudflare.com" in page_source
        ])
    except:
        return False


def check_turnstile_completed(sb) -> bool:
    """检查 Turnstile 是否已完成"""
    try:
        # 检查是否有 turnstile response
        result = sb.execute_script("""
            // 检查 turnstile response input
            var inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
            for (var i = 0; i < inputs.length; i++) {
                if (inputs[i].value && inputs[i].value.length > 20) {
                    return true;
                }
            }
            // 检查 turnstile 是否显示成功状态
            var frames = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
            if (frames.length === 0) {
                // 没有 turnstile iframe，可能已完成或不存在
                var turnstile = document.querySelector('.cf-turnstile');
                if (!turnstile) return true;  // 没有 turnstile 元素
            }
            return false;
        """)
        return bool(result)
    except:
        return False


def wait_for_turnstile(sb, timeout: int = 60) -> bool:
    """等待 Turnstile 验证完成"""
    print("[Turnstile] 等待验证...")
    
    start_time = time.time()
    clicked = False
    
    while time.time() - start_time < timeout:
        # 检查是否完成
        if check_turnstile_completed(sb):
            print("[Turnstile] ✓ 验证完成")
            return True
        
        # 检查是否还存在
        if not check_turnstile_present(sb):
            print("[Turnstile] ✓ 验证已消失")
            return True
        
        # 尝试点击
        if not clicked:
            try:
                print("[Turnstile] 尝试点击验证框...")
                sb.uc_gui_click_captcha()
                clicked = True
                time.sleep(3)
            except Exception as e:
                print(f"[Turnstile] 点击失败: {e}")
                clicked = True  # 避免重复尝试
        
        time.sleep(1)
    
    # 最后检查一次
    return check_turnstile_completed(sb) or not check_turnstile_present(sb)


def wait_for_cloudflare(sb, timeout: int = 30) -> bool:
    """等待 Cloudflare 初始验证通过"""
    print("[CF] 检查 Cloudflare 验证...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            page_source = sb.get_page_source().lower()
            current_url = sb.get_current_url().lower()
            
            # 检查是否已通过
            if any([
                "hub.weirdhost.xyz/server" in current_url,
                "hub.weirdhost.xyz/dashboard" in current_url,
                "유통기한" in sb.get_page_source(),  # 韩文"到期时间"
                "server controls" in page_source,
            ]):
                print("[CF] ✓ 验证通过")
                return True
            
            # 检查是否在验证中
            if "just a moment" in page_source or "checking your browser" in page_source:
                time.sleep(1)
                continue
            
            time.sleep(1)
        except:
            time.sleep(1)
    
    print("[CF] ⚠ 验证超时")
    return False


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
    
    # 构建 SB 参数
    sb_kwargs = {
        "uc": True,
        "test": True,
        "locale": "en",
        "headless": False,  # 非 headless 模式，配合 Xvfb
        "uc_cdp_events": True,
    }
    
    if socks_proxy:
        proxy_addr = socks_proxy.replace("socks5://", "").replace("socks5h://", "")
        sb_kwargs["proxy"] = f"socks5://{proxy_addr}"
        print(f"[浏览器] 使用代理: {proxy_addr}")
    
    try:
        print("[浏览器] 启动 Chrome (UC Mode, 非 headless)...")
        
        with SB(**sb_kwargs) as sb:
            # 访问主页设置 Cookie
            print(f"[浏览器] 访问 {BASE_URL}")
            sb.uc_open_with_reconnect(BASE_URL, reconnect_time=6)
            time.sleep(3)
            
            # 处理初始 CF 验证
            wait_for_cloudflare(sb, 30)
            screenshot(sb, "01-homepage")
            
            # 添加 Cookie
            for name, value in cookies.items():
                try:
                    sb.add_cookie({"name": name, "value": value, "domain": "hub.weirdhost.xyz"})
                except:
                    pass
            
            # 访问服务器页面
            print(f"[浏览器] 访问 {server_url}")
            sb.uc_open_with_reconnect(server_url, reconnect_time=6)
            time.sleep(3)
            
            # 处理 CF 验证
            wait_for_cloudflare(sb, 30)
            screenshot(sb, "02-server-page")
            
            # 检查登录状态
            if "/login" in sb.get_current_url():
                result["cookie_expired"] = True
                result["message"] = "Cookie 已失效"
                screenshot(sb, SCREENSHOT_PATH.replace(".png", ""))
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            # 等待页面加载
            print("[浏览器] 等待页面加载...")
            time.sleep(5)
            
            # 获取到期时间
            page_source = sb.get_page_source()
            expiry_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', page_source)
            if expiry_match:
                result["expiry"] = expiry_match.group(1)
                print(f"[浏览器] 到期时间: {result['expiry']}")
            
            # 查找并点击续期按钮
            print("[浏览器] 查找续期按钮...")
            
            btn_clicked = sb.execute_script("""
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var text = buttons[i].innerText || buttons[i].textContent;
                    if (text.includes('시간추가') || text.includes('시간 추가') || text.includes('시간')) {
                        buttons[i].scrollIntoView({block: 'center'});
                        buttons[i].click();
                        return true;
                    }
                }
                return false;
            """)
            
            if not btn_clicked:
                # 列出所有按钮用于调试
                all_buttons = sb.execute_script("""
                    var buttons = document.querySelectorAll('button');
                    var texts = [];
                    for (var i = 0; i < buttons.length; i++) {
                        var text = (buttons[i].innerText || buttons[i].textContent).trim();
                        if (text) texts.push(text.substring(0, 30));
                    }
                    return texts.join(' | ');
                """)
                print(f"[浏览器] 页面按钮: {all_buttons}")
                result["message"] = "未找到续期按钮"
                screenshot(sb, "debug_result")
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            print("[浏览器] ✓ 已点击续期按钮")
            time.sleep(2)
            screenshot(sb, "03-after-click")
            
            # 检查并处理 Turnstile 验证
            if check_turnstile_present(sb):
                print("[浏览器] 检测到 Turnstile 验证...")
                
                if not wait_for_turnstile(sb, 60):
                    print("[浏览器] ⚠ Turnstile 验证超时")
                    result["message"] = "Turnstile 验证超时"
                    screenshot(sb, "debug_result")
                    result["screenshot"] = SCREENSHOT_PATH
                    return result
                
                screenshot(sb, "04-turnstile-done")
            
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
            
            for i in range(30):
                time.sleep(1)
                
                try:
                    current_source = sb.get_page_source()
                    
                    # 检查是否还在验证中
                    if check_turnstile_present(sb):
                        if i % 5 == 0:
                            print(f"[浏览器] 仍在验证中... ({i+1}秒)")
                            try:
                                sb.uc_gui_click_captcha()
                            except:
                                pass
                        continue
                    
                    # 检查冷却期
                    for kw in cooldown_keywords:
                        if kw in current_source:
                            result["is_cooldown"] = True
                            result["message"] = "冷却期内"
                            print(f"[浏览器] 检测到冷却期")
                            break
                    
                    if result["is_cooldown"]:
                        break
                    
                    # 检查成功
                    for kw in success_keywords:
                        if kw in current_source:
                            result["success"] = True
                            result["message"] = "续期成功"
                            print(f"[浏览器] ✓ 检测到成功")
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
                            print(f"[浏览器] ✓ 到期时间已更新: {new_expiry}")
                            break
                    
                except Exception as e:
                    if i % 10 == 0:
                        print(f"[浏览器] 检测异常: {e}")
                
                if i % 10 == 9:
                    print(f"[浏览器] 等待中... ({i+1}秒)")
            
            # 保存最终截图
            time.sleep(2)
            screenshot(sb, "debug_result")
            result["screenshot"] = SCREENSHOT_PATH
            
            if result["success"]:
                print("[浏览器] ✓ 续期成功")
                # 刷新获取最新到期时间
                sb.refresh()
                time.sleep(3)
                new_source = sb.get_page_source()
                new_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', new_source)
                if new_match:
                    result["expiry"] = new_match.group(1)
                screenshot(sb, "debug_result")
            elif result["is_cooldown"]:
                print("[浏览器] ⏳ 冷却期内")
            else:
                result["message"] = "未检测到明确结果"
                print("[浏览器] ✗ 未检测到明确结果")
            
            # 获取新 Cookie
            try:
                for cookie in sb.get_cookies():
                    if cookie["name"].startswith("remember_web"):
                        new_cookie_str = cookie["name"] + "=" + cookie["value"]
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
    print("WeirdHost 自动续期 v33")
    print("=" * 50)
    print(f"服务器 ID: {server_id}")
    print(f"代理: {socks_proxy if socks_proxy else '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"DISPLAY: {os.environ.get('DISPLAY', '未设置')}")
    print("=" * 50)
    
    result = run_browser_renew(cookie, server_id, socks_proxy if socks_proxy else None)
    
    if result.get("new_cookie"):
        update_github_secret("WEIRDHOST_COOKIE", result["new_cookie"])
    
    remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
    screenshot_file = result.get("screenshot")
    
    if result["success"]:
        msg = f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}"
        notify_telegram(msg, screenshot_file)
        sys.exit(0)
    elif result["is_cooldown"]:
        print(f"[结果] 冷却期内，跳过通知")
        print(f"[信息] 到期: {result['expiry']}，剩余: {remaining}")
        sys.exit(0)
    elif result["cookie_expired"]:
        notify_telegram("❌ <b>WeirdHost Cookie 已失效</b>\n\n请手动更新", screenshot_file)
        sys.exit(1)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n{result['message']}", screenshot_file)
        sys.exit(1)


if __name__ == "__main__":
    main()
