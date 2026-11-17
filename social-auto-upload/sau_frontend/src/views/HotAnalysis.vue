<template>
  <div class="hot-workbench">
    <!-- ============ 抓取配置 ============ -->
    <el-card shadow="never" class="cfg-card">
      <template #header>
        <div class="card-header">
          <div>
            <span class="title">爆款采集</span>
            <span class="subtitle">选领域 → 定条数 → 抓回来逐条拆解</span>
          </div>
          <el-tag v-if="lastCapture" size="small" type="info">最近抓取 {{ fmtTime(lastCapture) }}</el-tag>
        </div>
      </template>

      <el-form :inline="false" label-width="96px" size="default" class="cfg-form">
        <el-row :gutter="16">
          <el-col :span="10">
            <el-form-item label="抓取方式">
              <el-radio-group v-model="form.mode" @change="onModeChange">
                <el-radio-button value="domain">按领域</el-radio-button>
                <el-radio-button value="hot">按热搜</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="7">
            <el-form-item label="平台">
              <el-select v-model="form.platform" style="width: 100%" @change="loadAuth">
                <el-option label="小红书" value="xiaohongshu" />
                <el-option label="抖音" value="douyin" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="7">
            <el-form-item label="抓取条数">
              <el-input-number v-model="form.limit" :min="3" :max="200" :step="5" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item v-if="form.mode === 'domain'" label="领域">
          <el-select v-model="form.domains" multiple collapse-tags collapse-tags-tooltip
            placeholder="可多选，每个领域会自动展开成一组搜索词" style="width: 100%">
            <el-option v-for="d in domains" :key="d.name" :value="d.name"
              :label="`${d.name}（${d.keyword_count} 个词）`">
              <span>{{ d.name }}</span>
              <span class="opt-hint">{{ d.keywords.slice(0, 4).join(' / ') }}</span>
            </el-option>
          </el-select>
        </el-form-item>

        <el-form-item v-if="form.use_mc" label="登录态">
          <span class="auth-text" :class="authOk ? 'ok' : 'bad'">{{ authText }}</span>
          <el-button v-if="!authOk" link type="primary" size="small" @click="goAccounts">
            去账号管理登录
          </el-button>
        </el-form-item>

        <el-form-item label="补充词">
          <el-input v-model="form.custom" clearable
            :placeholder="form.mode === 'hot' ? '热搜模式不需要填，关键词自动从榜单抽取' : '可选，多个词用逗号分隔，如：露营,围炉煮茶'" />
        </el-form-item>

        <el-form-item label="执行抓取">
          <el-switch v-model="form.use_mc" active-text="真抓取" inactive-text="只读本地数据"
            @change="onUseMcChange" />
          <span class="hint">
            {{ form.use_mc
              ? (form.platform === 'douyin'
                  ? '复用「账号管理」里已登录的抖音账号，无头运行不弹浏览器，耗时 1-3 分钟'
                  : '会启动 MediaCrawler（有头浏览器），沿用账号管理的小红书登录态，耗时 2-5 分钟')
              : '不联网，只把本地已抓到的数据按当前关键词筛出来（用来调页面最快）' }}
          </span>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="running" @click="startCapture">
            {{ running ? '抓取中…' : '开始抓取' }}
          </el-button>
          <el-button :disabled="running" @click="refreshItems">刷新列表</el-button>
          <span v-if="taskMsg" class="task-msg">{{ taskMsg }}</span>
        </el-form-item>

        <el-progress v-if="running || taskProgress > 0" :percentage="taskProgress"
          :status="taskStatus === 'error' ? 'exception' : (taskStatus === 'done' ? 'success' : '')"
          :stroke-width="6" style="margin-bottom: 6px" />
        <div v-if="taskLogs.length" class="task-logs">
          <div v-for="(l, i) in taskLogs.slice(-4)" :key="i" class="task-log">{{ l.msg }}</div>
        </div>
      </el-form>
    </el-card>

    <!-- ============ 结果列表 ============ -->
    <el-card shadow="never" class="list-card">
      <template #header>
        <div class="card-header">
          <div>
            <span class="title">抓取结果</span>
            <span class="subtitle" v-if="items.length">
              共 {{ items.length }} 条 · 封面 {{ coverOk }} 张 · 已分析 {{ analyzedCount }} 条
            </span>
          </div>
          <div class="list-actions">
            <el-input v-model="filters.q" placeholder="搜索标题/正文" clearable size="small"
              style="width: 180px" @change="loadItems" />
            <el-select v-model="filters.sort" size="small" style="width: 110px" @change="loadItems">
              <el-option label="按点赞" value="likes" />
              <el-option label="按收藏" value="collects" />
              <el-option label="按评论" value="comments" />
            </el-select>
          </div>
        </div>
      </template>

      <el-empty v-if="!items.length && !loadingItems"
        description="还没有数据，上面配置好点「开始抓取」" />

      <div v-loading="loadingItems" class="grid">
        <div v-for="it in items" :key="it.platform + ':' + it.id" class="item-card">
          <div class="cover-wrap" @click="openOriginal(it)">
            <el-image :src="coverSrc(it)" fit="cover" class="cover" lazy>
              <template #error>
                <div class="cover-fallback">
                  <el-icon><Picture /></el-icon>
                  <span>封面已过期</span>
                </div>
              </template>
            </el-image>
            <span class="platform-badge">{{ it.platform === 'douyin' ? '抖音' : '小红书' }}</span>
            <span v-if="it.analyzed" class="analyzed-badge">已分析</span>
            <span v-else-if="isAnalyzing(it)" class="analyzing-badge">分析中…</span>
          </div>

          <div class="item-body">
            <div class="item-title" :title="it.title">{{ it.title || '（无标题）' }}</div>
            <div class="item-meta">
              <span>{{ it.author || '—' }}</span>
              <span v-if="it.keyword" class="kw"># {{ it.keyword }}</span>
            </div>
            <div class="item-stats">
              <span>赞 {{ fmt(it.likes) }}</span>
              <span>藏 {{ fmt(it.collects) }}</span>
              <span>评 {{ fmt(it.comments) }}</span>
            </div>
            <div class="item-actions">
              <el-button size="small" text @click="openOriginal(it)">
                看原帖<el-icon class="el-icon--right"><TopRight /></el-icon>
              </el-button>
              <el-button size="small" title="每次分析会起一个独立的 agent 任务" type="primary" plain
                :loading="isAnalyzing(it)" @click="analyzeItem(it)">
                {{ isAnalyzing(it) ? '分析中' : (it.analyzed ? '再看' : '分析') }}
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </el-card>

    <!-- ============ Agent 配置 ============ -->
    <el-card shadow="never" class="agent-card">
      <template #header>
        <div class="card-header">
          <span class="title">Agent 配置（WorkBuddy CLI）</span>
          <el-tag :type="agent.available ? 'success' : 'danger'" size="small">
            {{ agent.available ? 'CLI 已就绪' : 'CLI 未找到' }}
          </el-tag>
        </div>
      </template>
      <el-form label-width="110px" size="small">
        <el-form-item label="CLI 入口">
          <span class="mono">{{ agent.cli || '—' }}</span>
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="agentCfg.agent_model" :placeholder="agent.model || 'custom-local:qwen3.8-27b'"
            style="max-width: 340px" />
          <span class="hint">留空用默认本地模型；换成 hy4flash 等云端模型需先在终端 `codebuddy` 里 /login</span>
        </el-form-item>
        <el-form-item label="超时(秒)">
          <el-input-number v-model="agentCfg.agent_timeout" :min="30" :max="600" :step="30" />
          <el-switch v-model="agentOn" active-text="启用 agent" style="margin-left: 16px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="saveAgentCfg">保存配置</el-button>
          <el-button :loading="probing" @click="probeAgent">测试连通性</el-button>
          <span v-if="probeResult" class="hint">{{ probeResult }}</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- ============ 分析抽屉 ============ -->
    <el-drawer v-model="drawer" :size="620" :title="drawerTitle" destroy-on-close>
      <div v-if="currentItem && isAnalyzing(currentItem) && !analysis" class="drawer-loading">
        <el-skeleton :rows="8" animated />
        <p class="hint" style="margin-top: 12px">
          agent 正在拆解，通常 1-3 分钟（本地模型推理较慢；同时跑多条会互相拖慢）…
        </p>
      </div>

      <template v-else-if="analysis">
        <el-alert v-if="analysisError" :title="analysisError" type="warning" show-icon :closable="false"
          style="margin-bottom: 12px" />

        <div class="an-head">
          <el-tag :type="engineTag" size="small">{{ engineText }}</el-tag>
          <span v-if="analysisMs" class="hint">耗时 {{ (analysisMs / 1000).toFixed(1) }}s</span>
          <span class="score-wrap">
            <span class="score-label">爆款潜力</span>
            <el-progress :percentage="Number(analysis.score || 0)" :stroke-width="10"
              :color="scoreColor" style="width: 180px" />
          </span>
        </div>

        <h3 class="an-verdict">{{ analysis.verdict }}</h3>

        <div class="an-block" v-if="analysis.hook">
          <div class="an-label">钩子</div>
          <p>{{ analysis.hook }}</p>
        </div>

        <div class="an-block" v-if="analysis.structure && analysis.structure.length">
          <div class="an-label">内容结构</div>
          <ul><li v-for="(s, i) in analysis.structure" :key="i">{{ s }}</li></ul>
        </div>

        <div class="an-block" v-if="analysis.emotion">
          <div class="an-label">情绪 / 需求</div>
          <p>{{ analysis.emotion }}</p>
        </div>

        <div class="an-block" v-if="analysis.why_viral && analysis.why_viral.length">
          <div class="an-label">为什么爆</div>
          <ul><li v-for="(s, i) in analysis.why_viral" :key="i">{{ s }}</li></ul>
        </div>

        <div class="an-block" v-if="analysis.title_formula">
          <div class="an-label">标题公式</div>
          <p class="mono">{{ analysis.title_formula }}</p>
        </div>

        <div class="an-block" v-if="analysis.reusable">
          <div class="an-label">可复用脚本</div>
          <p>{{ analysis.reusable }}</p>
        </div>

        <div class="an-block" v-if="analysis.remake && analysis.remake.length">
          <div class="an-label">翻拍建议</div>
          <ol><li v-for="(s, i) in analysis.remake" :key="i">{{ s }}</li></ol>
        </div>

        <div class="an-block" v-if="analysis.risks">
          <div class="an-label">风险</div>
          <p class="risk">{{ analysis.risks }}</p>
        </div>

        <div class="drawer-foot">
          <el-button size="small" @click="openOriginal(currentItem)">看原帖</el-button>
          <el-button size="small" type="success" plain @click="makePlanFromCurrent">
            用它出方案
          </el-button>
          <el-button size="small" type="primary" plain
            :loading="currentItem ? isAnalyzing(currentItem) : false" @click="reAnalyze">
            重新分析
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Picture, TopRight } from '@element-plus/icons-vue'
import { http } from '../utils/request'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5409'

