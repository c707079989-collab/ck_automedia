<template>
  <div class="creator-page">
    <!-- 环境状态 -->
    <div class="env-bar">
      <div class="env-left">
        <span class="env-title">创作工作台</span>
        <el-tag :type="env.online ? 'success' : 'danger'" size="small" effect="dark">
          ComfyUI {{ env.online ? '在线' : '未连接' }}
        </el-tag>
        <el-tag v-if="env.online" type="info" size="small">{{ env.device || 'GPU' }} · {{ env.vram_gb }}GB</el-tag>
        <el-tag :type="env.models_ok === false ? 'danger' : (env.models_ok ? 'success' : 'info')" size="small">
          权重{{ env.models_ok === false ? '缺失' : (env.models_ok ? '齐全' : '未校验') }}
        </el-tag>
        <el-tag :type="env.agent?.available ? 'success' : 'warning'" size="small">
          本地模型{{ env.agent?.available ? '可用' : '不可用' }}
        </el-tag>
        <el-tag v-if="env.queue_running || env.queue_pending" type="warning" size="small">
          出片队列 {{ env.queue_running }}/{{ env.queue_pending }}
        </el-tag>
      </div>
      <div class="env-right">
        <el-tag v-if="env.url_fallback" type="warning" size="small">
          端口已自动切到 {{ shortUrl(env.comfy_url) }}
        </el-tag>
        <span v-if="env.missing?.length" class="env-miss">缺：{{ env.missing.join('，') }}</span>
        <el-popover placement="bottom-end" :width="360" trigger="click">
          <template #reference>
            <el-button size="small" link>服务地址</el-button>
          </template>
          <div class="cfg-box">
            <div class="cfg-title">出片服务地址</div>
            <el-input v-model="cfgForm.comfy_url" size="small" placeholder="http://127.0.0.1:5008" />
            <div class="cfg-hint">当前：{{ env.comfy_url || '未连接' }}</div>
            <div class="cfg-title">候选端口（自动探测）</div>
            <el-input v-model="cfgForm.ports" size="small" placeholder="5008,7878,8188" />
            <div class="cfg-hint">地址连不上时会依次试这些端口，命中后自动记住。</div>
            <div class="cfg-actions">
              <el-button size="small" type="primary" :loading="savingCfg" @click="saveCfg">保存并自检</el-button>
            </div>
          </div>
        </el-popover>
        <el-button size="small" :loading="loadingStatus" @click="loadStatus(true)">重新自检</el-button>
      </div>
    </div>

    <el-alert v-if="env.online === false" type="warning" :closable="false" show-icon class="mb"
      title="ComfyUI 没在跑，方案可以正常生成，但出片会失败">
      <template #default>
        启动 <code>F:\ComfyUI130\run_nvidia_gpu.bat</code>（默认端口 5008，可在「服务地址」改），起来后点右上角「重新自检」。
      </template>
    </el-alert>

    <!-- 三步向导 -->
    <div class="wizard">
      <div class="wizard-head">
        <div class="steps">
          <div v-for="(s, i) in stepDefs" :key="i"
               :class="['step', { on: step === i, passed: step > i }]" @click="goStep(i)">
            <span class="step-no">{{ step > i ? '✓' : i + 1 }}</span>
            <div class="step-txt">
              <div class="step-name">{{ s.name }}</div>
              <div class="step-desc">{{ s.desc }}</div>
            </div>
          </div>
        </div>

        <!-- 出片进度常驻，不随步骤走 -->
        <div v-if="task" class="prog-chip" @click="goStep(2)">
          <el-tag size="small" :type="taskType">{{ taskLabel }}</el-tag>
          <span class="prog-txt">{{ task.done }}/{{ task.total }}</span>
          <el-progress :percentage="progressPct" :stroke-width="6" :show-text="false" class="prog-bar" />
          <span class="prog-go">查看 ›</span>
        </div>
      </div>

      <!-- ① 选题 -->
      <el-card v-show="step === 0" shadow="never" class="card">
        <template #header>
          <div class="card-head">
            <span>选一条路子开工</span>
            <span class="hint">口播类只出稿子和提词卡，不烧显卡</span>
          </div>
        </template>

        <div class="mode-row">
          <div v-for="m in modes" :key="m.value"
               :class="['mode-card', { active: form.mode === m.value }]"
               @click="form.mode = m.value">
            <div class="mode-name">{{ m.label }}</div>
            <div class="mode-desc">{{ m.desc }}</div>
          </div>
        </div>

        <!-- 爆款复刻 -->
        <div v-if="form.mode === 'viral'" class="src-block">
          <div class="src-head">
            <span>选一条已分析的爆款（{{ analyzed.length }}）</span>
            <el-button link size="small" @click="loadAnalyzed">刷新</el-button>
          </div>
          <el-empty v-if="!analyzed.length" description="还没有分析结果，先去爆款工作台点「分析」" :image-size="60" />
          <el-scrollbar v-else height="300px">
            <div v-for="a in analyzed" :key="a.key"
                 :class="['src-item', { active: form.item_id === a.item_id || form.item_id === a.key }]"
                 @click="pickSource(a)">
              <div class="src-title">{{ a.title || a.item_id }}</div>
              <div class="src-meta">
                <el-tag size="small" type="danger" effect="plain">{{ a.score }}分</el-tag>
                <span class="src-verdict">{{ a.verdict }}</span>
              </div>
            </div>
          </el-scrollbar>
        </div>

        <!-- 自创 / 口播 -->
        <div v-else class="src-block">
          <el-input v-model="form.brief" type="textarea" :rows="3"
            :placeholder="form.mode === 'talking'
              ? '想讲的观点/知识点，例如：为什么你越努力越穷，三个认知陷阱'
              : '一句话说清你想拍什么，例如：出租屋3平米厨房塞下全套厨电，预算2000' " />
          <div class="quick-topics">
            <el-tag v-for="t in presets.topics" :key="t" class="quick-tag"
                    effect="plain" @click="form.brief = t">{{ t }}</el-tag>
          </div>
        </div>

        <div class="form-row">
          <div class="form-item">
            <label>画幅</label>
            <el-select v-model="form.format" style="width: 200px">
              <el-option v-for="f in presets.formats" :key="f.value"
                         :label="`${f.label} ${f.width}x${f.height}`" :value="f.value" />
            </el-select>
          </div>
          <div class="form-item grow">
            <label>补充要求（可选）</label>
            <el-input v-model="form.extra" placeholder="例如：真人真事、不要网红风、口播要慢一点" />
          </div>
        </div>

        <div class="step-foot">
          <span v-if="making" class="making-tip">本地 27B 模型在写方案，通常 1-3 分钟，别关页面。</span>
          <span v-else class="hint">生成后自动进入第 2 步改稿；写一半也会自动存成「临时方案」，随时能还原</span>
          <el-button type="primary" size="large" :loading="making" @click="makePlan">
            {{ making ? '生成中…' : '生成方案 →' }}
          </el-button>
        </div>
      </el-card>

      <!-- ② 方案与分镜 -->
      <el-card v-show="step === 1" shadow="never" class="card">
        <template #header>
          <div class="card-head">
            <span>{{ plan ? plan.title || '未命名方案' : '方案与分镜' }}</span>
            <span class="hint" v-if="plan">
              {{ plan.shots.length }} 镜 · 约 {{ plan.duration_planned }}s
              <template v-if="plan.engine === 'fallback'">（模型没给结构化结果，这是兜底骨架）</template>
              <template v-if="dirty"> · <b class="dirty">有未保存修改</b></template>
            </span>
          </div>
        </template>

        <el-empty v-if="!plan" description="还没有方案，回第 1 步生成，或从右侧「我的方案」打开一份" :image-size="70">
          <el-button @click="goStep(0)">回选题</el-button>
        </el-empty>

        <template v-else>
          <!-- 爆款复刻：把这条方案的「出处」摆出来，能对照、能点回原片 -->
          <div v-if="plan.source?.type === 'viral'" class="viral-ref">
            <div class="vr-head">
              <span class="vr-tag">参考爆款</span>
              <el-tag v-if="srcA.score" size="small" type="danger" effect="dark">{{ srcA.score }}分</el-tag>
              <span :class="['vr-title', { link: !!plan.source.url }]" @click="openSourceUrl">
                {{ plan.source.title || '（这条没记录原片标题）' }}
              </span>
              <span class="spacer" />
              <el-button link size="small" @click="viralOpen = !viralOpen">
                {{ viralOpen ? '收起原片拆解' : '展开原片拆解' }}
              </el-button>
            </div>

            <div class="vr-diff">
              <div class="vr-col">
                <label>原片钩子</label>
                <p>{{ srcA.hook || '—' }}</p>
              </div>
              <div class="vr-col mine">
                <label>本片钩子</label>
                <p>{{ plan.hook || '（还没写，可在下面补）' }}</p>
              </div>
            </div>

            <div class="vr-diff">
              <div class="vr-col">
                <label>可复用套路</label>
                <p>{{ srcA.reusable || '—' }}</p>
              </div>
              <div class="vr-col">
                <label>做的时候要避开</label>
                <p>{{ srcA.risks || '—' }}</p>
              </div>
            </div>

            <div v-show="viralOpen" class="vr-detail">
              <div v-if="srcA.structure?.length" class="vr-block">
                <label>原片结构</label>
                <ol><li v-for="(x, i) in srcA.structure" :key="'st' + i">{{ x }}</li></ol>
              </div>
              <div v-if="srcA.remake?.length" class="vr-block">
                <label>原片翻拍建议</label>
                <ul><li v-for="(x, i) in srcA.remake" :key="'rm' + i">{{ x }}</li></ul>
              </div>
              <div v-if="srcA.why_viral?.length" class="vr-block">
                <label>为什么爆</label>
                <ul><li v-for="(x, i) in srcA.why_viral" :key="'wv' + i">{{ x }}</li></ul>
              </div>
              <div v-if="srcA.emotion" class="vr-block">
                <label>情绪落点</label><p>{{ srcA.emotion }}</p>
              </div>
              <div v-if="srcA.title_formula" class="vr-block">
                <label>标题公式</label><p>{{ srcA.title_formula }}</p>
              </div>
              <div v-if="srcA.verdict" class="vr-block">
                <label>爆款判定</label><p>{{ srcA.verdict }}</p>
              </div>
              <div v-if="!srcA.hook" class="vr-block">
                <label>提示</label>
                <p>这条方案生成时没把原片拆解一起存下来（旧版本生成的）。
                  去爆款工作台对这条重新点一次「分析」，或重新生成方案即可。</p>
              </div>
            </div>
          </div>

          <div class="plan-edit">
            <el-input v-model="plan.title" size="large" placeholder="标题" />
            <div class="row3">
              <el-input v-model="plan.genre" placeholder="类型" />
              <el-input v-model="plan.target_audience" placeholder="目标人群" />
              <el-input v-model="plan.angle" placeholder="取胜点" />
            </div>
            <el-input v-model="plan.hook" type="textarea" :rows="2" placeholder="前 3 秒钩子" />
            <el-input v-model="plan.identity" type="textarea" :rows="2" placeholder="人物与场景设定（中文，给人看）" />
            <el-input v-if="plan.mode !== 'talking'" v-model="plan.script" type="textarea" :rows="3" placeholder="整片台本" />
          </div>

          <!-- 口播稿 -->
          <div v-if="plan.mode === 'talking'" class="talk-block">
            <div class="talk-head">
              <span>口播稿（可照着念）</span>
              <el-button size="small" @click="showExport('teleprompter')">提词卡</el-button>
              <el-button size="small" @click="showExport('shotlist')">分镜表</el-button>
              <el-button size="small" @click="showExport('srt')">字幕</el-button>
            </div>
            <div class="talk-script">{{ plan.script || '（模型没给完整台本，看下面每镜的台词）' }}</div>
          </div>

          <div class="shots-head">
            <span>分镜（点一行展开编辑）</span>
            <span class="hint">单镜上限 {{ maxShot }} 秒（16G 显存实测安全值）</span>
          </div>

          <div class="shots">
            <div v-for="(s, i) in plan.shots" :key="s.idx" :class="['shot-row', { open: openShot === s.idx }]">
              <div class="shot-line" @click="toggleShot(s.idx)">
                <span class="caret">▸</span>
                <span class="shot-idx">#{{ s.idx }}</span>
                <span class="shot-sec">{{ s.seconds }}s</span>
                <span class="shot-visual">{{ s.visual || '（未写画面）' }}</span>
                <span v-if="plan.mode === 'talking' && s.voiceover" class="shot-vo">{{ s.voiceover }}</span>
                <span v-if="shotState(s.idx)" :class="['shot-state', shotState(s.idx).status]">
                  {{ stateLabel[shotState(s.idx).status] }}
                </span>
                <span v-else-if="s.prompt" class="shot-flag">已备提示词</span>
              </div>

              <div v-if="openShot === s.idx" class="shot-body" @click.stop>
                <div class="shot-body-head">
                  <span>第 {{ s.idx }} 镜</span>
                  <el-input-number v-model="s.seconds" :min="1" :max="maxShot" :step="1"
                                   size="small" controls-position="right" style="width: 96px" />
                  <span class="unit">秒</span>
                  <div class="shot-tools">
                    <el-button link size="small" :disabled="i === 0" @click="moveShot(i, -1)">上移</el-button>
                    <el-button link size="small" :disabled="i === plan.shots.length - 1" @click="moveShot(i, 1)">下移</el-button>
                    <el-button link type="danger" size="small" @click="removeShot(i)">删除</el-button>
                  </div>
                </div>
                <el-input v-model="s.visual" type="textarea" :rows="2" placeholder="画面（中文，给人看）" />
                <div class="row3">
                  <el-input v-model="s.subtitle" placeholder="字幕" />
                  <el-input v-model="s.voiceover" placeholder="口播/对白" />
                  <el-input v-model="s.camera" placeholder="运镜" />
                </div>
                <el-alert v-if="plan.mode === 'talking' && !s.voiceover" type="warning" :closable="false" show-icon
                  title="这一镜没写台词，出片时模型会自己编音轨（听感就是口胡）" class="vo-warn" />
                <el-collapse>
                  <el-collapse-item :title="`出片提示词（英文）${s.prompt ? '' : ' · 空'}`" :name="s.idx">
                    <el-input v-model="s.prompt" type="textarea" :rows="3" />
                    <div class="prompt-actions">
                      <el-button size="small" @click="rebuildPrompt(s)">套回统一人物设定</el-button>
                      <span class="hint">改这句直接影响出片；身份段统一才能保证跨镜是同一张脸</span>
                    </div>
                    <el-alert v-if="plan.mode === 'talking'" type="info" :closable="false" class="vo-tip"
                      title="台词必须写在提示词里，格式：... says in Mandarin Chinese: &quot;该镜原话&quot;" />
                  </el-collapse-item>
                </el-collapse>
              </div>
            </div>
          </div>

          <div class="add-shot">
            <el-button size="small" @click="addShot">＋ 加一镜</el-button>
          </div>
        </template>

        <div class="step-foot">
          <el-button @click="goStep(0)">← 上一步</el-button>
          <span class="spacer" />
          <el-button :disabled="!plan" :loading="saving" @click="savePlan(false)">保存修改</el-button>
          <el-button type="primary" :disabled="!plan" @click="toRenderStep">
            下一步：出片与导出 →
          </el-button>
        </div>
      </el-card>

      <!-- ③ 出片与导出 -->
      <el-card v-show="step === 2" shadow="never" class="card">
        <template #header>
          <div class="card-head">
            <span>{{ plan ? (plan.title || '未命名方案') : '出片与导出' }}</span>
            <el-tag v-if="task" size="small" :type="taskType">{{ taskLabel }}</el-tag>
          </div>
        </template>

        <el-empty v-if="!plan" description="先在第 2 步准备好方案" :image-size="70">
          <el-button @click="goStep(plan ? 1 : 0)">{{ plan ? '去方案' : '回选题' }}</el-button>
        </el-empty>

        <template v-else>
          <div class="render-bar">
            <template v-if="plan.mode !== 'talking'">
              <el-button type="primary" @click="render" :loading="rendering" :disabled="taskRunning">
                {{ taskRunning ? '出片中…' : (plan.final_video || task?.status === 'done'
                  ? `重新出片（${plan.shots.length} 段）` : `开始出片（${plan.shots.length} 段）`) }}
              </el-button>
              <span class="hint">预计 {{ Math.round(plan.shots.length * 6) }} 分钟（每段实测 5-7 分钟）</span>
            </template>
            <template v-else>
              <el-button @click="renderAnyway" :disabled="taskRunning" :loading="rendering">
                仍要 AI 画面（慢，可选）
              </el-button>
              <span class="hint">口播方案正常只需要稿子，AI 画面会占显卡数十分钟</span>
            </template>
            <span class="spacer" />
            <el-button size="small" @click="showExport('teleprompter')" v-if="plan.mode === 'talking'">提词卡</el-button>
            <el-button size="small" @click="showExport('shotlist')">分镜表</el-button>
            <el-button size="small" @click="showExport('srt')">字幕 SRT</el-button>
          </div>

          <template v-if="task">
            <el-progress :percentage="progressPct" :status="task.status === 'failed' ? 'exception' : undefined" class="mb" />
            <div class="task-shots">
              <div v-for="t in task.shots" :key="t.idx" class="task-shot">
                <span class="shot-idx">#{{ t.idx }}</span>
                <span :class="['shot-state', t.status]">{{ stateLabel[t.status] || t.status }}</span>
                <span v-if="t.seconds_spent" class="hint">{{ t.seconds_spent }}s</span>
                <span v-if="t.error" class="err">{{ t.error }}</span>
              </div>
            </div>
            <div v-if="finalName" class="final-block">
              <video :src="finalUrl" controls class="final-video" />
              <div class="final-meta">
                <el-button size="small" type="primary" @click="openVideo">在新窗口打开</el-button>
                <span class="hint">{{ finalName }}</span>
              </div>
            </div>
            <el-collapse class="log-collapse">
              <el-collapse-item title="运行日志" name="log">
                <pre class="log">{{ (task.log || []).join('\n') }}</pre>
              </el-collapse-item>
            </el-collapse>
          </template>
          <el-empty v-else description="还没出片" :image-size="60" />

          <!-- 没有任务记录时，用方案里回写的分段状态兜底 -->
          <div v-if="!task" class="seg-list">
            <div class="seg-title">分段状态</div>
            <div class="seg-row" v-for="s in plan.shots" :key="s.idx">
              <span class="shot-idx">#{{ s.idx }}</span>
              <span class="hint">{{ s.seconds }}s</span>
              <span class="seg-visual">{{ s.visual || '（未写画面）' }}</span>
              <span :class="['shot-state', segStatus(s.idx)]">{{ stateLabel[segStatus(s.idx)] || segStatus(s.idx) }}</span>
            </div>
          </div>
        </template>

        <div class="step-foot">
          <el-button @click="goStep(1)">← 回方案</el-button>
          <span class="spacer" />
          <el-button :loading="saving" :disabled="!plan" @click="savePlan(false)">保存修改</el-button>
        </div>
      </el-card>
    </div>

    <!-- 浮动按钮：我的方案 -->
    <div class="float-plans" @click="planDrawer = true">
      <span class="fp-text">我的方案</span>
      <span class="fp-badge">{{ plans.length }}</span>
    </div>

    <!-- 右侧小窗 -->
    <el-drawer v-model="planDrawer" direction="rtl" size="400px" class="plan-drawer">
      <template #header>
        <div class="dw-head">
          <span>我的方案</span>
          <el-button link size="small" @click="loadPlans">刷新</el-button>
        </div>
      </template>
      <el-button class="dw-new" @click="newPlan">＋ 新建方案</el-button>

      <!-- 临时方案：选题阶段写了一半的表单 -->
      <template v-if="drafts.length">
        <div class="dw-sec">临时方案（写了一半，点着继续）</div>
        <div v-for="d in drafts" :key="d.id" :class="['plan-item', 'draft', { active: draftId === d.id }]"
             @click="restoreDraft(d)">
          <div class="plan-item-title">{{ draftLabel(d) }}</div>
          <div class="plan-item-meta">
            <el-tag size="small" type="info" effect="plain">临时</el-tag>
            <el-tag size="small" effect="plain">{{ modeLabel[d.mode] || d.mode }}</el-tag>
            <span v-if="d.extra">· {{ d.extra.slice(0, 12) }}</span>
          </div>
          <div class="plan-item-time">{{ (d.updated_at || '').replace('T', ' ').slice(5, 16) }} 编辑过</div>
          <el-button link type="danger" size="small" class="plan-del"
                     @click.stop="deleteDraftItem(d.id)">删除</el-button>
        </div>
      </template>

      <div class="dw-sec">我的方案</div>
      <el-empty v-if="!plans.length" description="还没有方案" :image-size="60" />
      <div v-for="p in plans" :key="p.id" :class="['plan-item', { active: plan?.id === p.id }]"
           @click="pickPlan(p)">
        <div class="plan-item-title">{{ p.title || '未命名方案' }}</div>
        <div class="plan-item-meta">
          <el-tag size="small" effect="plain">{{ modeLabel[p.mode] || p.mode }}</el-tag>
          <span>{{ p.shots }}镜 {{ p.duration_planned }}s</span>
          <el-tag v-if="p.render_status === 'running'" size="small" type="warning">
            出片中 {{ p.render_done }}/{{ p.render_total }}
          </el-tag>
          <el-tag v-else-if="p.render_status === 'finalizing'" size="small" type="warning">拼接中</el-tag>
          <el-tag v-else-if="p.render_status === 'partial'" size="small" type="warning">
            未完成 {{ p.render_done }}/{{ p.render_total }}
          </el-tag>
          <el-tag v-else-if="p.render_status === 'failed'" size="small" type="danger">出片失败</el-tag>
          <el-tag v-if="p.has_video" size="small" type="success">已成片</el-tag>
        </div>
        <div class="plan-item-time">{{ (p.created_at || '').replace('T', ' ').slice(0, 16) }}</div>
        <el-button link type="danger" size="small" class="plan-del" @click.stop="removePlan(p.id)">删除</el-button>
      </div>
    </el-drawer>

    <!-- 导出弹窗 -->
    <el-dialog v-model="exportDlg.visible" :title="exportDlg.title" width="720px">
      <pre class="export-text">{{ exportDlg.text }}</pre>
      <template #footer>
        <el-button @click="copyText(exportDlg.text)">复制</el-button>
        <el-button type="primary" @click="downloadText">下载</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { creatorApi } from '@/api/creator'

