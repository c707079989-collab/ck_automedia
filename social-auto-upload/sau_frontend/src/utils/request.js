import axios from 'axios'
import { ElMessage } from 'element-plus'

// 创建axios实例
const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:5409',
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    // 可以在这里添加token等认证信息
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    const { data } = response
    
    // 根据后端接口规范处理响应
    if (data.code === 200 || data.success) {
      return data
    } else {
      ElMessage.error(data.msg || data.message || '请求失败')
      return Promise.reject(new Error(data.msg || data.message || '请求失败'))
    }
  },
  (error) => {
    console.error('响应错误:', error)

    // 后端把业务失败放在 HTTP 4xx/5xx 的 JSON body 里（如 /postVideo 的 cookie 失效）。
    // 优先展示后端原文，避免界面只出现 axios 的 "Request failed with status code 500"。
    const serverMsg = error.response?.data?.msg || error.response?.data?.message || ''
    let msg
    if (error.response) {
      const { status } = error.response
      switch (status) {
        case 401:
          msg = '未授权，请重新登录'
          break
        case 403:
          msg = '拒绝访问'
          break
        case 404:
          msg = '请求地址不存在'
          break
        default:
          msg = `服务器内部错误 (HTTP ${status})`
      }
    } else {
      msg = '网络连接失败'
    }
    if (serverMsg) msg = serverMsg

    // 登录态问题给出可操作的下一步，而不是让人对着 500 猜
    if (/cookie.*(失效|不存在)|请先完成.*登录/.test(msg)) {
      msg = `${msg} —— 请到「账号管理」重新扫码登录该账号，再回来发布`
    }

    ElMessage.error(msg)
    error.message = msg // 发布页 catch 里读的是 error.message
    return Promise.reject(error)
  }
)

// 封装常用的请求方法
export const http = {
  get(url, params) {
    return request.get(url, { params })
  },
  
  post(url, data, config = {}) {
    return request.post(url, data, config)
  },
  
  put(url, data, config = {}) {
    return request.put(url, data, config)
  },
  
  delete(url, params) {
    return request.delete(url, { params })
  },
  
  upload(url, formData, onUploadProgress) {
    return request.post(url, formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      },
      onUploadProgress
    })
  }
}

export default request