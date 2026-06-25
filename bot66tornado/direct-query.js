// 直接 API 查詢腳本 - 繞過瀏覽器自動化
require('./env-loader.js');
const https = require('https');

function env(name, fallback = '') {
  return process.env[name] || fallback;
}

// 配置：真實後台帳密放 .env，不要寫進 git。
const CONFIG = {
  baseUrl: env('BACKEND_BASE_URL', 'https://tac3.connect8bo.com'),
  authorization: env('BACKEND_AUTHORIZATION'), // 會自動刷新
  merchantCode: env('BACKEND_MERCHANT_CODE'),
  loginOperator: env('BACKEND_LOGIN_OPERATOR'),
  loginPassword: env('BACKEND_LOGIN_PASSWORD'),
  loginMerchant: env('BACKEND_LOGIN_MERCHANT')
};

const PLAYER_CONTRIBUTION_COLUMNS = [
  'customerType',
  'bankLabelName',
  'clubLabelName',
  'operationLabelName',
  'masterAgentName',
  'customerName',
  'referrerName',
  'referrerAgentName',
  'fundsIn',
  'creditAdj',
  'deposit',
  'depositCounts',
  'depositDays',
  'transferIn',
  'fundsOut',
  'debitAdj',
  'withdraw',
  'withdrawCounts',
  'transferOut',
  'gameBetting',
  'validGameBetting',
  'gameWinning',
  'promotion',
  'gameRebate',
  'referralRebate',
  'agentCommission',
  'dailySalary',
  'profitSharing',
  'pnl',
  'gameDividend',
  'totalOpeningBalance',
  'totalClosingBalance',
  'loginCounts',
  'lastLoginTime',
  'lastLoginIp',
  'lastDepositTime',
  'lastBettingTime',
  'regDate',
  'registerIp',
  'firstDepositDate',
  'firstDepositAmount',
].join(',');

function requireBackendConfig(fields) {
  const missing = fields.filter((field) => !CONFIG[field]);
  if (missing.length) {
    throw new Error(`Missing backend .env config: ${missing.map((field) => `BACKEND_${field.replace(/[A-Z]/g, m => `_${m}`).toUpperCase()}`).join(', ')}`);
  }
}

// 自動登入拿新 token
async function refreshToken(merchantOverride) {
  requireBackendConfig(['loginOperator', 'loginPassword']);
  const loginMerchant = CONFIG.loginMerchant || merchantOverride || CONFIG.merchantCode;
  if (!loginMerchant) throw new Error('Missing backend .env config: BACKEND_LOGIN_MERCHANT or BACKEND_MERCHANT_CODE');
  return new Promise((resolve, reject) => {
    const base = new URL(CONFIG.baseUrl);
    const body = JSON.stringify({
      operatorName: CONFIG.loginOperator,
      password: CONFIG.loginPassword
    });
    const req = https.request({
      hostname: base.hostname,
      path: '/tac/api/login/password',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
        'Merchant': loginMerchant,
        'Referer': `${CONFIG.baseUrl}/${loginMerchant}`,
        'Origin': CONFIG.baseUrl
      }
    }, (res) => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try {
          const j = JSON.parse(data);
          if (j.token) {
            CONFIG.authorization = j.token;
            console.log(`🔑 token 已刷新: ${j.token.substring(0, 8)}...`);
            resolve(j.token);
          } else {
            reject(new Error('login: no token in response'));
          }
        } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.write(body); req.end();
  });
}