const route = useRoute()

const env = ref({})
const presets = ref({ formats: [], topics: [], modes: [] })
const analyzed = ref([])
const plans = ref([])
const drafts = ref([])
const draftId = ref('')
const plan = ref(null)
const task = ref(null)

const step = ref(0)
const stepDefs = [
  { name: '选题', desc: '选模式与来源' },
  { name: '方案与分镜', desc: '改稿、调镜' },
  { name: '出片与导出', desc: '生成、验收' }
]
const openShot = ref(1)
const planDrawer = ref(false)
const savedSnapshot = ref('')

const loadingStatus = ref(false)
const making = ref(false)
const saving = ref(false)
const rendering = ref(false)
const maxShot = ref(5)

const form = reactive({ mode: 'original', brief: '', item_id: '', extra: '', format: 'portrait', source_title: '' })
const cfgForm = reactive({ comfy_url: '', ports: '' })
const savingCfg = ref(false)

const stateLabel = {
  pending: '待生成', queued: '排队中', running: '生成中', done: '已完成', failed: '失败'
}
const modeLabel = { viral: '爆款复刻', original: '完全自创', talking: '口播' }

const modes = computed(() => presets.value.modes?.length ? presets.value.modes : [
  { value: 'viral', label: '爆款复刻', desc: '从已分析的爆款拆解推同构新片' },
  { value: 'original', label: '完全自创', desc: '给一个想法，直接出方案' },
  { value: 'talking', label: '口播类', desc: '出口播稿+提词卡+分镜' }
])

