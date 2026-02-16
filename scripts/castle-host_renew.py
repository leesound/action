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
                        return (await r.json()).get('result', {}).get('message_id')
                    logger.error(f"❌ 通知失败: {await r.text()}")
        except Exception as e:
            logger.error(f"❌ 通知异常: {e}")
        return None


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
                async with s.get(
                    f"https://api.github.com/repos/{self.repo}/actions/secrets/public-key",
                    headers=self.headers
                ) as r:
                    if r.status != 200:
                        return False
                    kd = await r.json()
                pk = public.PublicKey(kd["key"].encode(), encoding.Base64Encoder())
                enc = b64encode(public.SealedBox(pk).encrypt(value.encode())).decode()
                async with s.put(
                    f"https://api.github.com/repos/{self.repo}/actions/secrets/{name}",
                    headers=self.headers,
                    json={"encrypted_value": enc, "key_id": kd["key_id"]}
                ) as r:
                    if r.status in [201, 204]:
                        logger.info(f"✅ Secret {name} 已更新")
                        return True
        except Exception as e:
            logger.error(f"❌ GitHub异常: {e}")
        return False


class CastleClient:
    BASE = "https://cp.castle-host.com"

    def __init__(self, ctx: BrowserContext, page: Page):
        self.ctx, self.page = ctx, page

    async def get_server_ids(self) -> List[str]:
        try:
            await self.page.goto(f"{self.BASE}/servers", wait_until="networkidle")
            match = re.search(r'var\s+ServersID\s*=\s*\[([\d,\s]+)\]', await self.page.content())
            if match:
                ids = [x.strip() for x in match.group(1).split(",") if x.strip()]
                logger.info(f"📋 找到 {len(ids)} 个服务器: {[mask_id(x) for x in ids]}")
                return ids
        except Exception as e:
            logger.error(f"❌ 获取服务器ID失败: {e}")
        return []

    async def start_if_stopped(self, sid: str) -> bool:
        """进入控制页，如果服务器关机则启动"""
        masked = mask_id(sid)
        try:
            await self.page.goto(f"{self.BASE}/servers/control/index/{sid}", wait_until="networkidle")
            await self.page.wait_for_timeout(2000)

            for sel in [
                f"a[onclick*=\"sendActionStatus({sid},'start')\"]",
                'a.btn-control:has-text("Запустить")',
                'a.btn-control:has(i.bi-play)',
            ]:
                btn = self.page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    logger.info(f"🔴 服务器 {masked} 已关机，正在启动...")
                    await btn.click()
                    await self.page.wait_for_timeout(5000)
                    logger.info(f"🟢 服务器 {masked} 启动指令已发送")
                    return True

            logger.info(f"✅ 服务器 {masked} 运行中")
        except Exception as e:
            logger.error(f"❌ 启动服务器 {masked} 失败: {e}")
        return False

    async def get_expiry(self, sid: str) -> str:
        try:
            await self.page.goto(f"{self.BASE}/servers/pay/index/{sid}", wait_until="networkidle")
            match = re.search(r"(\d{2}\.\d{2}\.\d{4})", await self.page.text_content("body"))
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
                    if "24 час" in await self.page.text_content("body"):
                        return RenewalStatus.RATE_LIMITED, "今日已续期"
                    return RenewalStatus.SUCCESS, "续约成功"
            except:
                continue
        return RenewalStatus.FAILED, "未找到续约按钮"

    async def extract_cookies(self) -> Optional[str]:
        try:
            cc = [c for c in await self.ctx.cookies() if "castle-host.com" in c.get("domain", "")]
            return "; ".join([f"{c['name']}={c['value']}" for c in cc]) if cc else None
        except:
            return None


async def process_account(cookie_str: str, idx: int, notifier: Notifier) -> Tuple[Optional[str], List[ServerResult]]:
    cookies = parse_cookies(cookie_str)
    if not cookies:
        logger.error(f"❌ 账号#{idx + 1} Cookie解析失败")
        return None, []

    logger.info(f"{'=' * 50}")
    logger.info(f"📌 处理账号 #{idx + 1}")

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
                started = await client.start_if_stopped(sid)
                expiry = await client.get_expiry(sid)
                d = days_left(expiry)
                logger.info(f"📅 到期: {convert_date(expiry)} ({d}天)")
                status, msg = await client.renew(sid)
                logger.info(f"📝 结果: {msg}")
                results.append(ServerResult(sid, status, msg, expiry, d, started))
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
                await notifier.send(
                    f"🎁 Castle-Host 自动续约通知\n\n"
                    f"👤 账号: #{idx + 1}\n"
                    f"💻 服务器: {r.server_id}\n"
                    f"📅 到期时间: {convert_date(r.expiry)}\n"
                    f"⏳ 剩余天数: {r.days} 天\n"
                    f"🔗 https://cp.castle-host.com/servers/pay/index/{r.server_id}\n\n"
                    f"{started_line}{stat}"
                )

            new_cookie = await client.extract_cookies()
            if new_cookie and new_cookie != cookie_str:
                logger.info(f"🔄 账号#{idx + 1} Cookie已变化")
                return new_cookie, results
            return cookie_str, results

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

    for i, cookie in enumerate(config.cookies_list):
        new, _ = await process_account(cookie, i, notifier)
        if new:
            new_cookies.append(new)
            if new != cookie:
                changed = True
        else:
            new_cookies.append(cookie)
        if i < len(config.cookies_list) - 1:
            await asyncio.sleep(5)

    if changed:
        await github.update_secret("CASTLE_COOKIES", ",".join(new_cookies))

    logger.info("👋 完成")


if __name__ == "__main__":
    asyncio.run(main())