function extractRows(result) {
  if (!result) return [];
  const candidates = [
    result.value,
    result.value?.list,
    result.value?.data,
    result.value?.records,
    result.value?.rows,
    result.value?.items,
    result.value?.content,
    result.value?.result,
    result.data,
    result.data?.list,
    result.data?.data,
    result.data?.records,
    result.data?.rows,
    result.data?.items,
    result.data?.content,
    result.records,
    result.rows,
    result.items,
    result.content,
    result.result,
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) return candidate;
  }
  const queue = [result.value, result.data, result.result].filter(Boolean);
  const seen = new Set();
  while (queue.length) {
    const node = queue.shift();
    if (!node || typeof node !== 'object' || seen.has(node)) continue;
    seen.add(node);
    if (Array.isArray(node) && node.some((row) => row && typeof row === 'object' && !Array.isArray(row))) {
      return node;
    }
    if (!Array.isArray(node)) {
      for (const value of Object.values(node)) {
        if (value && typeof value === 'object') queue.push(value);
      }
    }
  }
  return [];
}

function backendApiError(result, context = 'backend API') {
  const code = result?.errorCode || result?.code || 'UNKNOWN_BACKEND_ERROR';
  const message = result?.message || result?.msg || code;
  const err = new Error(`${context} failed: ${code} ${message}`);
  err.code = code;
  err.backendResponse = result;
  return err;
}

function assertBackendSuccess(result, context = 'backend API') {
  if (result && result.success === false) {
    throw backendApiError(result, context);
  }
  return result;
}

function toMoneyNumber(value) {
  if (value == null || value === '') return 0;
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  const normalized = String(value)
    .replace(/,/g, '')
    .replace(/[^\d.-]/g, '');
  const n = Number(normalized);
  return Number.isFinite(n) ? n : 0;
}

function sumMoney(records, field, fallbackField) {
  return (records || []).reduce((sum, record) => {
    if (record && record[field] != null) return sum + toMoneyNumber(record[field]);
    if (fallbackField && record && record[fallbackField] != null) return sum + toMoneyNumber(record[fallbackField]);
    return sum;
  }, 0);
}

function roundMoney(n) {
  return Math.round((Number(n) || 0) * 100) / 100;
}

function calculateTurnoverStatus(records) {
  const rows = Array.isArray(records) ? records : [];
  const totalDeposit = roundMoney(sumMoney(rows, 'fundsIn', 'deposit'));
  const totalValidBet = roundMoney(sumMoney(rows, 'validGameBetting'));
  const remainingTurnover = roundMoney(Math.max(totalDeposit - totalValidBet, 0));
  return {
    totalDeposit,
    totalValidBet,
    remainingTurnover,
    isMet: remainingTurnover <= 0,
    recordsCount: rows.length,
  };
}

function dateOnly(d) {
  const x = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(x.getTime())) return '';
  return x.toISOString().split('T')[0];
}

