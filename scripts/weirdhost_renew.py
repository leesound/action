#!/usr/bin/env python3
"""
WeirdHost 自动续期 v34
- 修复 Turnstile 检测逻辑
- 避免误点击广告链接
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

def check_turnstile_status(sb) -> dict:
    """检查 Turnstile 状态，返回详细信息"""
    try:
        result = sb.execute_script("""
            var status = {
                exists: false,
                verifying: false,
                completed: false,
                hasResponse: false
            };
            
            // 检查 turnstile 容器
            var turnstile = document.querySelector('.cf-turnstile, [data-turnstile]');
            if (turnstile) {
                status.exists = true;
            }
            
            // 检查 iframe
            var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (iframe) {
                status.exists = true;
                status.verifying = true;
            }
            
            // 检查 "Verifying..." 文本
            if (document.body.innerText.includes('Verifying')) {
                status.verifying = true;
            }
            
            // 检查 response input
            var inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
            for (var i = 0; i < inputs.length; i++) {
                if (inputs[i].value && inputs[i].value.length > 50) {
                    status.hasResponse = true;
                    status.completed = true;
                    status.verifying = false;
                }
            }
            
            // 检查成功标记（绿色勾）
            var successMark = document.querySelector('.cf-turnstile [data-state="success"]');
            if (successMark) {
                status.completed = true;
                status.verifying = false;
            }
            
            return status;
        """)
        return result if result else {"exists": False, "verifying": False, "completed": False, "hasResponse": False}
    except Exception as e:
        print(f"[Turnstile] 检查异常: {e}")
        return {"exists": False, "verifying": False, "completed": False, "hasResponse": False}


def wait_for_turnstile(sb, timeout: int = 90) -> bool:
    """等待 Turnstile 验证完成"""
    print("[Turnstile] 等待验证...")
    
    start_time = time.time()
    click_attempts = 0
    max_clicks = 3
    
    while time.time() - start_time < timeout:
        status = check_turnstile_status(sb)
        elapsed = int(time.time() - start_time)
        
        print(f"[Turnstile] {elapsed}s - exists:{status['exists']} verifying:{status['verifying']} completed:{status['completed']} hasResponse:{status['hasResponse']}")
        
        # 已完成
        if status['completed'] or status['hasResponse']:
            print("[Turnstile] ✓ 验证完成")
            return True
        
        # 不存在 turnstile
        if not status['exists'] and not status['verifying']:
            # 再等几秒确认
            time.sleep(2)
            status = check_turnstile_status(sb)
            if not status['exists'] and not status['verifying']:
                print("[Turnstile] ✓ 无需验证")
                return True
        
        # 正在验证中，尝试点击
        if status['verifying'] and click_attempts < max_clicks:
            try:
                print(f"[Turnstile] 尝试点击验证框 (第{click_attempts + 1}次)...")
                # 使用 JavaScript 点击 turnstile iframe
                clicked = sb.execute_script("""
                    var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                    if (iframe) {
                        var rect = iframe.getBoundingClientRect();
                        var x = rect.left + rect.width / 2;
                        var y = rect.top + rect.height / 2;
                        
                        // 创建并触发点击事件
                        var clickEvent = new MouseEvent('click', {
                            view: window,
                            bubbles: true,
                            cancelable: true,
                            clientX: x,
                            clientY: y
                        });
                        iframe.dispatchEvent(clickEvent);
                        return true;
                    }
                    return false;
                """)
                
                if not clicked:
                    # 尝试 uc_gui_click_captcha
                    try:
                        sb.uc_gui_click_captcha()
                    except:
                        pass
                
                click_attempts += 1
                time.sleep(5)
            except Exception as e:
                print(f"[Turnstile] 点击失败: {e}")
                click_attempts += 1
        
        time.sleep(2)
    
    # 超时后最后检查一次
    status = check_turnstile_status(sb)
    if status['completed'] or status['hasResponse']:
        print("[Turnstile] ✓ 验证完成（超时前）")
        return True
    
    print("[Turnstile] ✗ 验证超时")
    return False


def wait_for_cloudflare(sb, timeout: int = 30) -> bool:
    """等待 Cloudflare 初始验证通过"""
    print("[CF] 检查 Cloudflare 验证...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            page_source = sb.get_page_source()
            current_url = sb.get_current_url().lower()
            
            if any([
                "hub.weirdhost.xyz/server" in current_url,
                "hub.weirdhost.xyz/dashboard" in current_url,
                "유통기한" in page_source,
                "server controls" in page_source.lower(),
                "discord's bot server" in page_source.lower(),
            ]):
                print("[CF] ✓ 验证通过")
                return True
            
            if "just a moment" in page_source.lower() or "checking your browser" in page_source.lower():
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
    
    sb_kwargs = {
        "uc": True,
        "test": True,
        "locale": "en",
        "headless": False,
        "uc_cdp_events": True,
    }
    
    if socks_proxy:
        proxy_addr = socks_proxy.replace("socks5://", "").replace("socks5h://", "")
        sb_kwargs["proxy"] = f"socks5://{proxy_addr}"
        print(f"[浏览器] 使用代理: {proxy_addr}")
    
    try:
        print("[浏览器] 启动 Chrome (UC Mode)...")
        
        with SB(**sb_kwargs) as sb:
            print(f"[浏览器] 访问 {BASE_URL}")
            sb.uc_open_with_reconnect(BASE_URL, reconnect_time=6)
            time.sleep(3)
            
            wait_for_cloudflare(sb, 30)
            screenshot(sb, "01-homepage")
            
            for name, value in cookies.items():
                try:
                    sb.add_cookie({"name": name, "value": value, "domain": "hub.weirdhost.xyz"})
                except:
                    pass
            
            print(f"[浏览器] 访问 {server_url}")
            sb.uc_open_with_reconnect(server_url, reconnect_time=6)
            time.sleep(3)
            
            wait_for_cloudflare(sb, 30)
            screenshot(sb, "02-server-page")
            
            if "/login" in sb.get_current_url():
                result["cookie_expired"] = True
                result["message"] = "Cookie 已失效"
                screenshot(sb, "debug_result")
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            print("[浏览器] 等待页面加载...")
            time.sleep(5)
            
            page_source = sb.get_page_source()
            expiry_match = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', page_source)
            if expiry_match:
                result["expiry"] = expiry_match.group(1)
                print(f"[浏览器] 到期时间: {result['expiry']}")
            
            # 查找并点击续期按钮（시간추가）
            print("[浏览器] 查找续期按钮...")
            
            btn_clicked = sb.execute_script("""
                // 查找包含 "시간추가" 的按钮
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var text = (buttons[i].innerText || buttons[i].textContent || '').trim();
                    // 精确匹配 "시간추가" 按钮
                    if (text === '시간추가' || text.includes('시간추가')) {
                        console.log('Found button: ' + text);
                        buttons[i].scrollIntoView({block: 'center'});
                        buttons[i].click();
                        return 'clicked: ' + text;
                    }
                }
                
                // 列出所有按钮用于调试
                var allBtns = [];
                for (var i = 0; i < buttons.length; i++) {
                    var t = (buttons[i].innerText || buttons[i].textContent || '').trim();
                    if (t) allBtns.push(t.substring(0, 20));
                }
                return 'not found, buttons: ' + allBtns.join(' | ');
            """)
            
            print(f"[浏览器] 按钮点击结果: {btn_clicked}")
            
            if not btn_clicked or 'not found' in str(btn_clicked):
                result["message"] = f"未找到续期按钮: {btn_clicked}"
                screenshot(sb, "debug_result")
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            print("[浏览器] ✓ 已点击续期按钮")
            time.sleep(3)
            screenshot(sb, "03-after-click")
            
            # 检查是否还在正确页面
            current_url = sb.get_current_url()
            if "hub.weirdhost.xyz" not in current_url:
                print(f"[浏览器] ✗ 页面跳转到了错误位置: {current_url}")
                result["message"] = f"页面跳转错误: {current_url}"
                screenshot(sb, "debug_result")
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            # 处理 Turnstile 验证
            status = check_turnstile_status(sb)
            if status['exists'] or status['verifying']:
                print("[浏览器] 检测到 Turnstile 验证...")
                
                if not wait_for_turnstile(sb, 90):
                    print("[浏览器] ⚠ Turnstile 验证超时")
                    result["message"] = "Turnstile 验证超时"
                    screenshot(sb, "debug_result")
                    result["screenshot"] = SCREENSHOT_PATH
                    return result
                
                screenshot(sb, "04-turnstile-done")
                time.sleep(2)
            
            # 再次检查是否还在正确页面
            current_url = sb.get_current_url()
            if "hub.weirdhost.xyz" not in current_url:
                print(f"[浏览器] ✗ 验证后页面跳转错误: {current_url}")
                result["message"] = f"验证后页面跳转错误: {current_url}"
                screenshot(sb, "debug_result")
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            # 等待操作结果
            print("[浏览器] 等待操作结果...")
            
            cooldown_keywords = [
                "아직 서버를 갱신할 수 없습니다",
                "남은 시간이 더 줄어들 때까지",
                "갱신할 수 없습니다",
                "기다려주세요",
                "can't renew",
                "cannot renew",
            ]
            
            success_keywords = [
                "갱신되었습니다",
                "연장되었습니다",
                "추가되었습니다",
                "시간이 추가",
                "renewed",
                "extended",
            ]
            
            for i in range(45):
                time.sleep(1)
                
                # 检查 URL
                current_url = sb.get_current_url()
                if "hub.weirdhost.xyz" not in current_url:
                    print(f"[浏览器] ✗ 页面跳转: {current_url}")
                    # 尝试返回
                    sb.uc_open_with_reconnect(server_url, reconnect_time=4)
                    time.sleep(3)
                    break
                
                try:
                    current_source = sb.get_page_source()
                    
                    # 检查 Turnstile 是否还在验证
                    status = check_turnstile_status(sb)
                    if status['verifying'] and not status['completed']:
                        if i % 10 == 0:
                            print(f"[浏览器] 仍在验证中... ({i+1}秒)")
                        continue
                    
                    # 检查冷却期
                    for kw in cooldown_keywords:
                        if kw in current_source.lower() or kw in current_source:
                            result["is_cooldown"] = True
                            result["message"] = "冷却期内"
                            print("[浏览器] 检测到冷却期")
                            break
                    
                    if result["is_cooldown"]:
                        break
                    
                    # 检查成功
                    for kw in success_keywords:
                        if kw in current_source.lower() or kw in current_source:
                            result["success"] = True
                            result["message"] = "续期成功"
                            print("[浏览器] ✓ 检测到成功")
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
                
                if i % 15 == 14:
                    print(f"[浏览器] 等待中... ({i+1}秒)")
                    screenshot(sb, f"05-waiting-{i+1}s")
            
            # 保存最终截图
            time.sleep(2)
            screenshot(sb, "debug_result")
            result["screenshot"] = SCREENSHOT_PATH
            
            if result["success"]:
                print("[浏览器] ✓ 续期成功")
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
                result["message"] = result["message"] or "未检测到明确结果"
                print(f"[浏览器] ✗ {result['message']}")
            
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
    print("WeirdHost 自动续期 v34")
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