const domains = ref([])
const items = ref([])
const loadingItems = ref(false)
const filters = ref({ q: '', sort: 'likes' })
const lastCapture = ref('')

const form = ref({
  mode: 'domain',
  platform: 'xiaohongshu',
  domains: ['旅行'],
  custom: '',
  limit: 20,
  use_mc: false
})

const running = ref(false)
const taskProgress = ref(0)
const taskStatus = ref('')
const taskMsg = ref('')
const taskLogs = ref([])
let timer = null

const agent = ref({ available: false, cli: '', model: '', node: '' })
const agentCfg = ref({ agent_model: '', agent_timeout: 240 })
const agentOn = ref(true)
const probing = ref(false)
const probeResult = ref('')

const drawer = ref(false)
const analysis = ref(null)
const analysisError = ref('')
const analysisMs = ref(0)
const analyzingId = ref('')
const currentItem = ref(null)
// 同时进行中的分析（每条一个独立 agent 任务，互不阻塞）
const analyzingKeys = ref(new Set())
const MAX_CONCURRENT = 3
// 已拿到的结果按条目缓存：切走再切回来不用重新烧推理，也避免并发结果互相覆盖
const analysisByKey = ref({})

function isAnalyzing(it) { return analyzingKeys.value.has(keyOf(it)) }
function setAnalyzing(key, on) {
  const s = new Set(analyzingKeys.value)
  on ? s.add(key) : s.delete(key)
  analyzingKeys.value = s
  analyzingId.value = s.size ? Array.from(s)[0] : ''
}