function dateOnlyLocal(d) {
  const x = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(x.getTime())) return '';
  const y = x.getFullYear();
  const m = String(x.getMonth() + 1).padStart(2, '0');
  const day = String(x.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function latestSuccessfulDeposit(records) {
  const rows = Array.isArray(records) ? records.filter(Boolean) : [];
  const successStatus = new Set(['A', 'APPROVED', 'SUCCESS', 'SUCCESSFUL', 'COMPLETED', 'PAID']);
  return rows
    .filter((r) => successStatus.has(String(r.depositStatus || r.status || '').toUpperCase()))
    .slice()
    .sort((a, b) => new Date(b.requestDate || b.approveDate || 0) - new Date(a.requestDate || a.approveDate || 0))[0] || null;
}

function calculateLatestDepositTurnoverStatus(latestDeposit, contributionRecords) {
  if (!latestDeposit) {
    return {
      latestDeposit: null,
      latestDepositAmount: 0,
      latestDepositDate: null,
      totalValidBet: 0,
      remainingTurnover: 0,
      isMet: false,
      recordsCount: 0,
    };
  }
  const latestDepositAmount = roundMoney(toMoneyNumber(latestDeposit.requestAmount || latestDeposit.depositAmount || latestDeposit.amount));
  const totalValidBet = roundMoney(sumMoney(contributionRecords || [], 'validGameBetting'));
  const remainingTurnover = roundMoney(Math.max(latestDepositAmount - totalValidBet, 0));
  return {
    latestDeposit,
    latestDepositAmount,
    latestDepositDate: dateOnly(latestDeposit.requestDate || latestDeposit.approveDate),
    totalValidBet,
    remainingTurnover,
    isMet: remainingTurnover <= 0 && latestDepositAmount > 0,
    recordsCount: Array.isArray(contributionRecords) ? contributionRecords.length : 0,
  };
}

function firstPresent(record, keys) {
  for (const key of keys) {
    if (record && record[key] != null && record[key] !== '') return record[key];
  }
  return null;
}

function isIncompleteTurnoverStatus(value) {
  const s = String(value || '').trim().toUpperCase();
  if (!s) return false;
  return s.includes('未完成')
    || s.includes('未达成')
    || s.includes('未達成')
    || s.includes('未满足')
    || s.includes('未滿足')
    || s.includes('INCOMPLETE')
    || s.includes('UNFINISHED')
    || s.includes('PENDING')
    || s === 'I'
    || s === 'N'
    || s === 'NO'
    || s === 'OPEN';
}

function isCompleteTurnoverStatus(value) {
  const s = String(value || '').trim().toUpperCase();
  if (!s) return false;
  return s.includes('完成')
    || s.includes('已达成')
    || s.includes('已達成')
    || s.includes('已满足')
    || s.includes('已滿足')
    || s.includes('COMPLETE')
    || s.includes('COMPLETED')
    || s.includes('FINISHED')
    || s.includes('CLOSED')
    || s === 'C'
    || s === 'Y'
    || s === 'YES'
    || s === 'DONE';
}

function normalizeTurnoverRequirementRecord(record) {
  const status = firstPresent(record, [
    'statusName',
    'turnoverStatusName',
    'turnoverCheckingStatusName',
    'checkingStatusName',
    'auditStatusName',
    'completeStatusName',
    'status',
    'turnoverStatus',
    'turnoverCheckingStatus',
    'checkingStatus',
    'statusI18n',
    'state',
    'auditStatus',
    'completeStatus',
  ]);
  const remainingRaw = firstPresent(record, [
    'remainingTurnover',
    'remainingFlow',
    'remainingWater',
    'remainingWaterAmount',
    'remainTurnover',
    'remainFlow',
    'remainWater',
    'remainingRollover',
    'remainRollover',
    'remainingAmount',
    'remainAmount',
    'leftTurnover',
    'leftAmount',
    'surplusTurnover',
    'remainValidBet',
    'unfinishTurnover',
    'unfinishedTurnover',
    'unCompletedTurnover',
    'uncompletedTurnover',
    'remainingBet',
    'remainBet',
    'requiredBettingRemaining',
    'turnoverBalance',
  ]);
  const requiredRaw = firstPresent(record, [
    'turnoverRequirement',
    'turnoverRequirementAmount',
    'turnoverAmount',
    'requiredTurnover',
    'requireTurnover',
    'turnoverRequired',
    'bettingRequirement',
    'requiredBetting',
    'requirementAmount',
    'requiredAmount',
    'flowRequirement',
    'waterRequirement',
    'betRequirement',
    'targetTurnover',
  ]);
  const validRaw = firstPresent(record, [
    'validTurnover',
    'validFlow',
    'validWater',
    'validBetting',
    'validGameBetting',
    'effectiveTurnover',
    'effectiveBetting',
    'completedTurnover',
    'completedAmount',
    'finishedTurnover',
    'accumulatedTurnover',
  ]);
  const remainingTurnover = toMoneyNumber(remainingRaw);
  const hasStatus = status != null && String(status).trim() !== '';
  const statusIncomplete = isIncompleteTurnoverStatus(status);
  const statusComplete = isCompleteTurnoverStatus(status);
  const isIncomplete = statusIncomplete || (!hasStatus && !statusComplete && remainingTurnover > 0);
  return {
    raw: record,
    transactionTime: firstPresent(record, ['transactionTime', 'transactionDate', 'txnTime', 'createTime', 'createdTime', 'requestDate', 'lastUpdateTime']),
    transactionType: firstPresent(record, ['transactionTypeName', 'transactionType', 'txTypeName', 'typeName', 'type']),
    transactionId: firstPresent(record, ['transactionId', 'transactionNo', 'depositId', 'orderId', 'id']),
    amount: toMoneyNumber(firstPresent(record, ['amount', 'transactionAmount', 'depositAmount', 'requestAmount'])),
    requiredTurnover: toMoneyNumber(requiredRaw),
    validTurnover: toMoneyNumber(validRaw),
    remainingTurnover,
    status,
    isIncomplete,
  };
}

// 通用請求函數
function apiRequest(path, params = {}, merchantOverride) {
  requireBackendConfig(['authorization']);
  return new Promise((resolve, reject) => {
    const queryString = new URLSearchParams(params).toString();
    const url = `${CONFIG.baseUrl}${path}?${queryString}`;
    const mc = merchantOverride || CONFIG.merchantCode;
    if (!mc) {
      reject(new Error('Missing backend .env config: BACKEND_MERCHANT_CODE'));
      return;
    }

    const options = {
      method: 'GET',
      headers: {
        'Accept': 'application/json, text/plain, */*',
        'Authorization': CONFIG.authorization,
        'Merchant': mc,
        'merchantCode': mc,
        'Language': 'zh_CN',
        'environment': 'TCG1',
        'platform': 'TCG',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
      }
    };

    https.get(url, options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(new Error('Invalid JSON response'));
        }
      });
    }).on('error', reject);
  });
}

