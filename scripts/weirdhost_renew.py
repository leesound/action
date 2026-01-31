#!/usr/bin/env python3
"""
WeirdHost 自动续期 v36
- 修复 Turnstile 检测：检测 "Verify you are human"
- 主动点击验证框
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

BASE_URL = "https://hub.weirdhost.xyz"
SCREENSHOT_PATH = "debug_result.png"


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
        data = json.dumps({"encrypted_value": encrypted_value, "key_id": key_data["key_id"]}).encode('utf-8')
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


def send_telegram_photo(photo_path: str, caption: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id or not os.path.exists(photo_path):
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        boundary = "----FormBoundary7MA4YWxk"
        with open(photo_path, "rb") as f:
            photo_data = f.read()
        body = b"\r\n".join([
            f"--{boundary}".encode(), b'Content-Disposition: form-data; name="chat_id"', b"", chat_id.encode(),
            f"--{boundary}".encode(), b'Content-Disposition: form-data; name="caption"', b"", caption.encode('utf-8'),
            f"--{boundary}".encode(), b'Content-Disposition: form-data; name="parse_mode"', b"", b"HTML",
            f"--{boundary}".encode(), b'Content-Disposition: form-data; name="photo"; filename="screenshot.png"',
            b"Content-Type: image/png", b"", photo_data, f"--{boundary}--".encode(), b""
        ])
        req = urllib.request.Request(url, data=body, method="POST")
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
        data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode('utf-8')
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


def get_turnstile_status(sb) -> dict:
    """获取 Turnstile 状态"""
    try:
        return sb.execute_script("""
            var result = {exists: false, needsClick: false, verifying: false, completed: false};
            
            // 检查 iframe
            var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (iframe) {
                result.exists = true;
            }
            
            // 检查 turnstile 容器
            var turnstile = document.querySelector('.cf-turnstile, [data-turnstile]');
            if (turnstile) {
                result.exists = true;
            }
            
            // 检查文本
            var bodyText = document.body.innerText || '';
            if (bodyText.includes('Verify you are human')) {
                result.exists = true;
                result.needsClick = true;
            }
            if (bodyText.includes('Verifying')) {
                result.exists = true;
                result.verifying = true;
            }
            
            // 检查是否已完成（有 response）
            var input = document.querySelector('input[name="cf-turnstile-response"]');
            if (input && input.value && input.value.length > 50) {
                result.completed = true;
                result.needsClick = false;
                result.verifying = false;
            }
            
            return result;
        """) or {"exists": False, "needsClick": False, "verifying": False, "completed": False}
    except:
        return {"exists": False, "needsClick": False, "verifying": False, "completed": False}


def click_turnstile(sb) -> bool:
    """点击 Turnstile 验证框"""
    try:
        # 方法1: 使用 uc_gui_click_captcha
        print("[Turnstile] 尝试 uc_gui_click_captcha...")
        sb.uc_gui_click_captcha()
        return True
    except Exception as e:
        print(f"[Turnstile] uc_gui_click_captcha 失败: {e}")
    
    try:
        # 方法2: 直接点击 iframe
        print("[Turnstile] 尝试点击 iframe...")
        sb.execute_script("""
            var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (iframe) {
                iframe.scrollIntoView({block: 'center'});
                iframe.click();
            }
        """)
        return True
    except Exception as e:
        print(f"[Turnstile] 点击 iframe 失败: {e}")
    
    try:
        # 方法3: 点击 turnstile 容器
        print("[Turnstile] 尝试点击容器...")
        sb.execute_script("""
            var t = document.querySelector('.cf-turnstile, [data-turnstile]');
            if (t) {
                t.scrollIntoView({block: 'center'});
                t.click();
            }
        """)
        return True
    except:
        pass
    
    return False


def wait_for_turnstile_complete(sb, timeout: int = 120) -> bool:
    """等待 Turnstile 验证完成"""
    print("[Turnstile] 开始处理验证...")
    start = time.time()
    click_count = 0
    last_click = 0
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        status = get_turnstile_status(sb)
        
        print(f"[Turnstile] {elapsed}秒 - exists:{status['exists']} needsClick:{status['needsClick']} verifying:{status['verifying']} completed:{status['completed']}")
        
        # 已完成
        if status['completed']:
            print(f"[Turnstile] ✓ 验证完成 ({elapsed}秒)")
            return True
        
        # 不存在 turnstile
        if not status['exists']:
            time.sleep(2)
            status = get_turnstile_status(sb)
            if not status['exists']:
                print(f"[Turnstile] ✓ 无需验证 ({elapsed}秒)")
                return True
        
        # 需要点击或正在验证，尝试点击
        if (status['needsClick'] or status['verifying']) and time.time() - last_click > 10 and click_count < 8:
            print(f"[Turnstile] 尝试点击 (第{click_count + 1}次)...")
            click_turnstile(sb)
            click_count += 1
            last_click = time.time()
            time.sleep(5)
        else:
            time.sleep(2)
    
    print(f"[Turnstile] ✗ 超时 ({timeout}秒)")
    return False


def wait_for_cloudflare(sb, timeout: int = 30) -> bool:
    print("[CF] 检查验证...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            src = sb.get_page_source()
            url = sb.get_current_url().lower()
            if "hub.weirdhost.xyz/server" in url or "유통기한" in src or "server controls" in src.lower():
                print("[CF] ✓ 通过")
                return True
            time.sleep(1)
        except:
            time.sleep(1)
    print("[CF] ⚠ 超时")
    return False


def get_expiry_from_page(sb) -> Optional[str]:
    try:
        src = sb.get_page_source()
        m = re.search(r'유통기한\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', src)
        if m:
            return m.group(1).strip()
    except:
        pass
    return None


def check_cooldown_message(sb) -> bool:
    try:
        src = sb.get_page_source()
        for kw in ["아직 서버를 갱신할 수 없습니다", "갱신할 수 없습니다", "기다려주세요"]:
            if kw in src:
                return True
    except:
        pass
    return False


def parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    cookie_str = urllib.parse.unquote(cookie_str.strip())
    cookies = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def run_browser_renew(cookie_str: str, server_id: str, socks_proxy: Optional[str] = None) -> Dict:
    from seleniumbase import SB
    
    result = {"success": False, "is_cooldown": False, "cookie_expired": False, "message": "", "expiry": "", "new_cookie": None, "screenshot": None}
    cookies = parse_cookie_string(cookie_str)
    server_url = f"{BASE_URL}/server/{server_id}"
    
    sb_kwargs = {"uc": True, "test": True, "locale": "en", "headless": False, "uc_cdp_events": True}
    if socks_proxy:
        proxy_addr = socks_proxy.replace("socks5://", "").replace("socks5h://", "")
        sb_kwargs["proxy"] = f"socks5://{proxy_addr}"
        print(f"[浏览器] 代理: {proxy_addr}")
    
    try:
        print("[浏览器] 启动 Chrome...")
        with SB(**sb_kwargs) as sb:
            sb.uc_open_with_reconnect(BASE_URL, reconnect_time=6)
            time.sleep(3)
            wait_for_cloudflare(sb, 30)
            screenshot(sb, "01-homepage")
            
            for name, value in cookies.items():
                try:
                    sb.add_cookie({"name": name, "value": value, "domain": "hub.weirdhost.xyz"})
                except:
                    pass
            
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
            
            time.sleep(5)
            original_expiry = get_expiry_from_page(sb)
            if original_expiry:
                result["expiry"] = original_expiry
                print(f"[浏览器] 当前到期: {original_expiry}")
            
            # 点击续期按钮
            btn_result = sb.execute_script("""
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    var t = (btns[i].innerText || '').trim();
                    if (t === '시간추가' || t.includes('시간추가')) {
                        btns[i].scrollIntoView({block: 'center'});
                        btns[i].click();
                        return 'clicked';
                    }
                }
                return 'not_found';
            """)
            
            if btn_result != 'clicked':
                result["message"] = "未找到续期按钮"
                screenshot(sb, "debug_result")
                result["screenshot"] = SCREENSHOT_PATH
                return result
            
            print("[浏览器] ✓ 已点击续期按钮")
            time.sleep(3)
            screenshot(sb, "03-after-click")
            
            # 检查并处理 Turnstile
            status = get_turnstile_status(sb)
            if status['exists'] or status['needsClick'] or status['verifying']:
                print("[浏览器] 检测到 Turnstile...")
                if not wait_for_turnstile_complete(sb, 120):
                    result["message"] = "Turnstile 超时"
                    screenshot(sb, "debug_result")
                    result["screenshot"] = SCREENSHOT_PATH
                    return result
                screenshot(sb, "04-turnstile-done")
                time.sleep(3)
            
            # 等待结果
            print("[浏览器] 等待结果...")
            for i in range(60):
                time.sleep(1)
                
                # 检查 Turnstile 状态
                status = get_turnstile_status(sb)
                if status['exists'] and not status['completed']:
                    if i % 10 == 0:
                        print(f"[浏览器] {i}秒 - Turnstile 未完成，尝试点击...")
                        click_turnstile(sb)
                    continue
                
                if check_cooldown_message(sb):
                    result["is_cooldown"] = True
                    result["message"] = "冷却期内"
                    print("[浏览器] ⏳ 冷却期")
                    break
                
                new_expiry = get_expiry_from_page(sb)
                if new_expiry and original_expiry and new_expiry != original_expiry:
                    result["success"] = True
                    result["expiry"] = new_expiry
                    result["message"] = "续期成功"
                    print(f"[浏览器] ✓ 日期更新: {original_expiry} -> {new_expiry}")
                    break
                
                if i % 15 == 14:
                    print(f"[浏览器] {i+1}秒 - 等待中...")
                    screenshot(sb, f"05-waiting-{i+1}s")
            
            time.sleep(2)
            screenshot(sb, "debug_result")
            result["screenshot"] = SCREENSHOT_PATH
            
            if not result["success"] and not result["is_cooldown"]:
                final = get_expiry_from_page(sb)
                if final and original_expiry and final != original_expiry:
                    result["success"] = True
                    result["expiry"] = final
                    result["message"] = "续期成功"
                else:
                    result["message"] = "日期未变化"
            
            try:
                for c in sb.get_cookies():
                    if c["name"].startswith("remember_web"):
                        new_c = c["name"] + "=" + c["value"]
                        if new_c != cookie_str:
                            result["new_cookie"] = new_c
                        break
            except:
                pass
    except Exception as e:
        result["message"] = str(e)
        print(f"[浏览器] ✗ 异常: {e}")
        import traceback
        traceback.print_exc()
    
    return result


def main():
    cookie = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_id = os.environ.get("WEIRDHOST_ID", "").strip()
    socks_proxy = os.environ.get("SOCKS_PROXY", "").strip()
    
    if not cookie or not server_id:
        print("❌ 请设置 WEIRDHOST_COOKIE 和 WEIRDHOST_ID")
        sys.exit(1)
    
    print("=" * 50)
    print("WeirdHost 自动续期 v36")
    print("=" * 50)
    print(f"服务器 ID: {server_id}")
    print(f"代理: {socks_proxy or '无'}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"DISPLAY: {os.environ.get('DISPLAY', '未设置')}")
    print("=" * 50)
    
    result = run_browser_renew(cookie, server_id, socks_proxy or None)
    
    if result.get("new_cookie"):
        update_github_secret("WEIRDHOST_COOKIE", result["new_cookie"])
    
    remaining = calculate_remaining_time(result["expiry"]) if result["expiry"] else ""
    screenshot_file = result.get("screenshot")
    
    if result["success"]:
        notify_telegram(f"✅ <b>WeirdHost 续期成功</b>\n\n📅 到期: {result['expiry']}\n⏳ 剩余: {remaining}", screenshot_file)
        sys.exit(0)
    elif result["is_cooldown"]:
        print(f"[结果] 冷却期内，到期: {result['expiry']}，剩余: {remaining}")
        sys.exit(0)
    elif result["cookie_expired"]:
        notify_telegram("❌ <b>WeirdHost Cookie 已失效</b>", screenshot_file)
        sys.exit(1)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n{result['message']}", screenshot_file)
        sys.exit(1)


if __name__ == "__main__":
    main()