const coverOk = computed(() => items.value.filter(i => i.cover_file).length)

// ---- 登录态（复用账号管理）----
const router = useRouter()
const authInfo = ref(null)
const authOk = computed(() => {
  const r = authInfo.value?.resolved
  return !!r && r.mode !== 'none'
})
const authText = computed(() => {
  if (!form.value.use_mc) return ''
  const r = authInfo.value?.resolved
  if (!r) return '读取中…'
  if (r.mode === 'none') return r.error || `未找到可用登录态，请先到「账号管理」登录`
  if (r.mode === 'cookie') return `沿用本地已保存的小红书 cookie`
  const who = r.name ? `「${r.name}」` : '已登录账号'
  const when = r.logged_at ? `（${r.logged_at} 登录）` : ''
  return `复用「账号管理」里的${who}${when}，抓取时不需再登录`
})
async function loadAuth() {
  authInfo.value = null
  try {
    const res = await http.get('/hotCapture/accounts', { platform: form.value.platform })
    authInfo.value = res.data || null
  } catch (e) {
    authInfo.value = { resolved: { mode: 'none', error: '读取登录态失败' } }
  }
}
function goAccounts() { router.push('/account-management') }
const analyzedCount = computed(() => items.value.filter(i => i.analyzed).length)
const drawerTitle = computed(() => currentItem.value ? currentItem.value.title || '爆款拆解' : '爆款拆解')
const engineText = computed(() => {
  const e = currentItem.value?.engine
  return e === 'agent' ? 'WorkBuddy agent' : '规则引擎（agent 不可用）'
})
const engineTag = computed(() => (currentItem.value?.engine === 'agent' ? 'success' : 'warning'))
const scoreColor = computed(() => {
  const s = Number(analysis.value?.score || 0)
  return s >= 70 ? '#f56c6c' : s >= 50 ? '#e6a23c' : '#909399'
})