async function apiGet(path, params = {}, merchantCode) {
  let result = await apiRequest(path, params, merchantCode);
  if (result && result.errorCode === 'INVALID_TOKEN') {
    console.log('⚠️  token 失效，自動登入...');
    await refreshToken(merchantCode);
    result = await apiRequest(path, params, merchantCode);
  }
  return result;
}

// 查詢充值記錄（token 失效會自動重登入一次）
async function queryDeposit(username, dateFrom, dateTo, merchantCode) {
  // merchantCode 可選；不傳就用 CONFIG 預設（COP）。同一個 operator 可跨商戶查，只要換 header + param。
  const mc = merchantCode || CONFIG.merchantCode;
  if (!mc) throw new Error('Missing backend .env config: BACKEND_MERCHANT_CODE');
  if (!CONFIG.authorization) {
    console.log('⚠️  token 未設定，自動登入...');
    await refreshToken(mc);
  }
  console.log(`\n🔍 查詢用戶: ${username} @ merchant ${mc}`);
  console.log(`📅 日期範圍: ${dateFrom} ~ ${dateTo}`);

  const params = {
    searchDateMode: 'requestTime',
    dateFrom: dateFrom + ' 00:00:00',
    dateTo: dateTo + ' 23:59:59',
    merchantCode: mc,
    pageSize: 50,
    pageNo: 1,
    username: username,
    sortBy: '',
    sortOrder: '',
    pid: '610151'
  };

  try {
    let result = await apiRequest('/tac/api/relay/get/pv2-mcs-internal-v3-player-deposit-search', params, mc);

    // token 失效 → 自動刷新重試一次
    if (result && result.errorCode === 'INVALID_TOKEN') {
      console.log('⚠️  token 失效，自動登入...');
      await refreshToken(mc);
      result = await apiRequest('/tac/api/relay/get/pv2-mcs-internal-v3-player-deposit-search', params, mc);
    }

    assertBackendSuccess(result, 'deposit search');
    console.log('\n✅ API 請求成功！');
    
    // 修正：API 返回的數據在 result.value 中
    if (result.success && result.value && result.value.length > 0) {
      console.log(`\n📊 找到 ${result.value.length} 筆充值記錄：\n`);
      
      result.value.forEach((record, index) => {
        const date = new Date(record.requestDate).toISOString().split('T')[0];
        console.log(`${index + 1}. 交易流水號: ${record.depositId}`);
        console.log(`   金額: ${record.requestAmount}`);
        console.log(`   時間: ${date}`);
        console.log(`   狀態: ${record.depositStatus}`);
        console.log(`   參考號: ${record.bankRef || 'N/A'}`);
        console.log('');
      });
      
      return result.value;
    } else {
      console.log('⚠️ 未找到充值記錄');
      console.log('完整響應:', JSON.stringify(result, null, 2));
      return [];
    }
    
  } catch (error) {
    console.error('❌ 查詢失敗:', error.message);
    throw error;
  }
}

