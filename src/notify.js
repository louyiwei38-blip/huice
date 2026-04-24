'use strict';

const axios = require('axios');

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID   = process.env.TELEGRAM_CHAT_ID;

function toBeijing(date) {
  return new Date(date).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

async function sendMessage(text) {
  if (!BOT_TOKEN || !CHAT_ID) {
    console.warn('[Telegram] 未配置 BOT_TOKEN 或 CHAT_ID，跳过推送');
    return;
  }
  try {
    await axios.post(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
      chat_id: CHAT_ID,
      text,
      parse_mode: 'HTML',
    });
  } catch (err) {
    console.error('[Telegram] 推送失败:', err.response?.data?.description || err.message);
  }
}

async function sendSignal(signal, confidence) {
  const emoji   = signal.direction === 'LONG' ? '🟢' : '🔴';
  const dirText = signal.direction === 'LONG' ? '做多' : '做空';
  const tfText  = signal.timeframe === '4h'  ? '4小时' : '1小时';

  const confLine = confidence
    ? `🎯 置信度: ${confidence.stars} ${confidence.level} | 历史胜率 ${(confidence.winRate * 100).toFixed(1)}% (近${confidence.total}次)`
    : `🎯 置信度: 计算中...`;

  const text = [
    `${emoji} <b>BTCUSDT ${dirText}信号</b>`,
    `💰 价格: <code>${signal.price.toFixed(2)}</code> USDT`,
    `📋 原因: ${signal.reason}`,
    `🕐 周期: ${tfText}`,
    `⏰ 时间: ${toBeijing(signal.time)}`,
    confLine,
    `⏱ 策略: 持仓1小时后平仓`,
  ].join('\n');

  await sendMessage(text);
}

async function sendDailyReport(stats) {
  const date       = new Date().toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' });
  const winRateStr = stats.total > 0 ? `${(stats.winRate * 100).toFixed(1)}%` : 'N/A';

  const text = [
    `📊 <b>BTCUSDT 每日报告</b>`,
    `📅 日期: ${date}`,
    `🎯 胜率: ${winRateStr}`,
    `📈 交易次数: ${stats.total}`,
    `✅ 盈利: ${stats.wins} 次`,
    `❌ 亏损: ${stats.losses} 次`,
    `💵 收益摘要: ${stats.pnlSummary}`,
  ].join('\n');

  await sendMessage(text);
}

module.exports = { sendSignal, sendDailyReport, sendMessage };