function keyOf(it) { return it.platform + ':' + it.id }
function fmt(n) {
  n = Number(n || 0)
  if (n >= 10000) return (n / 10000).toFixed(1) + '万'
  return String(n)
}
function fmtTime(s) {
  if (!s) return ''
  // 2026-11-17T10:24:45+08:00 -> 11-17 10:24
  const m = String(s).match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  return m ? `${m[1]}-${m[2]} ${m[3]}:${m[4]}` : s
}
function coverSrc(it) {
  if (!it.cover_url) return ''
  return it.cover_url.startsWith('http') ? it.cover_url : API_BASE + it.cover_url
}
function openOriginal(it) {
  if (!it || !it.url) return ElMessage.warning('这条没有原始链接')
  window.open(it.url, '_blank', 'noopener')
}

async function loadDomains() {
  try {
    const res = await http.get('/hotCapture/domains')
    domains.value = res.data || []
  } catch (e) { /* 拦截器已提示 */ }
}

async function loadItems() {
  loadingItems.value = true
  try {
    const res = await http.get('/hotCapture/items', {
      q: filters.value.q, sort: filters.value.sort, limit: 200
    })
    const d = res.data || {}
    items.value = d.items || []
    lastCapture.value = d.captured_at || ''
  } catch (e) {
    items.value = []
  } finally {
    loadingItems.value = false
  }
}

function refreshItems() { loadItems() }

function onModeChange() {
  if (form.value.mode === 'hot') form.value.use_mc = true
}

function onUseMcChange(v) {
  if (v && !authInfo.value) loadAuth()
}

async function startCapture() {
  if (form.value.mode === 'domain' && !form.value.domains.length && !form.value.custom.trim()) {
    return ElMessage.warning('请至少选一个领域，或填一个补充词')
  }
  if (form.value.use_mc && authInfo.value?.resolved?.mode === 'none') {
    return ElMessage.error(authInfo.value.resolved.error || '没有可用登录态，请先到「账号管理」页登录')
  }
  running.value = true
  taskProgress.value = 2
  taskStatus.value = 'running'
  taskLogs.value = []
  taskMsg.value = '任务已提交…'
  try {
    const res = await http.post('/hotCapture/capture', { ...form.value })
    const task = res.data
    taskMsg.value = '任务运行中…'
    pollTask(task.id)
  } catch (e) {
    running.value = false
    taskStatus.value = 'error'
    taskMsg.value = ''
  }
}