async function queryPlayerUser(username, merchantCode, options = {}) {
  const mc = merchantCode || CONFIG.merchantCode;
  if (!mc) throw new Error('Missing backend .env config: BACKEND_MERCHANT_CODE');
  if (!CONFIG.authorization) {
    console.log('⚠️  token 未設定，自動登入...');
    await refreshToken(mc);
  }

  const searchCodes = options.searchCodes || ['USERNAME', 'MOBILE'];
  for (const searchCode of searchCodes) {
    const params = {
      merchantCode: mc,
      isWildcard: 'false',
      sortType: 'desc',
      pageable: 'true',
      data: username,
      searchCode,
    };
    const result = await apiGet('/tac/api/relay/get/player-search-non-bankcard', params, mc);
    assertBackendSuccess(result, `player search ${searchCode}`);
    const rows = extractRows(result);
    const exact = rows.find((row) => {
      const names = [
        row.customerName,
        row.username,
        row.loginName,
        row.mobile,
        row.phone,
      ].filter(Boolean).map(String);
      return names.some((name) => name === String(username));
    });
    const picked = exact || rows[0];
    if (picked) {
      return {
        searchCode,
        raw: picked,
        customerId: picked.customerId || picked.id || picked.customerID,
        customerName: picked.customerName || picked.username || picked.loginName || username,
      };
    }
  }
  return null;
}

function turnoverRequirementRange(days = 30, now = new Date()) {
  const to = new Date(now);
  const from = new Date(to);
  from.setDate(from.getDate() - days);
  return {
    startDate: `${dateOnlyLocal(from)} 00:00:00`,
    endDate: `${dateOnlyLocal(to)} 23:59:59`,
  };
}

function turnoverRequirementMonthRange(now = new Date()) {
  const to = new Date(now);
  const from = new Date(to);
  from.setDate(1);
  return {
    startDate: `${dateOnlyLocal(from)} 00:00:00`,
    endDate: `${dateOnlyLocal(to)} 23:59:59`,
  };
}

function turnoverRequirementLastWithdrawalWindow(now = new Date()) {
  const to = new Date(now);
  const from = new Date(to);
  from.setDate(from.getDate() - 1);
  return {
    startDate: `${dateOnlyLocal(from)} 12:00:00`,
    endDate: `${dateOnlyLocal(to)} 23:59:59`,
  };
}

function buildTurnoverRequirementQueries(options = {}) {
  const explicitRange = options.startDate || options.endDate || options.dateType;
  if (explicitRange) {
    const fallback = turnoverRequirementRange(Number(options.lookbackDays || 30), options.now);
    return [{
      queryMode: 'explicit',
      dateType: options.dateType || 'C',
      startDate: options.startDate || fallback.startDate,
      endDate: options.endDate || fallback.endDate,
    }];
  }
  const lookbackDays = Number(options.lookbackDays || 30);
  const shortRange = turnoverRequirementRange(lookbackDays, options.now);
  const longRange = turnoverRequirementRange(Number(options.fallbackLookbackDays || 90), options.now);
  const monthRange = turnoverRequirementMonthRange(options.now);
  const withdrawalRange = turnoverRequirementLastWithdrawalWindow(options.now);
  return [
    { queryMode: 'custom_recent', dateType: 'C', ...shortRange },
    { queryMode: 'custom_month', dateType: 'C', ...monthRange },
    { queryMode: 'last_withdrawal_window', dateType: 'W', ...withdrawalRange },
    { queryMode: 'last_withdrawal_recent', dateType: 'W', ...shortRange },
    { queryMode: 'custom_lookback', dateType: 'C', ...longRange },
  ];
}

