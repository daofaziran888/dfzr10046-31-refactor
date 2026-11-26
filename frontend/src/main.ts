import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import { workbenchBoundary } from './domain/workbench-boundary'
import { getWorkbenchController } from './domain/workbench-controller'
import './styles/global.css'

const app = createApp(App)
app.use(router)
app.mount('#app')

// 开发服务器/浏览器自动化可直接检查在途请求与回执边界；模块切换不会释放它们。
if (import.meta.env.DEV) {
  Object.assign(window, {
    __workbenchBoundary: workbenchBoundary,
    __getWorkbenchController: getWorkbenchController,
  })
}