let timer = null

const taskRunning = computed(() =>
  !!task.value && (task.value.status === 'running' || task.value.status === 'finalizing'))
const progressPct = computed(() => {
  if (!task.value) return 0
  const t = task.value
  if (t.status === 'done' || t.status === 'finalizing') return 100
  return Math.round((t.done / Math.max(1, t.total)) * 100)
})
const taskLabel = computed(() => ({
  running: '进行中', finalizing: '拼接中', done: '已完成', partial: '部分完成', failed: '失败'
}[task.value?.status] || task.value?.status || ''))
const taskType = computed(() => ({
  running: 'warning', finalizing: 'warning', done: 'success', partial: 'warning', failed: 'danger'
}[task.value?.status] || 'info'))
const finalName = computed(() => {
  const p = task.value?.final_path || ''
  return p ? p.split(/[\\/]/).pop() : ''
})
const finalUrl = computed(() => {
  if (!finalName.value || !plan.value) return ''
  return creatorApi.videoUrl(plan.value.id, finalName.value)
})

// 爆款复刻：方案里存的原始拆解
const viralOpen = ref(false)
const srcA = computed(() => plan.value?.source?.analysis || {})

function openSourceUrl() {
  const u = plan.value?.source?.url
  if (u) window.open(u, '_blank')
}