function summarizeTurnoverRequirementQuery(username, mc, player, query, records, queryAttempts) {
  const activeRequirements = records.filter((record) => record.isIncomplete);
  const remainingTurnover = roundMoney(activeRequirements.reduce((sum, record) => sum + toMoneyNumber(record.remainingTurnover), 0));
  return {
    username,
    merchantCode: mc,
    source: 'turnover_requirement',
    playerFound: true,
    customerId: player.customerId,
    customerName: player.customerName,
    queryMode: query.queryMode,
    dateType: query.dateType,
    startDate: query.startDate,
    endDate: query.endDate,
    records,
    activeRequirements,
    remainingTurnover,
    isMet: activeRequirements.length === 0 || remainingTurnover <= 0,
    recordsCount: records.length,
    activeRequirementsCount: activeRequirements.length,
    queryAttempts,
  };
}

async function queryTurnoverRequirement(username, merchantCode, options = {}) {
  const mc = merchantCode || CONFIG.merchantCode;
  if (!mc) throw new Error('Missing backend .env config: BACKEND_MERCHANT_CODE');
  const player = await queryPlayerUser(username, mc, options);
  const queries = buildTurnoverRequirementQueries(options);
  const firstQuery = queries[0];
  if (!player || !player.customerId) {
    return {
      username,
      merchantCode: mc,
      source: 'turnover_requirement',
      playerFound: false,
      customerId: null,
      customerName: null,
      queryMode: firstQuery.queryMode,
      dateType: firstQuery.dateType,
      startDate: firstQuery.startDate,
      endDate: firstQuery.endDate,
      records: [],
      activeRequirements: [],
      remainingTurnover: 0,
      isMet: false,
      recordsCount: 0,
      activeRequirementsCount: 0,
      queryAttempts: [],
    };
  }

  const queryAttempts = [];
  let best = null;
  for (const query of queries) {
    const params = {
      merchantCode: mc,
      customerId: player.customerId,
      dateType: query.dateType,
      startDate: query.startDate,
      endDate: query.endDate,
      pageNo: 1,
      pageSize: options.pageSize || 20,
    };
    const result = await apiGet('/tac/api/relay/get/mcs-player-promotion-turnover-checking-getTurnoverCheckingRecord', params, mc);
    assertBackendSuccess(result, `turnover requirement search ${query.queryMode}`);
    const records = extractRows(result).map(normalizeTurnoverRequirementRecord);
    const summary = summarizeTurnoverRequirementQuery(username, mc, player, query, records, queryAttempts);
    queryAttempts.push({
      queryMode: query.queryMode,
      dateType: query.dateType,
      startDate: query.startDate,
      endDate: query.endDate,
      recordsCount: summary.recordsCount,
      activeRequirementsCount: summary.activeRequirementsCount,
      remainingTurnover: summary.remainingTurnover,
    });
    if (summary.activeRequirementsCount > 0) return summary;
    if (!best || summary.recordsCount > best.recordsCount) best = summary;
  }
  return best || summarizeTurnoverRequirementQuery(username, mc, player, firstQuery, [], queryAttempts);
}

