'use strict';

require('dotenv').config();

const { runBacktest } = require('./src/backtest');

runBacktest()
  .then(() => { console.log('\n回测完成'); process.exit(0); })
  .catch(err => { console.error('回测失败:', err); process.exit(1); });