// 未保存检测：只比对这些真正会写回后端的字段
function snapshot(p) {
  if (!p) return ''
  return JSON.stringify({
    title: p.title, hook: p.hook, script: p.script, identity: p.identity,
    genre: p.genre, format: p.format, target_audience: p.target_audience,
    angle: p.angle, hashtags: p.hashtags, shots: p.shots
  })
}
const dirty = computed(() => !!plan.value && snapshot(plan.value) !== savedSnapshot.value)

function shotState(idx) {
  return (task.value?.shots || []).find(s => s.idx === idx)
}
function segStatus(idx) {
  const st = shotState(idx)
  if (st) return st.status
  const r = (plan.value?.rendered || []).find(x => x.idx === idx)
  return r ? r.status : 'pending'
}

function goStep(i) {
  if (i > 0 && !plan.value) {
    ElMessage.warning('第 1 步还没走完：先选好来源，点「生成方案」才能往下走')
    return
  }
  step.value = i
}

function toggleShot(idx) {
  openShot.value = openShot.value === idx ? 0 : idx
}

async function loadStatus(deep = false) {
  loadingStatus.value = true
  try {
    const r = await creatorApi.status(deep)
    env.value = r.data || {}
    if (env.value.max_shot_seconds) maxShot.value = env.value.max_shot_seconds
    if (!cfgForm.comfy_url) cfgForm.comfy_url = env.value.comfy_url || ''
    if (!cfgForm.ports) cfgForm.ports = (env.value.comfy_ports || []).join(',')
  } catch (e) { /* 拦截器已提示 */ } finally { loadingStatus.value = false }
}