// 查詢玩家貢獻報表：用入金 - 有效投注計算剩餘流水。
async function queryPlayerContribution(username, dateFrom, dateTo, merchantCode) {
  const mc = merchantCode || CONFIG.merchantCode;
  if (!mc) throw new Error('Missing backend .env config: BACKEND_MERCHANT_CODE');
  if (!CONFIG.authorization) {
    console.log('⚠️  token 未設定，自動登入...');
    await refreshToken(mc);
  }

  const params = {
    subordinateNames: username,
    subordinateMapType: 'AGENT',
    subordinateType: 'DIRECT',
    startDate: dateFrom,
    endDate: dateTo,
    'list[0][andOr]': 'AND',
    'list[1][andOr]': 'OR',
    columnsList: PLAYER_CONTRIBUTION_COLUMNS,
    hasExcludeMemberLabelId: 'false',
    hasExcludePayLabelId: 'false',
    isShowExportBankLabel: 'true',
    isShowExportOperationLabel: 'true',
    merchantCode: mc,
    regStartDate: '',
    regEndDate: '',
    size: 50,
    page: 1,
    pageable: 'true',
  };

  let result = await apiRequest('/tac/api/relay/post/ods-v2-report-player-contributionv2-search', params, mc);
  if (result && result.errorCode === 'INVALID_TOKEN') {
    console.log('⚠️  token 失效，自動登入...');
    await refreshToken(mc);
    result = await apiRequest('/tac/api/relay/post/ods-v2-report-player-contributionv2-search', params, mc);
  }
  assertBackendSuccess(result, 'player contribution search');
  return extractRows(result);
}

async function queryTurnover(username, dateFrom, dateTo, merchantCode) {
  const records = await queryPlayerContribution(username, dateFrom, dateTo, merchantCode);
  return {
    username,
    merchantCode: merchantCode || CONFIG.merchantCode,
    dateFrom,
    dateTo,
    ...calculateTurnoverStatus(records),
    records,
    source: 'player_contribution',
  };
}

async function queryTurnoverFromLatestDeposit(username, merchantCode, options = {}) {
  const mc = merchantCode || CONFIG.merchantCode;
  if (!mc) throw new Error('Missing backend .env config: BACKEND_MERCHANT_CODE');
  const lookbackDays = Number(options.lookbackDays || 90);
  const today = new Date();
  const from = new Date(today);
  from.setDate(from.getDate() - lookbackDays);
  const dateFrom = options.dateFrom || dateOnly(from);
  const dateTo = options.dateTo || dateOnly(today);

  const deposits = await queryDeposit(username, dateFrom, dateTo, mc);
  const latestDeposit = latestSuccessfulDeposit(deposits);
  if (!latestDeposit) {
    return {
      username,
      merchantCode: mc,
      dateFrom,
      dateTo,
      source: 'latest_deposit',
      deposits,
      records: [],
      ...calculateLatestDepositTurnoverStatus(null, []),
    };
  }

  const latestDepositDate = dateOnly(latestDeposit.requestDate || latestDeposit.approveDate) || dateFrom;
  const contributionRecords = await queryPlayerContribution(username, latestDepositDate, dateTo, mc);
  return {
    username,
    merchantCode: mc,
    dateFrom: latestDepositDate,
    dateTo,
    source: 'latest_deposit',
    deposits,
    records: contributionRecords,
    ...calculateLatestDepositTurnoverStatus(latestDeposit, contributionRecords),
  };
}

// 匹配充值記錄（根據金額和參考號）
function matchDeposit(records, targetAmount, targetDate, targetRef) {
  console.log(`\n🎯 匹配條件:`);
  if (targetAmount) console.log(`   金額: ${targetAmount}`);
  if (targetRef) console.log(`   參考號: ${targetRef}`);
  
  for (const record of records) {
    const amount = parseFloat(record.requestAmount || record.depositAmount || 0);
    const recordDate = new Date(record.requestDate).toISOString().split('T')[0];
    const ref = record.bankRef || record.tpRefNo || '';
    
    // 如果只提供金額
    if (targetAmount && !targetRef) {
      if (Math.abs(amount - targetAmount) < 0.01) {
        console.log(`\n✅ 找到匹配記錄（金額: ${amount}）`);
        console.log(`   交易流水號: ${record.depositId}`);
        console.log(`   日期: ${recordDate}`);
        console.log(`   參考號: ${ref}`);
        return record;
      }
    }
    
    // 如果提供金額+參考號
    if (targetAmount && targetRef) {
      if (
        Math.abs(amount - targetAmount) < 0.01 &&
        ref.toUpperCase().includes(targetRef.toUpperCase())
      ) {
        console.log(`\n✅ 找到匹配記錄（金額: ${amount}, 參考號: ${ref}）`);
        console.log(`   交易流水號: ${record.depositId}`);
        console.log(`   日期: ${recordDate}`);
        return record;
      }
    }
  }
  
  console.log(`\n⚠️ 未找到匹配的充值記錄`);
  console.log(`   提示: 請檢查金額和參考號是否正確`);
  return null;
}