function pollTask(id) {
  clearInterval(timer)
  timer = setInterval(async () => {
    try {
      const res = await http.get(`/hotCapture/task/${id}`)
      const t = res.data || {}
      taskProgress.value = t.progress || 0
      taskStatus.value = t.status
      taskLogs.value = t.logs || []
      taskMsg.value = t.message || ''
      if (t.status === 'done' || t.status === 'error') {
        clearInterval(timer)
        running.value = false
        if (t.status === 'done') {
          ElMessage.success(t.message || '抓取完成')
          loadItems()
        } else {
          ElMessage.error(t.message || '抓取失败')
        }
      }
    } catch (e) {
      clearInterval(timer)
      running.value = false
    }
  }, 2000)
}

// 把当前拆解结论带到创作工作台，直接生成同构方案
function makePlanFromCurrent() {
  const it = currentItem.value
  if (!it) return
  router.push({ path: '/creator', query: { item_id: String(it.id || ''), mode: 'viral' } })
}

async function analyzeItem(it, force = false) {
  const k = keyOf(it)
  currentItem.value = it
  drawer.value = true
  analysisError.value = ''
  const cached = analysisByKey.value[k]
  analysis.value = cached ? cached.analysis : null
  analysisMs.value = cached ? cached.ms : 0
  if (cached) currentItem.value = { ...it, engine: cached.engine }

  if (isAnalyzing(it)) return          // 已经在跑，别重复提交
  if (analyzingKeys.value.size >= MAX_CONCURRENT) {
    ElMessage.warning(`同时最多跑 ${MAX_CONCURRENT} 个分析，本地模型并行过多会互相拖慢，等一个完成再点`)
    return
  }

  setAnalyzing(k, true)
  try {
    const res = await http.post('/hotCapture/analyzeItem', { item: it, force })
    const d = res.data || {}
    analysisByKey.value[k] = { analysis: d.analysis, engine: d.engine, ms: d.ms }
    it.analyzed = true
    const idx = items.value.findIndex(x => keyOf(x) === k)
    if (idx >= 0) items.value[idx].analyzed = true
    // 只有抽屉当前还停在这条时才刷界面，否则会把别条的分析串到这条上
    if (currentItem.value && keyOf(currentItem.value) === k) {
      analysis.value = d.analysis || null
      analysisMs.value = d.ms || 0
      currentItem.value = { ...it, engine: d.engine }
      if (d.engine !== 'agent') {
        analysisError.value = d.error
          ? `agent 未生效（${d.agent_status}）：${d.error}，已回退规则引擎`
          : '已回退规则引擎分析'
      }
    } else if (d.engine !== 'agent') {
      ElMessage.warning(`「${(it.title || '').slice(0, 12)}」agent 未生效，已回退规则引擎`)
    }
  } catch (e) {
    if (currentItem.value && keyOf(currentItem.value) === k) {
      analysisError.value = '分析失败，请稍后重试'
    }
  } finally {
    setAnalyzing(k, false)
  }
}

function reAnalyze() {
  if (currentItem.value) analyzeItem(currentItem.value, true)
}

async function loadAgentStatus(probe = false) {
  try {
    const res = await http.get('/hotCapture/agentStatus', probe ? { probe: 1 } : {})
    agent.value = res.data || {}
  } catch (e) { /* 拦截器已提示 */ }
}

async function loadAgentCfg() {
  try {
    const res = await http.get('/hotCapture/config')
    const d = res.data || {}
    agentCfg.value.agent_model = d.agent_model || ''
    agentCfg.value.agent_timeout = Number(d.agent_timeout || 240)
    agentOn.value = String(d.agent_enabled ?? '1') !== '0'
  } catch (e) { /* 拦截器已提示 */ }
}

async function saveAgentCfg() {
  try {
    await http.post('/hotCapture/config', {
      agent_model: agentCfg.value.agent_model,
      agent_timeout: String(agentCfg.value.agent_timeout),
      agent_enabled: agentOn.value ? '1' : '0'
    })
    ElMessage.success('Agent 配置已保存')
  } catch (e) { /* 拦截器已提示 */ }
}