function shortUrl(u) {
  return String(u || '').replace(/^https?:\/\//, '')
}

async function saveCfg() {
  savingCfg.value = true
  try {
    const ports = cfgForm.ports.split(/[,，\s]+/)
      .map(x => Number(x)).filter(x => Number.isInteger(x) && x > 0 && x < 65536)
    const payload = {}
    if (cfgForm.comfy_url.trim()) payload.comfy_url = cfgForm.comfy_url.trim()
    if (ports.length) payload.comfy_ports = ports
    await creatorApi.saveConfig(payload)
    ElMessage.success('已保存，正在重新自检')
    await loadStatus(true)
  } catch (e) {} finally { savingCfg.value = false }
}

async function loadPresets() {
  try {
    const r = await creatorApi.presets()
    presets.value = r.data || {}
  } catch (e) {}
}

async function loadAnalyzed() {
  try {
    const r = await creatorApi.analyzed()
    analyzed.value = r.data || []
  } catch (e) {}
}

async function loadPlans() {
  try {
    const r = await creatorApi.plans()
    plans.value = r.data || []
  } catch (e) {}
}

function pickSource(a) {
  form.item_id = a.item_id || a.key
  form.source_title = a.title || ''
}

// ---------------- 临时方案（草稿）：选题阶段随时落盘 ----------------
let draftTimer = null

function draftPayload() {
  return {
    id: draftId.value || '',
    mode: form.mode,
    brief: form.brief,
    item_id: form.item_id,
    extra: form.extra,
    format: form.format,
    source_title: form.source_title,
    step: step.value
  }
}

function scheduleDraftSave() {
  clearTimeout(draftTimer)
  draftTimer = setTimeout(saveDraftNow, 700)
}

async function saveDraftNow() {
  // 还没写任何东西就不存；已经生成出方案了也不用草稿
  const hasContent = !!form.brief.trim() || !!form.item_id
  if (!hasContent) return
  if (plan.value && step.value > 0) return
  try {
    const r = await creatorApi.saveDraft(draftPayload())
    const d = r.data
    if (!d?.id) return
    draftId.value = d.id
    // 本地 upsert，避免每次都拉整表
    const i = drafts.value.findIndex(x => x.id === d.id)
    if (i >= 0) drafts.value.splice(i, 1, d)
    else drafts.value.unshift(d)
  } catch (e) { /* 草稿存不上不该打断用户 */ }
}

async function deleteDraftItem(id) {
  try {
    await creatorApi.deleteDraft(id)
    drafts.value = drafts.value.filter(x => x.id !== id)
    if (draftId.value === id) draftId.value = ''
    ElMessage.success('已删除临时方案')
  } catch (e) {}
}

async function loadDrafts() {
  try {
    const r = await creatorApi.drafts()
    drafts.value = r.data || []
  } catch (e) {}
}

async function dropDraft() {
  const id = draftId.value
  draftId.value = ''
  if (!id) return
  drafts.value = drafts.value.filter(x => x.id !== id)
  try { await creatorApi.deleteDraft(id) } catch (e) {}
}

function draftLabel(d) {
  if (d.source_title) return d.source_title
  if (d.brief) return d.brief
  return modeLabel[d.mode] || '临时方案'
}

async function restoreDraft(d) {
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        `当前方案「${plan.value.title || '未命名'}」有未保存的修改，先保存再回到临时方案？`,
        '有未保存的修改', { type: 'warning', confirmButtonText: '保存并切换', cancelButtonText: '取消' })
    } catch (e) { return }
    const ok = await savePlan(true)
    if (!ok) return
  }
  stopPolling()
  form.mode = d.mode || 'original'
  form.brief = d.brief || ''
  form.item_id = d.item_id || ''
  form.extra = d.extra || ''
  form.format = d.format || 'portrait'
  form.source_title = d.source_title || ''
  draftId.value = d.id
  plan.value = null
  task.value = null
  savedSnapshot.value = ''
  step.value = 0
  planDrawer.value = false
  ElMessage.success('已还原临时方案，接着改就行')
}

function resetForm() {
  form.mode = 'original'
  form.brief = ''
  form.item_id = ''
  form.extra = ''
  form.format = 'portrait'
  form.source_title = ''
}

async function makePlan() {
  if (form.mode === 'viral' && !form.item_id) {
    ElMessage.warning('先选一条已分析的爆款')
    return
  }
  if (form.mode !== 'viral' && !form.brief.trim()) {
    ElMessage.warning('先写清楚想拍什么')
    return
  }
  if (plan.value && dirty.value) {
    try {
      await ElMessageBox.confirm('当前方案还有未保存的修改，生成新方案会丢弃它。继续？', '未保存的修改',
        { type: 'warning', confirmButtonText: '继续生成', cancelButtonText: '取消' })
    } catch (e) { return }
  }
  making.value = true
  try {
    const payload = { mode: form.mode, format: form.format, extra: form.extra, save: true }
    if (form.mode === 'viral') payload.item_id = form.item_id
    else payload.brief = form.brief
    const r = await creatorApi.makePlan(payload)
    plan.value = r.data.plan
    savedSnapshot.value = snapshot(plan.value)
    openShot.value = (plan.value.shots[0] || {}).idx || 1
    task.value = null
    step.value = 1
    dropDraft()   // 已经成方案了，临时方案没用了
    if (!r.data.ok) ElMessage.warning(r.data.error || '模型没给出完整方案，已生成兜底骨架（已存进我的方案）')
    else ElMessage.success(`方案已生成（${Math.round(r.data.ms / 1000)}s）`)
    loadPlans()
  } catch (e) {} finally { making.value = false }
}

async function openPlan(id) {
  try {
    const r = await creatorApi.getPlan(id)
    plan.value = r.data
    savedSnapshot.value = snapshot(plan.value)
    openShot.value = (plan.value.shots?.[0] || {}).idx || 1
    viralOpen.value = false
    task.value = null
    backfillViral()
    // 恢复该方案正在跑（或刚跑完）的出片任务，刷新页面不会丢进度
    try {
      const t = await creatorApi.tasks()
      const mine = (t.data || []).find(x => x.plan_id === id)
      if (mine) {
        task.value = mine
        if (mine.status === 'running' || mine.status === 'finalizing') startPolling()
        return
      }
    } catch (e) {}
    if (plan.value.final_video) {
      const name = plan.value.final_video.split(/[\\/]/).pop()
      task.value = { status: 'done', total: plan.value.shots.length, done: plan.value.shots.length,
                     shots: [], final_path: name, log: [] }
    }
  } catch (e) {}
}

