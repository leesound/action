const BASE_URL = 'https://ctrl.lunes.host';

async function notifyTelegram({ ok, stage, msg }) {
  try {
    const token = process.env.TELEGRAM_BOT_TOKEN;
    const chatId = process.env.TELEGRAM_CHAT_ID;
    if (!token || !chatId) return;

    const text = [
      `🔔 Lunes 自动操作：${ok ? '✅ 成功' : '❌ 失败'}`,
      `阶段：${stage}`,
      msg ? `信息：${msg}` : '',
      `时间：${new Date().toISOString()}`
    ].filter(Boolean).join('\n');

    await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text })
    });
  } catch (e) {
    console.log('[WARN] Telegram 通知失败:', e.message);
  }
}

async function main() {
  const apiKey = process.env.PTERODACTYL_API_KEY;
  const serverId = process.env.SERVER_ID;

  if (!apiKey || !serverId) {
    console.error('[ERROR] 请设置 PTERODACTYL_API_KEY 和 SERVER_ID');
    process.exit(1);
  }

  console.log(`[INFO] 服务器 ID: ${serverId}`);
  console.log('[INFO] 发送重启请求...');

  try {
    // 发送重启信号
    const response = await fetch(`${BASE_URL}/api/client/servers/${serverId}/power`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      },
      body: JSON.stringify({ signal: 'restart' })
    });

    if (response.status === 204 || response.ok) {
      console.log('[INFO] ✅ 重启命令发送成功！');
      
      await notifyTelegram({
        ok: true,
        stage: '✅ 服务器重启',
        msg: `服务器 ${serverId} 正在重启`
      });
      
      process.exit(0);
    } else {
      const text = await response.text();
      console.error(`[ERROR] 重启失败: ${response.status}`);
      console.error(text);
      
      await notifyTelegram({
        ok: false,
        stage: '重启失败',
        msg: `HTTP ${response.status}: ${text.substring(0, 100)}`
      });
      
      process.exit(1);
    }
  } catch (e) {
    console.error('[ERROR]', e.message);
    
    await notifyTelegram({
      ok: false,
      stage: '异常',
      msg: e.message
    });
    
    process.exit(1);
  }
}

main();