// 主函數
async function main() {
  const args = process.argv.slice(2);

  if (args[0] === '--turnover') {
    if (args.length < 2) {
      console.log('用法: node direct-query.js --turnover <用戶名> [merchantCode]');
      process.exit(1);
    }
    const [, username, merchantCode] = args;
    const result = await queryTurnoverRequirement(username, merchantCode);
    console.log(JSON.stringify({
      username: result.username,
      merchantCode: result.merchantCode,
      source: result.source,
      playerFound: result.playerFound,
      customerId: result.customerId,
      customerName: result.customerName,
      queryMode: result.queryMode,
      dateType: result.dateType,
      startDate: result.startDate,
      endDate: result.endDate,
      recordsCount: result.recordsCount,
      activeRequirementsCount: result.activeRequirementsCount,
      remainingTurnover: result.remainingTurnover,
      isMet: result.isMet,
      activeRequirements: result.activeRequirements?.map((record) => ({
        transactionTime: record.transactionTime,
        transactionType: record.transactionType,
        amount: record.amount,
        requiredTurnover: record.requiredTurnover,
        validTurnover: record.validTurnover,
        remainingTurnover: record.remainingTurnover,
        status: record.status,
      })) || [],
      queryAttempts: result.queryAttempts || [],
    }, null, 2));
    return;
  }

  if (args[0] === '--turnover-estimate') {
    if (args.length < 2) {
      console.log('用法: node direct-query.js --turnover-estimate <用戶名> [merchantCode]');
      process.exit(1);
    }
    const [, username, merchantCode] = args;
    const result = await queryTurnoverFromLatestDeposit(username, merchantCode);
    console.log(JSON.stringify({
      username: result.username,
      merchantCode: result.merchantCode,
      dateFrom: result.dateFrom,
      dateTo: result.dateTo,
      latestDepositAmount: result.latestDepositAmount,
      latestDepositDate: result.latestDepositDate,
      totalValidBet: result.totalValidBet,
      remainingTurnover: result.remainingTurnover,
      isMet: result.isMet,
      recordsCount: result.recordsCount,
    }, null, 2));
    return;
  }
  
  if (args.length < 3) {
    console.log('用法: node direct-query.js <用戶名> <開始日期> <結束日期> [金額] [參考號]');
    console.log('示例: node direct-query.js eileenbarrios 2026-04-08 2026-04-15 10000 M23680701');
    process.exit(1);
  }
  
  const [username, dateFrom, dateTo, amount, ref] = args;
  
  const records = await queryDeposit(username, dateFrom, dateTo);
  
  if (amount && records.length > 0) {
    const matched = matchDeposit(records, parseFloat(amount), '', ref);
    if (matched) {
      console.log(`\n📝 結果：交易流水號 = ${matched.depositId}`);
    }
  }
}

// 導出供其他模塊使用
module.exports = {
  queryDeposit,
  matchDeposit,
  queryPlayerContribution,
  queryTurnover,
  queryPlayerUser,
  queryTurnoverRequirement,
  queryTurnoverFromLatestDeposit,
  calculateTurnoverStatus,
  calculateLatestDepositTurnoverStatus,
  normalizeTurnoverRequirementRecord,
  latestSuccessfulDeposit,
  toMoneyNumber,
  assertBackendSuccess,
  CONFIG,
};

// 如果直接執行
if (require.main === module) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
