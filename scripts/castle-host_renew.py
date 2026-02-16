#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Castle-Host 服务器自动续约脚本
功能：多账号支持 + 自动启动关机服务器 + Cookie自动更新
配置变量:
- CASTLE_COOKIES=PHPSESSID=xxx; uid=xxx,PHPSESSID=xxx; uid=xxx  (多账号用逗号分隔)
"""

import os
import sys
import re
import io
import logging
import asyncio
import aiohttp
from enum import Enum
from base64 import b64encode
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict
from playwright.async_api import async_playwright, BrowserContext, Page

LOG_FILE = "castle_renew.log"
REQUEST_TIMEOUT = 30
PAGE_TIMEOUT = 60000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
logger = logging.getLogger(__name__)


class RenewalStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


@dataclass
class ServerResult:
    server_id: str
    status: RenewalStatus
    message: str
    expiry: str = ""
    days: int = 0
    started: bool = False
    console_log: str = ""


@dataclass
class Config:
    cookies_list: List[str]
    tg_token: Optional[str]
    tg_chat_id: Optional[str]
    repo_token: Optional[str]
    repository: Optional[str]

    @classmethod
    def from_env(cls) -> "Config":
        raw = os.environ.get("CASTLE_COOKIES", "").strip()
        return cls(
            cookies_list=[c.strip() for c in raw.split(",") if c.strip()],
            tg_token=os.environ.get("TG_BOT_TOKEN"),
            tg_chat_id=os.environ.get("TG_CHAT_ID"),
            repo_token=os.environ.get("REPO_TOKEN"),
            repository=os.environ.get("GITHUB_REPOSITORY")
        )


def mask_id(sid: str) -> str:
    return f"{sid[0]}***{sid[-2:]}" if len(sid) > 3 else sid


def convert_date(s: str) -> str:
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s) if s else None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else "Unknown"


def days_left(s: str) -> int:
    try:
        return (datetime.strptime(s, "%d.%m.%Y") - datetime.now()).days
    except:
        return 0


def parse_cookies(s: str) -> List[Dict]:
    cookies = []
    for p in s.split(";"):
        p = p.strip()
        if "=" in p:
            n, v = p.split("=", 1)
            cookies.append({"name": n.strip(), "value": v.strip(), "domain": ".castle-host.com", "path": "/"})
    return cookies


def analyze_error(msg: str) -> Tuple[RenewalStatus, str]:
    m = msg.lower()
    if "24 час" in m or "уже продлен" in m:
        return RenewalStatus.RATE_LIMITED, "今日已续期"
    if "недостаточно" in m:
        return RenewalStatus.FAILED, "余额不足"
    return RenewalStatus.FAILED, msg


class Notifier:
    def __init__(self, token: Optional[str], chat_id: Optional[str]):
        self.token, self.chat_id = token, chat_id

    async def send(self, msg: str) -> Optional[int]:
        if not self.token or not self.chat_id:
            return None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": msg},
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                ) as r:
                    if r.status == 200:
                        logger.info("✅ 通知已发送")
                        data = await r.json()
                        return data.get('result', {}).get('message_id')
                    else:
                        text = await r.text()
                        logger.error(f"❌ 通知失败: {text}")
        except Exception as e:
            logger.error(f"❌ 通知异常: {e}")
        return None

    async def send_file(self, content: str, filename: str, caption: str = "", reply_to: int = None) -> bool:
        if not self.token or not self.chat_id:
            return False
        try:
            file_obj = io.BytesIO(content.encode('utf-8'))
            async with aiohttp.ClientSession() as s:
                data = aiohttp.FormData()
                data.add_field('chat_id', str(self.chat_id))
                data.add_field('document', file_obj, filename=filename, content_type='text/plain')
                if caption:
                    data.add_field('caption', caption)
                if reply_to:
                    data.add_field('reply_to_message_id', str(reply_to))

                async with s.post(
                    f"https://api.telegram.org/bot{self.token}/sendDocument",
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as r:
                    if r.status == 200:
                        logger.info("✅ 文件已发送")
                        return True
                    else:
                        text = await r.text()
                        logger.error(f"❌ 文件发送失败: {text}")
        except Exception as e:
            logger.error(f"❌ 文件发送异常: {e}")
        return False


class GitHubManager:
    def __init__(self, token: Optional[str], repo: Optional[str]):
        self.token, self.repo = token, repo
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"} if token else {}

    async def update_secret(self, name: str, value: str) -> bool:
        if not self.token or not self.repo:
            return False
        try:
            from nacl import encoding, public
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://api.github.com/repos/{self.repo}/actions/secrets/public-key",
                                 headers=self.headers) as r:
                    if r.status != 200:
                        return False
                    kd = await r.json()
                pk = public.PublicKey(kd["key"].encode(), encoding.Base64Encoder())
                enc = b64encode(public.SealedBox(pk).encrypt(value.encode())).decode()
                async with s.put(f"https://api.github.com/repos/{self.repo}/actions/secrets/{name}",
                                 headers=self.headers,
                                 json={"encrypted_value": enc, "key_id": kd["key_id"]}) as r:
                    if r.status in [201, 204]:
                        logger.info(f"✅ Secret {name} 已更新")
                        return True
        except Exception as e:
            logger.error(f"❌ GitHub异常: {e}")
        return False


class CastleClient:
    def __init__(self, ctx: BrowserContext, page: Page):
        self.ctx, self.page = ctx, page
        self.base = "https://cp.castle-host.com"

    async def get_server_ids(self) -> List[str]:
        try:
            await self.page.goto(f"{self.base}/servers", wait_until="networkidle")
            content = await self.page.content()
            match = re.search(r'var\s+ServersID\s*=\s*\[([\d,\s]+)\]', content)
            if match:
                ids = [x.strip() for x in match.group(1).split(",") if x.strip()]
                logger.info(f"📋 找到 {len(ids)} 个服务器: {[mask_id(x) for x in ids]}")
                return ids
        except Exception as e:
            logger.error(f"❌ 获取服务器ID失败: {e}")
        return []

    async def get_console_log(self, sid: str) -> str:
        """获取服务器控制台日志"""
        try:
            await self.page.goto(f"{self.base}/servers/console/index/{sid}", wait_until="networkidle")
            await self.page.wait_for_timeout(3000)

            console = self.page.locator("#console_data")
            if await console.count() > 0:
                log = await console.text_content() or ""
                logger.info(f"📜 获取到控制台日志 ({len(log)} 字符)")
                return log
        except Exception as e:
            logger.error(f"❌ 获取控制台日志失败: {e}")
        return ""

    async def start_if_stopped(self, sid: str) -> Tuple[bool, str]:
        """进入控制页启动服务器，返回(是否启动, 控制台日志)"""
        masked = mask_id(sid)
        try:
            # 进入服务器控制页面
            await self.page.goto(f"{self.base}/servers/control/index/{sid}", wait_until="networkidle")
            await self.page.wait_for_timeout(2000)

            # 多种选择器匹配启动按钮
            selectors = [
                # 控制页: <a onclick="sendActionStatus(id,'start')">
                f'a[onclick*="sendActionStatus({sid},\'start\')"]',
                f"a[onclick*=\"sendActionStatus({sid},'start')\"]",
                # 控制页备用: 按文字匹配 (Запустить = 启动)
                'a.btn-control:has-text("Запустить")',
                # 带 play 图标的按钮
                'a.btn-control:has(i.bi-play)',
                # 列表页: 可能用 sendAction
                f'button[onclick*="sendAction({sid},\'start\')"]',
                f'a[onclick*="sendAction({sid},\'start\')"]',
            ]

            for sel in selectors:
                try:
                    btn = self.page.locator(sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        logger.info(f"🔴 服务器 {masked} 已关机，正在启动... (选择器: {sel})")
                        await btn.click()
                        await self.page.wait_for_timeout(5000)

                        # 检查是否有弹窗确认
                        confirm_selectors = [
                            'button:has-text("OK")',
                            'button:has-text("Да")',
                            '.swal2-confirm',
                            'button.btn-success:has-text("OK")',
                        ]
                        for csig in confirm_selectors:
                            try:
                                confirm = self.page.locator(csig).first
                                if await confirm.count() > 0 and await confirm.is_visible():
                                    await confirm.click()
                                    logger.info(f"✅ 已确认弹窗")
                                    await self.page.wait_for_timeout(3000)
                                    break
                            except:
                                continue

                        logger.info(f"🟢 服务器 {masked} 启动指令已发送")

                        # 获取控制台日志
                        log = await self.get_console_log(sid)
                        return True, log
                except:
                    continue

            logger.info(f"✅ 服务器 {masked} 运行中 (无启动按钮)")
        except Exception as e:
            logger.error(f"❌ 启动服务器 {masked} 失败: {e}")
        return False, ""

    async def get_expiry(self, sid: str) -> str:
        try:
            await self.page.goto(f"{self.base}/servers/pay/index/{sid}", wait_until="networkidle")
            text = await self.page.text_content("body")
            match = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
            return match.group(1) if match else ""
        except:
            return ""

    async def renew(self, sid: str) -> Tuple[RenewalStatus, str]:
        masked = mask_id(sid)
        api_resp: Dict = {}

        async def capture(resp):
            if "/buy_months/" in resp.url:
                try:
                    api_resp["data"] = await resp.json()
                except:
                    pass

        self.page.on("response", capture)

        for sel in ["#freebtn", 'button:has-text("Продлить")', 'a:has-text("Продлить")',
                     'button:has-text("Бесплатно")', 'a:has-text("Бесплатно")']:
            try:
                btn = self.page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    logger.info(f"🖱️ 服务器 {masked} 已点击续约")

                    for _ in range(20):
                        if api_resp.get("data"):
                            break
                        await asyncio.sleep(0.5)

                    if api_resp.get("data"):
                        data = api_resp["data"]
                        if data.get("status") == "error":
                            return analyze_error(data.get("error", ""))
                        if data.get("status") in ["success", "ok"]:
                            return RenewalStatus.SUCCESS, "续约成功"

                    await self.page.wait_for_timeout(2000)
                    text = await self.page.text_content("body")
                    if "24 час" in text:
                        return RenewalStatus.RATE_LIMITED, "今日已续期"
                    return RenewalStatus.SUCCESS, "续约成功"
            except:
                continue
        return RenewalStatus.FAILED, "未找到续约按钮"

    async def extract_cookies(self) -> Optional[str]:
        try:
            cookies = await self.ctx.cookies()
            cc = [c for c in cookies if "castle-host.com" in c.get("domain", "")]
            return "; ".join([f"{c['name']}={c['value']}" for c in cc]) if cc else None
        except:
            return None


async def process_account(cookie_str: str, idx: int, config: Config, notifier: Notifier) -> Tuple[
    Optional[str], List[Tuple[str, int, str]]]:
    """返回(新Cookie, [(服务器ID, 消息ID, 控制台日志)])"""
    cookies = parse_cookies(cookie_str)
    if not cookies:
        logger.error(f"❌ 账号#{idx + 1} Cookie解析失败")
        return None, []

    logger.info(f"{'=' * 50}")
    logger.info(f"📌 处理账号 #{idx + 1}")

    started_servers: List[Tuple[str, int, str]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)
        client = CastleClient(ctx, page)
        results: List[ServerResult] = []

        try:
            server_ids = await client.get_server_ids()
            if not server_ids:
                if "login" in page.url:
                    logger.error(f"❌ 账号#{idx + 1} Cookie已失效")
                    await notifier.send(f"❌ 账号#{idx + 1} Cookie已失效")
                return None, []

            for sid in server_ids:
                logger.info(f"--- 处理服务器 {mask_id(sid)} ---")

                # 进入控制页启动服务器并获取日志
                started, console_log = await client.start_if_stopped(sid)

                expiry = await client.get_expiry(sid)
                d = days_left(expiry)
                logger.info(f"📅 到期: {convert_date(expiry)} ({d}天)")

                status, msg = await client.renew(sid)
                logger.info(f"📝 结果: {msg}")

                results.append(ServerResult(sid, status, msg, expiry, d, started, console_log))
                await asyncio.sleep(2)

            # 发送通知
            for r in results:
                if r.status == RenewalStatus.SUCCESS:
                    stat = "✅ 续约成功 (+1天)"
                elif r.status == RenewalStatus.RATE_LIMITED:
                    stat = "📝 今日已续期"
                else:
                    stat = f"❌ 续约失败: {r.message}"

                started_line = "🟢 服务器已启动\n" if r.started else ""
                msg = f"""🎁 Castle-Host 自动续约通知