// 老方案里只存了 item_id 没存原片拆解，打开时补一次（只补前端展示，不回写）
async function backfillViral() {
  const s = plan.value?.source
  if (!s || s.type !== 'viral' || s.analysis || !s.item_id) return
  try {
    const r = await creatorApi.analysis(s.item_id)
    const j = r.data || {}
    if (plan.value?.source === s) s.analysis = j.analysis || {}
    if (!s.title && j.title) s.title = j.title
    if (!s.url && j.url) s.url = j.url
  } catch (e) {}
}

// 点「我的方案」里的一条：先处理未保存，再跳详情
async function pickPlan(p) {
  if (plan.value && plan.value.id === p.id) {
    planDrawer.value = false
    toPlanDetail()
    return
  }
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        `当前方案「${plan.value.title || '未命名'}」有未保存的修改，先保存再切到「${p.title || '未命名'}」？`,
        '有未保存的修改',
        { type: 'warning', confirmButtonText: '保存并切换', cancelButtonText: '取消' }
      )
    } catch (e) { return }
    const ok = await savePlan(true)
    if (!ok) return
  }
  await openPlan(p.id)
  planDrawer.value = false
  toPlanDetail()
}

// 进方案详情：有出片记录就落到第 3 步，否则停在第 2 步改稿
function toPlanDetail() {
  if (!plan.value) return
  const hasRender = !!task.value || !!plan.value.final_video
  step.value = hasRender ? 2 : 1
}

async function newPlan() {
  if (dirty.value) {
    try {
      await ElMessageBox.confirm('当前方案有未保存的修改，要保存后再新建吗？', '未保存的修改',
        { type: 'warning', confirmButtonText: '保存并新建', cancelButtonText: '取消' })
    } catch (e) { return }
    const ok = await savePlan(true)
    if (!ok) return
  }
  // 关键：一定要把当前方案清掉。否则「新建」之后点第 2 步，
  // 看到的还是上一份方案的分镜，跟这次新建没关系。
  stopPolling()
  plan.value = null
  task.value = null
  savedSnapshot.value = ''
  openShot.value = 0
  draftId.value = ''      // 新的一次选题，旧草稿留在列表里
  resetForm()
  planDrawer.value = false
  step.value = 0
}

// 返回 true 表示已保存成功；silent=true 时不弹「已保存」，由调用方自己提示
async function savePlan(silent = false) {
  if (!plan.value?.id) return false
  saving.value = true
  try {
    const r = await creatorApi.savePlan(plan.value.id, {
      title: plan.value.title, hook: plan.value.hook, script: plan.value.script,
      identity: plan.value.identity, genre: plan.value.genre, format: plan.value.format,
      target_audience: plan.value.target_audience, angle: plan.value.angle,
      shots: plan.value.shots, hashtags: plan.value.hashtags
    })
    plan.value = r.data
    savedSnapshot.value = snapshot(plan.value)
    if (!silent) ElMessage.success('已保存')
    loadPlans()
    return true
  } catch (e) {
    return false
  } finally {
    saving.value = false
  }
}

async function toRenderStep() {
  if (plan.value && dirty.value) {
    const ok = await savePlan(true)
    if (!ok) return
  }
  step.value = 2
}

function rebuildPrompt(s) {
  const id = plan.value?.identity_en || ''
  const body = s.prompt_body || ''
  s.prompt = id ? `${id}. ${body}`.replace(/\.\s*$/, '') : body
  ElMessage.success('已套回统一人物设定')
}

function addShot() {
  if (!plan.value) return
  const last = plan.value.shots[plan.value.shots.length - 1] || {}
  const idx = (last.idx || 0) + 1
  plan.value.shots.push({
    idx, seconds: 4,
    visual: '', prompt: '', prompt_body: '', camera: '', subtitle: '', voiceover: '', audio: ''
  })
  openShot.value = idx
}

function removeShot(i) {
  plan.value.shots.splice(i, 1)
}

function moveShot(i, delta) {
  const arr = plan.value.shots
  const j = i + delta
  if (j < 0 || j >= arr.length) return
  const [x] = arr.splice(i, 1)
  arr.splice(j, 0, x)
}

async function render() {
  if (!plan.value?.id) return
  // 出片前必须落盘，否则后端读到的是旧分镜
  if (dirty.value) {
    const ok = await savePlan(true)
    if (!ok) return
  }
  rendering.value = true
  try {
    const r = await creatorApi.render(plan.value.id)
    task.value = r.data
    startPolling()
    ElMessage.success('已开始出片，可以离开页面，进度会保存')
  } catch (e) {} finally { rendering.value = false }
}

async function renderAnyway() {
  await ElMessageBox.confirm(
    '口播方案本来只需要稿子和提词卡。生成 AI 画面会占用显卡数十分钟，确定要继续？',
    '仍要出 AI 画面', { type: 'warning' }
  ).then(render).catch(() => {})
}

function startPolling() {
  stopPolling()
  timer = setInterval(pollTask, 3000)
}

function stopPolling() {
  if (timer) { clearInterval(timer); timer = null }
}

let pollTick = 0

async function pollTask() {
  if (!task.value?.id || !plan.value?.id) { stopPolling(); return }
  try {
    const r = await creatorApi.task(task.value.id)
    task.value = r.data
    // 「我的方案」跟着任务走，否则抽屉里看不到出片进度
    if (++pollTick % 4 === 0) loadPlans()
    if (!taskRunning.value) {
      stopPolling()
      if (task.value.status === 'done') ElMessage.success('成片已生成')
      else if (task.value.status === 'failed') ElMessage.error(task.value.error || '出片失败')
      else if (task.value.status === 'partial') ElMessage.warning(task.value.error || '部分镜次未完成')
      loadPlans()
      // 成片路径写在方案里，重新拉一次让回放立刻可用
      try {
        const pr = await creatorApi.getPlan(plan.value.id)
        const keep = task.value
        plan.value = pr.data
        savedSnapshot.value = snapshot(plan.value)
        task.value = keep
      } catch (e) {}
    }
  } catch (e) { stopPolling() }
}

const exportDlg = reactive({ visible: false, title: '', text: '', kind: 'teleprompter', name: '' })

async function showExport(kind) {
  if (!plan.value?.id) return
  try {
    const r = await creatorApi.exportText(plan.value.id, kind)
    exportDlg.kind = kind
    exportDlg.title = { teleprompter: '口播提词卡', shotlist: '分镜表', srt: '字幕（SRT）' }[kind]
    exportDlg.text = r.data.text || ''
    exportDlg.name = `${plan.value.title || 'plan'}_${kind}.${kind === 'srt' ? 'srt' : 'md'}`
    exportDlg.visible = true
  } catch (e) {}
}

