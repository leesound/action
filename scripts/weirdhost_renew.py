#!/usr/bin/env python3
"""
WeirdHost 自动续期
"""

import os
import sys
import time
import base64
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

# ==================== 配置 ====================

BASE_URL = "https://hub.weirdhost.xyz"
COOKIE_DOMAIN = "hub.weirdhost.xyz"

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


def screenshot(sb, name: str) -> str:
    path = f"./{name}.png"
    try:
        sb.save_screenshot(path)
        print(f"[截图] {path}")
    except Exception as e:
        print(f"[截图] 失败: {e}")
    return path


# ==================== Telegram 通知 ====================

def notify_telegram(message: str, photo_path: str = ""):
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    
    try:
        if photo_path and Path(photo_path).exists():
            send_telegram_photo(token, chat_id, photo_path, message)
        else:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }).encode()
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30)
        print("[TG] ✓ 通知已发送")
    except Exception as e:
        print(f"[TG] ✗ 发送失败: {e}")


def send_telegram_photo(token: str, chat_id: str, photo_path: str, caption: str):
    try:
        boundary = "----WebKitFormBoundary"
        with open(photo_path, "rb") as f:
            photo_data = f.read()
        
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="screenshot.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
        
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=body
        )
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        urllib.request.urlopen(req, timeout=60)
    except Exception as e:
        print(f"[TG] 图片发送失败: {e}")


# ==================== GitHub Secret 更新 ====================