👤 账号: #{idx + 1}
💻 服务器: {r.server_id}
📅 到期时间: {convert_date(r.expiry)}
⏳ 剩余天数: {r.days} 天
🔗 https://cp.castle-host.com/servers/pay/index/{r.server_id}

{started_line}{stat}"""
                message_id = await notifier.send(msg)

                # 启动的服务器记录消息ID和日志
                if r.started and message_id:
                    started_servers.append((r.server_id, message_id, r.console_log))

            new_cookie = await client.extract_cookies()
            if new_cookie and new_cookie != cookie_str:
                logger.info(f"🔄 账号#{idx + 1} Cookie已变化")
                return new_cookie, started_servers
            return cookie_str, started_servers

        except Exception as e:
            logger.error(f"❌ 账号#{idx + 1} 异常: {e}")
            await notifier.send(f"❌ 账号#{idx + 1} 异常: {e}")
            return None, []
        finally:
            await ctx.close()
            await browser.close()


async def main():
    logger.info("=" * 50)
    logger.info("Castle-Host 自动续约")
    logger.info("=" * 50)

    config = Config.from_env()
    if not config.cookies_list:
        logger.error("❌ 未设置 CASTLE_COOKIES")
        return

    logger.info(f"📊 共 {len(config.cookies_list)} 个账号")

    notifier = Notifier(config.tg_token, config.tg_chat_id)
    github = GitHubManager(config.repo_token, config.repository)

    new_cookies = []
    changed = False
    all_started: List[Tuple[str, int, str]] = []

    for i, cookie in enumerate(config.cookies_list):
        new, started = await process_account(cookie, i, config, notifier)
        all_started.extend(started)
        if new:
            new_cookies.append(new)
            if new != cookie:
                changed = True
        else:
            new_cookies.append(cookie)
        if i < len(config.cookies_list) - 1:
            await asyncio.sleep(5)

    # 发送控制台日志文件
    for sid, msg_id, console_log in all_started:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        content = f"Castle-Host 服务器启动日志\n"
        content += f"服务器ID: {sid}\n"
        content += f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        content += f"控制面板: https://cp.castle-host.com/servers/control/index/{sid}\n"
        content += "=" * 50 + "\n\n"
        content += "【控制台输出】\n"
        content += console_log if console_log else "(无日志)"

        await notifier.send_file(content, f"castle_{sid}_{ts}.txt", "📜 启动日志", reply_to=msg_id)

    if changed:
        await github.update_secret("CASTLE_COOKIES", ",".join(new_cookies))

    logger.info("👋 完成")


if __name__ == "__main__":
    asyncio.run(main())
