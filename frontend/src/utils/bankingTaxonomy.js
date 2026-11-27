/** 商业银行一级监管机构 -> 二级监管报送体系字典（与 data-rule-studio 产品家族统一口径）。 */

export const BANK_AGENCIES = [
  {
    code: 'NFRA',
    label: '国家金融监管总局',
    shortLabel: '金监总局',
    hint: '一表通、EAST、1104 非现场、PLUS 专项报送',
    regimes: ['一表通', 'EAST', '1104', 'PLUS'],
  },
  {
    code: 'PBOC',
    label: '中国人民银行',
    shortLabel: '人民银行',
    hint: '人行统计、大集中、金融基础数据、征信与反洗钱',
    regimes: ['人行统计'],
  },
  {
    code: 'SAFE',
    label: '国家外汇管理局',
    shortLabel: '外汇局',
    hint: '外汇业务采集、对外金融资产负债及国际收支申报',
    regimes: ['SAFE'],
  },
  {
    code: 'OTHER',
    label: '通用与其他监管',
    shortLabel: '通用监管',
    hint: '跨条线通用监管与行内管理规范',
    regimes: ['通用监管'],
  },
]

const AGENCY_MAP = Object.fromEntries(BANK_AGENCIES.map((a) => [a.code, a]))

export function agencyForRegime(regime) {
  const clean = String(regime || '').trim()
  if (!clean) return 'OTHER'
  const upper = clean.toUpperCase()
  if (AGENCY_MAP[upper]) return upper
  for (const ag of BANK_AGENCIES) {
    if (ag.regimes.includes(clean) || ag.regimes.some((r) => r.toUpperCase() === upper)) {
      return ag.code
    }
  }
  return 'OTHER'
}

export function agencyMeta(code) {
  if (!code || code === 'ALL') {
    return { code: 'ALL', label: '全部监管机构', shortLabel: '全部机构', hint: '全行所有监管条线' }
  }
  return AGENCY_MAP[String(code).toUpperCase()] || AGENCY_MAP.OTHER
}

export function matchesAgencyOrRegime(itemRegime, { agency = 'ALL', regime = '' } = {}) {
  if (agency && agency !== 'ALL') {
    if (agencyForRegime(itemRegime) !== agency) return false
  }
  if (regime && regime !== 'ALL') {
    if (String(itemRegime || '通用监管').trim() !== regime) return false
  }
  return true
}