async function probeAgent() {
  probing.value = true
  probeResult.value = '探测中（约 10-30 秒）…'
  try {
    await loadAgentStatus(true)
    const a = agent.value
    probeResult.value = a.ok
      ? `连通正常，${(a.ms / 1000).toFixed(1)}s`
      : `不通：${a.error || a.error_kind || '未知原因'}`
    ElMessage[ a.ok ? 'success' : 'warning' ](probeResult.value)
  } catch (e) {
    probeResult.value = '探测失败'
  } finally {
    probing.value = false
  }
}

onMounted(() => {
  loadDomains()
  loadItems()
  loadAgentStatus()
  loadAgentCfg()
  loadAuth()
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped lang="scss">
.hot-workbench { padding: 8px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.title { font-size: 16px; font-weight: 600; margin-right: 12px; }
.subtitle { font-size: 12px; color: #909399; }
.cfg-card, .list-card { margin-bottom: 16px; }
.cfg-form :deep(.el-form-item) { margin-bottom: 14px; }
.opt-hint { float: right; color: #909399; font-size: 12px; margin-left: 12px; }
.hint { color: #909399; font-size: 12px; margin-left: 10px; }
.auth-text { font-size: 13px; }
.auth-text.ok { color: #67c23a; }
.auth-text.bad { color: #f56c6c; }
.task-msg { margin-left: 12px; color: #409eff; font-size: 13px; }
.task-logs { background: #fafafa; border: 1px solid #ebeef5; border-radius: 4px; padding: 8px 12px; }
.task-log { font-size: 12px; color: #606266; line-height: 1.8; }
.list-actions { display: flex; gap: 8px; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 14px; }
.item-card { border: 1px solid #ebeef5; border-radius: 8px; overflow: hidden; background: #fff;
  transition: box-shadow .2s, transform .2s; }
.item-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.08); transform: translateY(-2px); }
.cover-wrap { position: relative; cursor: pointer; background: #f5f7fa; }
.cover { width: 100%; height: 190px; display: block; }
.cover-fallback { height: 190px; display: flex; flex-direction: column; align-items: center;
  justify-content: center; color: #c0c4cc; font-size: 12px; gap: 6px; }
.cover-fallback .el-icon { font-size: 26px; }
.platform-badge { position: absolute; left: 8px; top: 8px; background: rgba(0,0,0,.55); color: #fff;
  font-size: 11px; padding: 1px 6px; border-radius: 8px; }
.analyzed-badge { position: absolute; right: 8px; top: 8px; background: #67c23a; color: #fff;
  font-size: 11px; padding: 1px 6px; border-radius: 8px; }
.analyzing-badge { position: absolute; right: 8px; top: 8px; background: #e6a23c; color: #fff;
  font-size: 11px; padding: 1px 6px; border-radius: 8px; }
.item-body { padding: 10px; }
.item-title { font-size: 13px; font-weight: 600; line-height: 1.4; height: 36px; overflow: hidden;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.item-meta { display: flex; justify-content: space-between; font-size: 11px; color: #909399; margin-top: 6px; }
.item-meta .kw { color: #409eff; }
.item-stats { display: flex; gap: 10px; font-size: 11px; color: #606266; margin: 6px 0 8px; }
.item-actions { display: flex; justify-content: space-between; align-items: center; }

.agent-card { margin-top: 16px; }
.mono { font-family: Menlo, Consolas, monospace; font-size: 12px; color: #606266; word-break: break-all; }

.drawer-loading { padding: 8px 4px; }
.an-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.score-wrap { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.score-label { font-size: 12px; color: #909399; }
.an-verdict { font-size: 15px; line-height: 1.7; margin: 4px 0 16px; padding-left: 10px;
  border-left: 3px solid #409eff; }
.an-block { margin-bottom: 14px; }
.an-label { font-size: 12px; font-weight: 600; color: #409eff; margin-bottom: 4px; }
.an-block p { margin: 0; font-size: 13px; line-height: 1.75; color: #303133; }
.an-block ul { list-style: disc; }
.an-block ol { list-style: decimal; }
.an-block ul, .an-block ol { margin: 0; padding-left: 20px; font-size: 13px; line-height: 1.8; color: #303133; }
.risk { color: #e6a23c !important; }
.drawer-foot { margin-top: 20px; display: flex; gap: 8px; justify-content: flex-end; }
</style>