def update_github_secret(secret_name: str, secret_value: str) -> bool:
    try:
        from nacl import encoding, public
    except ImportError:
        print("[GitHub] nacl 库不可用")
        return False
    
    repo_token = os.environ.get("REPO_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    
    if not repo_token or not repository:
        print("[GitHub] 缺少 REPO_TOKEN 或 GITHUB_REPOSITORY")
        return False
    
    try:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {repo_token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Python"
        }
        
        # 获取公钥
        pk_url = f"https://api.github.com/repos/{repository}/actions/secrets/public-key"
        req = urllib.request.Request(pk_url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            import json
            pk_data = json.loads(resp.read().decode())
        
        # 加密
        pk = public.PublicKey(pk_data["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(pk)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        encrypted_value = base64.b64encode(encrypted).decode("utf-8")
        
        # 更新
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


# ==================== 页面操作 ====================

def wait_for_cloudflare(sb, timeout: int = 60) -> bool:
    """等待 Cloudflare 验证通过"""
    print("[CF] 检测验证状态...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            page_source = sb.get_page_source().lower()
            current_url = sb.get_current_url().lower()
            
            # 检查是否已通过验证
            if any([
                "유통기한" in sb.get_page_source(),  # 韩文到期时间
                "시간추가" in sb.get_page_source(),  # 韩文续期按钮
                "/server/" in current_url,
                "dashboard" in current_url,
            ]):
                print(f"[CF] ✓ 验证通过 ({int(time.time() - start_time)}s)")
                return True
            
            # 检查是否有 CF 验证页面
            if "just a moment" in page_source or "checking" in page_source:
                if int(time.time() - start_time) % 10 == 0:
                    print(f"[CF] 等待中... ({int(time.time() - start_time)}s)")
            
            time.sleep(1)
        except:
            time.sleep(1)
    
    print(f"[CF] ⚠ 验证超时 ({timeout}s)")
    return False


def wait_for_page_load(sb, timeout: int = 60) -> bool:
    """等待页面内容加载"""
    print("[页面] 等待加载...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            page_source = sb.get_page_source()
            if "유통기한" in page_source and "시간추가" in page_source:
                print(f"[页面] ✓ 加载完成 ({int(time.time() - start_time)}s)")
                return True
            
            if int(time.time() - start_time) % 10 == 0 and int(time.time() - start_time) > 0:
                print(f"[页面] 加载中... ({int(time.time() - start_time)}s)")
            
            time.sleep(1)
        except:
            time.sleep(1)
    
    print(f"[页面] ⚠ 加载超时 ({timeout}s)")
    return False


def get_expiry_time(sb) -> str:
    """获取到期时间"""
    try:
        return sb.execute_script("""
            const text = document.body.innerText;
            const match = text.match(/유통기한\\s*([\\d]{4}-[\\d]{2}-[\\d]{2}(?:\\s+[\\d]{2}:[\\d]{2}:[\\d]{2})?)/);
            return match ? match[1].trim() : '';
        """) or ""
    except:
        return ""


def check_turnstile_completed(sb) -> bool:
    """检查 Turnstile 是否完成"""
    try:
        result = sb.execute_script("""
            const input = document.querySelector('input[name="cf-turnstile-response"]');
            return input && input.value && input.value.length > 20;
        """)
        return bool(result)
    except:
        return False


def wait_for_turnstile(sb, timeout: int = 90) -> bool:
    """等待 Turnstile 验证完成"""
    print("[Turnstile] 等待验证...")
    
    start_time = time.time()
    clicked = False
    
    while time.time() - start_time < timeout:
        # 检查是否已完成
        if check_turnstile_completed(sb):
            print(f"[Turnstile] ✓ 验证完成 ({int(time.time() - start_time)}s)")
            return True
        
        # 尝试点击验证框
        if not clicked:
            try:
                # 使用 SeleniumBase 的 UC 模式点击验证码
                sb.uc_gui_click_captcha()
                clicked = True
                print("[Turnstile] 已尝试点击验证框")
                time.sleep(3)
            except Exception as e:
                print(f"[Turnstile] 点击失败: {e}")
                clicked = True  # 避免重复尝试
        
        elapsed = int(time.time() - start_time)
        if elapsed % 15 == 0 and elapsed > 0:
            print(f"[Turnstile] 等待中... ({elapsed}s)")
            # 再次尝试点击
            try:
                sb.uc_gui_click_captcha()
            except:
                pass
        
        time.sleep(1)
    
    # 最后检查一次
    if check_turnstile_completed(sb):
        print("[Turnstile] ✓ 验证完成")
        return True
    
    print(f"[Turnstile] ⚠ 验证超时 ({timeout}s)")
    return False


def click_renew_button(sb) -> bool:
    """点击续期按钮"""
    print("[续期] 查找续期按钮...")
    
    # 滚动页面查找按钮
    for scroll in range(5):
        sb.execute_script(f"window.scrollBy(0, {400 * (scroll + 1)})")
        time.sleep(0.5)
        
        clicked = sb.execute_script("""
            const buttons = document.querySelectorAll('button, a, [role="button"]');
            for (const btn of buttons) {
                const text = btn.textContent || btn.innerText || '';
                if (text.includes('시간추가') || text.includes('시간연장')) {
                    btn.scrollIntoView({ behavior: 'instant', block: 'center' });
                    btn.click();
                    return true;
                }
            }
            return false;
        """)
        
        if clicked:
            print("[续期] ✓ 已点击续期按钮")
            return True
    
    print("[续期] ✗ 未找到续期按钮")
    return False


def click_confirm_button(sb):
    """点击确认按钮"""
    try:
        sb.execute_script("""
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const text = (btn.textContent || '').toLowerCase();
                if (text.includes('확인') || text.includes('confirm') || text.includes('ok')) {
                    btn.click();
                    return;
                }
            }
        """)
    except:
        pass


def check_renew_result(sb, original_expiry: str) -> dict:
    """检查续订结果"""
    try:
        time.sleep(2)
        
        # 检查页面是否有错误信息
        page_text = sb.get_page_source()
        
        # 检查是否有冷却期错误
        cooldown_keywords = ["can only once", "can't renew", "cannot renew", "already", "아직"]
        for kw in cooldown_keywords:
            if kw.lower() in page_text.lower():
                return {"success": False, "is_cooldown": True, "message": "冷却期内"}
        
        # 获取新的到期时间
        new_expiry = get_expiry_time(sb)
        
        if new_expiry and new_expiry != original_expiry:
            return {"success": True, "expiry": new_expiry, "message": "续期成功"}
        
        return {"success": False, "is_cooldown": False, "message": "未检测到变化"}
    except Exception as e:
        return {"success": False, "is_cooldown": False, "message": str(e)}


# ==================== 主函数 ====================

def main():
    cookie_str = os.environ.get("WEIRDHOST_COOKIE", "").strip()
    server_url = os.environ.get("WEIRDHOST_SERVER_URL", "").strip()
    
    if not cookie_str:
        print("❌ 请设置 WEIRDHOST_COOKIE")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\nWEIRDHOST_COOKIE 未设置")
        sys.exit(1)
    
    if not server_url:
        print("❌ 请设置 WEIRDHOST_SERVER_URL")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\nWEIRDHOST_SERVER_URL 未设置")
        sys.exit(1)
    
    cookie_name, cookie_value = parse_cookie(cookie_str)
    
    print("=" * 60)
    print("WeirdHost 自动续期 v11 (SeleniumBase)")
    print("=" * 60)
    print(f"Cookie: {mask_string(cookie_name)}={mask_string(cookie_value, 8)}")
    print(f"Server: [已隐藏]")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    result = {
        "success": False,
        "message": "",
        "expiry_before": "",
        "expiry_after": "",
        "is_cooldown": False
    }
    
    try:
        from seleniumbase import SB
        
        sb_kwargs = {
            "uc": True,
            "test": True,
            "locale": "en",
            "headless": True,  # GitHub Actions 需要 headless
            "uc_cdp_events": True,
        }
        
        with SB(**sb_kwargs) as sb:
            print("\n[浏览器] 已启动")
            
            # 设置 Cookie
            print("[Cookie] 设置中...")
            sb.open(BASE_URL)
            time.sleep(2)
            
            sb.add_cookie({
                "name": cookie_name,
                "value": cookie_value,
                "domain": COOKIE_DOMAIN,
                "path": "/"
            })
            
            # 访问服务器页面
            print(f"\n[续期] 访问服务器页面...")
            sb.uc_open_with_reconnect(server_url, reconnect_time=6)
            time.sleep(3)
            
            # 等待 Cloudflare
            if not wait_for_cloudflare(sb, 90):
                screenshot(sb, "cf_failed")
                result["message"] = "Cloudflare 验证失败"
                raise Exception(result["message"])
            
            # 等待页面加载
            if not wait_for_page_load(sb, 60):
                screenshot(sb, "load_failed")
                result["message"] = "页面加载失败"
                raise Exception(result["message"])
            
            # 检查登录状态
            if "/auth/login" in sb.get_current_url() or "/login" in sb.get_current_url():
                screenshot(sb, "login_required")
                result["message"] = "Cookie 已失效"
                raise Exception(result["message"])
            
            # 获取当前到期时间
            result["expiry_before"] = get_expiry_time(sb)
            if result["expiry_before"]:
                remaining = calculate_remaining_time(result["expiry_before"])
                print(f"[续期] 当前到期: {result['expiry_before']} ({remaining})")
            
            screenshot(sb, "server_page")
            
            # 点击续期按钮
            if not click_renew_button(sb):
                screenshot(sb, "no_button")
                result["message"] = "未找到续期按钮"
                raise Exception(result["message"])
            
            time.sleep(2)
            screenshot(sb, "renew_clicked")
            
            # 处理 Turnstile 验证
            if not wait_for_turnstile(sb, 90):
                screenshot(sb, "turnstile_failed")
                result["message"] = "Turnstile 验证失败"
                raise Exception(result["message"])
            
            screenshot(sb, "turnstile_done")
            
            # 点击确认
            click_confirm_button(sb)
            time.sleep(3)
            
            screenshot(sb, "after_confirm")
            
            # 检查结果
            print("[续期] 检查结果...")
            check = check_renew_result(sb, result["expiry_before"])
            
            if check["success"]:
                result["success"] = True
                result["expiry_after"] = check.get("expiry", "")
                result["message"] = "续期成功"
                
                if result["expiry_after"]:
                    new_remaining = calculate_remaining_time(result["expiry_after"])
                    print(f"[续期] ✓ 成功！新到期: {result['expiry_after']} ({new_remaining})")
                else:
                    print("[续期] ✓ 成功！")
                    
            elif check.get("is_cooldown"):
                result["is_cooldown"] = True
                result["message"] = "冷却期内"
                print(f"[续期] ⏳ {result['message']}")
            else:
                result["message"] = check.get("message", "未知错误")
                print(f"[续期] ✗ {result['message']}")
            
            screenshot(sb, "final_result")
            
            # 检查 Cookie 更新
            print("\n[Cookie] 检查更新...")
            try:
                cookies = sb.get_cookies()
                for cookie in cookies:
                    if cookie.get("name", "").startswith("remember_web"):
                        new_cookie = f"{cookie['name']}={cookie['value']}"
                        if new_cookie != cookie_str:
                            print("[Cookie] 检测到新 Cookie")
                            update_github_secret("WEIRDHOST_COOKIE", new_cookie)
                        else:
                            print("[Cookie] Cookie 未变化")
                        break
            except Exception as e:
                print(f"[Cookie] 检查失败: {e}")
    
    except ImportError:
        print("[ERROR] 请安装: pip install seleniumbase")
        notify_telegram("❌ <b>WeirdHost 续期失败</b>\n\n缺少 seleniumbase 库")
        sys.exit(1)
    except Exception as e:
        print(f"\n[异常] {e}")
        import traceback
        traceback.print_exc()
        if not result["message"]:
            result["message"] = str(e)
    
    # 发送通知
    if result["success"]:
        expiry_info = ""
        if result["expiry_after"]:
            remaining = calculate_remaining_time(result["expiry_after"])
            expiry_info = f"\n📅 新到期: {result['expiry_after']}\n⏳ 剩余: {remaining}"
        notify_telegram(f"✅ <b>WeirdHost 续期成功</b>{expiry_info}")
        sys.exit(0)
    elif result["is_cooldown"]:
        remaining = calculate_remaining_time(result["expiry_before"]) if result["expiry_before"] else "未知"
        notify_telegram(f"ℹ️ <b>WeirdHost 冷却期</b>\n\n📅 到期: {result['expiry_before']}\n⏳ 剩余: {remaining}")
        sys.exit(0)
    else:
        notify_telegram(f"❌ <b>WeirdHost 续期失败</b>\n\n❗ {result['message']}", "final_result.png")
        sys.exit(1)


if __name__ == "__main__":
    main()
