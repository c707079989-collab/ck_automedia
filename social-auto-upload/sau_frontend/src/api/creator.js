import { http } from '@/utils/request'

// 创作工作台 API
export const creatorApi = {
  // 环境自检（ComfyUI / 权重 / ffmpeg / agent）
  status: (deep = true) => http.get(`/creator/status${deep ? '?deep=1' : ''}`),

  // 可选画幅、模式、选题快捷词
  presets: () => http.get('/creator/presets'),

  // 出片服务连接配置（端口可改）
  getConfig: () => http.get('/creator/config'),
  saveConfig: (data) => http.post('/creator/config', data),

  // 已做过爆款分析的条目（爆款复刻的输入源）
  analyzed: () => http.get('/creator/analyzed'),
  // 单条爆款的完整拆解（老方案没存原片拆解时用来补）
  analysis: (itemId) => http.get(`/creator/analysis/${encodeURIComponent(itemId)}`),

  // 生成方案：{ mode, brief?, item_id?, extra?, format?, save? }
  makePlan: (data) => http.post('/creator/plan', data),

  // 方案列表 / 详情 / 保存 / 删除
  plans: () => http.get('/creator/plans'),
  getPlan: (id) => http.get(`/creator/plan/${id}`),
  savePlan: (id, data) => http.put(`/creator/plan/${id}`, data),
  deletePlan: (id) => http.delete(`/creator/plan/${id}`),

  // 临时方案（选题阶段写了一半的表单，随时还原）
  drafts: () => http.get('/creator/drafts'),
  saveDraft: (data) => http.post('/creator/drafts', data),
  deleteDraft: (id) => http.delete(`/creator/draft/${id}`),

  // 出片
  render: (id, shots) => http.post(`/creator/plan/${id}/render`, shots ? { shots } : {}),
  task: (id) => http.get(`/creator/task/${id}`),
  tasks: () => http.get('/creator/tasks'),

  // 导出：kind = teleprompter | shotlist | srt
  exportText: (id, kind) => http.get(`/creator/plan/${id}/export?kind=${kind}`),

  // 视频回放地址（支持 Range，前端 video 可直接拖进度）
  videoUrl: (planId, filename) => {
    const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5409'
    return `${base}/creator/video/${encodeURIComponent(planId)}/${encodeURIComponent(filename)}`
  }
}