async function copyText(t) {
  try {
    await navigator.clipboard.writeText(t)
    ElMessage.success('已复制')
  } catch (e) { ElMessage.warning('浏览器拒绝剪贴板，请手动全选复制') }
}

function downloadText() {
  const blob = new Blob([exportDlg.text], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = exportDlg.name
  a.click()
  URL.revokeObjectURL(a.href)
}

async function removePlan(id) {
  try {
    await ElMessageBox.confirm('删除这份方案？分段视频也会一起删掉。', '确认删除', { type: 'warning' })
    await creatorApi.deletePlan(id)
    if (plan.value?.id === id) {
      plan.value = null
      task.value = null
      savedSnapshot.value = ''
      step.value = 0
    }
    ElMessage.success('已删除')
    loadPlans()
  } catch (e) {}
}

function openVideo() {
  if (finalUrl.value) window.open(finalUrl.value, '_blank')
}

// 选题表单一动就攒草稿（防抖 700ms），写一半跑掉也能还原
watch(() => [form.mode, form.brief, form.item_id, form.extra, form.format], scheduleDraftSave)
watch(step, scheduleDraftSave)

onMounted(async () => {
  // 从爆款工作台「用它出方案」跳过来时，自动切到爆款复刻并选中那条
  if (route.query.item_id) {
    form.mode = 'viral'
    form.item_id = String(route.query.item_id)
  } else if (route.query.mode) {
    form.mode = String(route.query.mode)
  }
  loadStatus(true)
  loadPresets()
  loadAnalyzed()
  loadPlans()
  loadDrafts()
  // 有正在跑的出片任务就直接接管，刷新页面不丢进度
  try {
    const t = await creatorApi.tasks()
    const running = (t.data || []).find(x => x.status === 'running' || x.status === 'finalizing')
    if (running) {
      await openPlan(running.plan_id)
      step.value = 2
    }
  } catch (e) {}
})
onUnmounted(() => {
  clearTimeout(draftTimer)
  stopPolling()
})
</script>

<style lang="scss" scoped>
.creator-page { padding: 0 0 40px; }

.env-bar {
  display: flex; justify-content: space-between; align-items: center;
  background: #fff; border-radius: 6px; padding: 10px 16px; margin-bottom: 12px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.06);
  .env-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .env-title { font-weight: 600; font-size: 15px; margin-right: 6px; }
  .env-right { display: flex; align-items: center; gap: 10px; }
  .env-miss { color: #e6a23c; font-size: 12px; }
}
.mb { margin-bottom: 12px; }
.cfg-box {
  display: flex; flex-direction: column; gap: 6px;
  .cfg-title { font-size: 12px; font-weight: 600; color: #303133; }
  .cfg-hint { font-size: 11px; color: #909399; line-height: 1.5; }
  .cfg-actions { margin-top: 4px; text-align: right; }
}

/* 向导 */
.wizard-head { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.steps { display: flex; gap: 8px; flex: 1; min-width: 0; }
.step {
  display: flex; align-items: center; gap: 10px; flex: 1;
  background: #fff; border: 1px solid #ebeef5; border-radius: 6px;
  padding: 10px 14px; cursor: pointer; transition: all .2s;
  &:hover { border-color: #c0c4cc; }
  &.on { border-color: #409eff; background: #ecf5ff; }
  &.passed { background: #f7fbf6; }
  .step-no {
    flex: none; width: 24px; height: 24px; line-height: 24px; text-align: center;
    border-radius: 50%; background: #f0f2f5; color: #909399; font-size: 13px; font-weight: 600;
  }
  &.on .step-no { background: #409eff; color: #fff; }
  &.passed .step-no { background: #67c23a; color: #fff; }
  .step-name { font-weight: 600; font-size: 14px; color: #303133; }
  .step-desc { font-size: 12px; color: #909399; margin-top: 2px; }
  &.on .step-name { color: #409eff; }
}
.prog-chip {
  flex: none; display: flex; align-items: center; gap: 8px;
  background: #fff; border: 1px solid #ebeef5; border-radius: 6px;
  padding: 8px 12px; cursor: pointer;
  &:hover { border-color: #409eff; }
  .prog-txt { font-size: 12px; color: #606266; font-variant-numeric: tabular-nums; }
  .prog-bar { width: 80px; }
  .prog-go { font-size: 12px; color: #409eff; }
}

.card { margin-bottom: 12px; }
.wizard :deep(.el-card__body) { min-height: 320px; }
.card-head {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  font-weight: 600;
  .hint { font-weight: 400; font-size: 12px; color: #909399; }
  .dirty { color: #e6a23c; }
}
.hint { font-size: 12px; color: #909399; }
.err { color: #f56c6c; font-size: 12px; }
.spacer { flex: 1; }
.unit { font-size: 12px; color: #909399; }

.mode-row { display: flex; gap: 10px; margin-bottom: 14px; }
.mode-card {
  flex: 1; border: 1px solid #e4e7ed; border-radius: 6px; padding: 12px;
  cursor: pointer; transition: all .2s; background: #fafafa;
  &:hover { border-color: #c0c4cc; }
  &.active { border-color: #409eff; background: #ecf5ff; }
  .mode-name { font-weight: 600; margin-bottom: 4px; }
  .mode-desc { font-size: 12px; color: #909399; line-height: 1.5; }
}

.src-block { margin-bottom: 14px; }
.src-head {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 13px; color: #606266; margin-bottom: 8px;
}
.src-item {
  border: 1px solid #ebeef5; border-radius: 6px; padding: 10px; margin: 0 8px 8px 0;
  cursor: pointer;
  &:hover { background: #f5f7fa; }
  &.active { border-color: #409eff; background: #ecf5ff; }
  .src-title { font-size: 13px; line-height: 1.5; margin-bottom: 6px; }
  .src-meta { display: flex; gap: 8px; align-items: center; }
  .src-verdict { font-size: 12px; color: #909399; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
}
.quick-topics { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.quick-tag { cursor: pointer; }

.form-row { display: flex; gap: 12px; align-items: flex-end; }
.form-item {
  display: flex; flex-direction: column; gap: 6px;
  label { font-size: 12px; color: #606266; }
  &.grow { flex: 1; }
}
.making-tip { font-size: 12px; color: #e6a23c; }

.step-foot {
  margin-top: 16px; padding-top: 14px; border-top: 1px solid #f0f0f0;
  display: flex; align-items: center; gap: 10px;
}

.plan-edit { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }
.row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

.talk-block { background: #fdf6ec; border-radius: 6px; padding: 12px; margin-bottom: 14px; }
.talk-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-weight: 600; font-size: 13px; }
.talk-script { white-space: pre-wrap; line-height: 1.9; font-size: 14px; color: #303133; }

.shots-head {
  display: flex; justify-content: space-between; align-items: baseline;
  font-weight: 600; margin-bottom: 8px;
}

/* 分镜：一行一镜，展开才编辑 */
.shots { display: flex; flex-direction: column; gap: 6px; }
.shot-row {
  border: 1px solid #ebeef5; border-radius: 6px; overflow: hidden;
  &.open { border-color: #c6e2ff; background: #fbfdff; }
}
.shot-line {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px;
  cursor: pointer; font-size: 13px;
  &:hover { background: #f5f7fa; }
  .caret { color: #c0c4cc; font-size: 11px; transition: transform .2s; }
  .shot-sec { color: #909399; font-size: 12px; font-variant-numeric: tabular-nums; flex: none; }
  .shot-visual {
    flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #303133;
  }
  .shot-vo {
    flex: none; max-width: 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    color: #e6a23c; font-size: 12px;
  }
  .shot-flag { flex: none; font-size: 11px; color: #b1b3b8; }
}
.shot-row.open .shot-line {
  background: #f0f7ff;
  .caret { transform: rotate(90deg); color: #409eff; }
}
.shot-idx { font-weight: 600; color: #409eff; flex: none; }
.shot-body {
  padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 8px;
  border-top: 1px dashed #ebeef5;
}
.shot-body-head {
  display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600;
  .shot-tools { margin-left: auto; display: flex; gap: 2px; }
}
.shot-state {
  flex: none; font-size: 12px; padding: 1px 6px; border-radius: 3px; background: #f4f4f5; color: #909399;
  &.running { background: #fdf6ec; color: #e6a23c; }
  &.done { background: #f0f9eb; color: #67c23a; }
  &.failed { background: #fef0f0; color: #f56c6c; }
  &.queued, &.pending { background: #f4f4f5; color: #909399; }
}
.prompt-actions { margin-top: 8px; display: flex; gap: 10px; align-items: center; }
.vo-warn { margin-top: 2px; }
.vo-tip { margin-top: 8px; }
.add-shot { margin-top: 8px; }

/* 爆款复刻：原片拆解对照 */
.viral-ref {
  background: #fff8f0; border: 1px solid #fbe3c8; border-radius: 6px;
  padding: 12px 14px; margin-bottom: 14px;
}
.vr-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.vr-tag {
  font-size: 12px; font-weight: 600; color: #b88230;
  background: #fdf0de; border-radius: 3px; padding: 1px 6px;
}
.vr-title {
  font-size: 13px; color: #303133; max-width: 52%;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  &.link { color: #409eff; cursor: pointer; &:hover { text-decoration: underline; } }
}
.vr-diff {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 8px;
  .vr-col {
    background: #fff; border-radius: 4px; padding: 8px 10px;
    label { font-size: 11px; color: #909399; }
    p { font-size: 13px; line-height: 1.7; color: #303133; margin: 4px 0 0; }
    &.mine { background: #f0f9eb; label { color: #67c23a; } }
  }
}
.vr-detail { border-top: 1px dashed #f2dcc2; padding-top: 10px; margin-top: 4px; }
.vr-block {
  margin-bottom: 10px;
  label { font-size: 11px; color: #b88230; font-weight: 600; }
  p { font-size: 13px; line-height: 1.7; color: #303133; margin: 4px 0 0; }
  ol, ul { margin: 4px 0 0; padding-left: 20px; }
  li { font-size: 13px; line-height: 1.8; color: #303133; }
}

.render-bar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding-bottom: 14px; border-bottom: 1px solid #f0f0f0; margin-bottom: 14px;
}
.task-shots { margin: 12px 0; display: flex; flex-direction: column; gap: 6px; }
.task-shot { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.final-block { margin-top: 12px; }
.final-video { width: 300px; border-radius: 6px; background: #000; }
.final-meta { margin-top: 8px; display: flex; gap: 10px; align-items: center; }
.log-collapse { margin-top: 12px; }
.log {
  font-size: 12px; color: #606266; background: #fafafa; padding: 10px;
  border-radius: 4px; max-height: 220px; overflow: auto; white-space: pre-wrap;
}

.seg-list { margin-top: 18px; padding-top: 14px; border-top: 1px solid #f0f0f0; }
.seg-title { font-weight: 600; font-size: 13px; margin-bottom: 8px; }
.seg-row {
  display: flex; align-items: center; gap: 10px; font-size: 13px; padding: 5px 0;
  border-bottom: 1px dashed #f5f5f5;
  .seg-visual {
    flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #606266;
  }
}

/* 浮动按钮 + 右侧小窗 */
.float-plans {
  position: fixed; right: 0; top: 40%; z-index: 2000;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
  min-height: 140px; min-width: 34px;
  background: #409eff; color: #fff; padding: 12px 9px;
  border-radius: 8px 0 0 8px; cursor: pointer;
  box-shadow: -2px 2px 10px rgba(64, 158, 255, .35);
  transition: background .2s, padding .2s;
  &:hover { background: #337ecc; padding-right: 12px; }
  .fp-text { writing-mode: vertical-rl; letter-spacing: 1px; font-size: 13px; line-height: 1; }
  .fp-badge {
    background: #fff; color: #409eff; font-size: 11px; font-weight: 600;
    border-radius: 8px; padding: 0 5px; line-height: 16px; min-width: 16px; text-align: center;
  }
}
.dw-head { display: flex; align-items: center; justify-content: space-between; font-weight: 600; }
.dw-new { width: 100%; margin-bottom: 12px; }
.dw-sec {
  font-size: 12px; color: #909399; font-weight: 600;
  margin: 4px 0 8px; padding-left: 2px;
}
.plan-drawer :deep(.el-drawer__body) { padding-top: 8px; }

.plan-item {
  position: relative; border: 1px solid #ebeef5; border-radius: 6px;
  padding: 10px; margin-bottom: 8px; cursor: pointer;
  &:hover { background: #f5f7fa; .plan-del { display: block; } }
  &.active { border-color: #409eff; background: #ecf5ff; }
  .plan-item-title { font-size: 13px; line-height: 1.5; margin-bottom: 6px; padding-right: 40px; }
  .plan-item-meta { display: flex; gap: 6px; align-items: center; font-size: 12px; color: #909399; flex-wrap: wrap; }
  .plan-item-time { font-size: 11px; color: #c0c4cc; margin-top: 4px; }
  .plan-del { display: none; position: absolute; top: 8px; right: 6px; }
  &.draft { border-style: dashed; background: #fcfcfd; }
}

.export-text {
  max-height: 60vh; overflow: auto; background: #fafafa; padding: 14px;
  border-radius: 6px; font-size: 13px; line-height: 1.8; white-space: pre-wrap;
}
</style>
