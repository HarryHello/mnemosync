import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import App from './App.vue'
import router from './router'
import { getErrorMessage } from '@/utils/error'
import '@/scss/global.scss'

const app = createApp(App)

for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component as never)
}

// 全局错误处理器: 捕获未处理的渲染/事件错误
app.config.errorHandler = (err, _instance, info) => {
  console.error('[Vue Error]', err, info)
  ElMessage.error(`应用错误: ${getErrorMessage(err)}`)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
